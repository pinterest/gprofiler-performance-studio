//
// Copyright (C) 2023 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

package main

import (
	"bufio"
	"errors"
	"net"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go/aws"
)

func TestParseSQSSystemAttributes(t *testing.T) {
	sentAt, receiveCount := parseSQSSystemAttributes(map[string]*string{
		"SentTimestamp":           aws.String("1760450000123"),
		"ApproximateReceiveCount": aws.String("3"),
	})
	if !sentAt.Equal(time.UnixMilli(1760450000123)) {
		t.Fatalf("wrong sentAt: %v", sentAt)
	}
	if receiveCount != 3 {
		t.Fatalf("wrong receiveCount: %d", receiveCount)
	}

	for name, attributes := range map[string]map[string]*string{
		"nil":       nil,
		"empty":     {},
		"malformed": {"SentTimestamp": aws.String("yesterday"), "ApproximateReceiveCount": aws.String("x")},
	} {
		sentAt, receiveCount = parseSQSSystemAttributes(attributes)
		if !sentAt.IsZero() || receiveCount != 0 {
			t.Fatalf("%s: expected zero values, got %v and %d", name, sentAt, receiveCount)
		}
	}
}

func TestSinceOrZero(t *testing.T) {
	now := time.Now()
	if d := sinceOrZero(now.Add(-2*time.Second), now); d != 2*time.Second {
		t.Fatalf("expected 2s, got %s", d)
	}
	if d := sinceOrZero(time.Time{}, now); d != 0 {
		t.Fatalf("unknown start: expected 0, got %s", d)
	}
	if d := sinceOrZero(now.Add(time.Second), now); d != 0 {
		t.Fatalf("clock skew: expected 0, got %s", d)
	}
}

func TestRecordBufferTracksOldestRecord(t *testing.T) {
	var buf recordBuffer
	first := time.Now().Add(-10 * time.Second)
	buf.add(MetricRecord{HostName: "a"}, first)
	buf.add(MetricRecord{HostName: "b"}, first.Add(5*time.Second))
	if len(buf.records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(buf.records))
	}
	if !buf.oldestAt.Equal(first) {
		t.Fatalf("oldestAt should be the first record's time, got %v", buf.oldestAt)
	}

	buf.reset()
	if len(buf.records) != 0 || !buf.oldestAt.IsZero() {
		t.Fatalf("reset should empty the buffer, got %d records, oldestAt %v", len(buf.records), buf.oldestAt)
	}
	second := time.Now()
	buf.add(MetricRecord{HostName: "c"}, second)
	if !buf.oldestAt.Equal(second) {
		t.Fatalf("oldestAt should restart after reset, got %v", buf.oldestAt)
	}
}

func TestFlushEmptyBufferIsNoop(t *testing.T) {
	// A nil connection would panic if flush tried to insert.
	client := &ClickHouseClient{}
	var buf recordBuffer
	client.flush(&buf, "flamedb.samples", FlushReasonTimer)
}

func TestFormatMetricLine(t *testing.T) {
	line := formatMetricLine("gprofiler_indexer.flush.rows", 1760450000, 1000000, "gprofiler-indexer",
		map[string]string{"table": "flamedb.samples", "reason": "timer", "result": "ok"})
	expected := "put gprofiler_indexer.flush.rows 1760450000 1000000 " +
		"service=gprofiler-indexer reason=timer result=ok table=flamedb.samples"
	if line != expected {
		t.Fatalf("wrong line:\n got: %s\nwant: %s", line, expected)
	}

	line = formatMetricLine("m", 1, 12.5, "svc", nil)
	if line != "put m 1 12.5 service=svc" {
		t.Fatalf("wrong line: %s", line)
	}
}

// startMetricsListener accepts metric lines like the metrics agent does.
func startMetricsListener(t *testing.T) (*MetricsPublisher, <-chan string) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	lines := make(chan string, 100)
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			scanner := bufio.NewScanner(conn)
			for scanner.Scan() {
				lines <- scanner.Text()
			}
			conn.Close()
		}
	}()
	host, port, _ := net.SplitHostPort(listener.Addr().String())
	return &MetricsPublisher{host: host, port: port, serviceName: "gprofiler-indexer", enabled: true,
		errorLogInterval: 300}, lines
}

func receiveLines(t *testing.T, lines <-chan string, n int) []string {
	received := make([]string, 0, n)
	for i := 0; i < n; i++ {
		select {
		case line := <-lines:
			received = append(received, line)
		case <-time.After(2 * time.Second):
			t.Fatalf("expected %d metric lines, got %d: %v", n, len(received), received)
		}
	}
	return received
}

// metricLineWithoutTimestamp drops the timestamp field so lines can be compared.
func metricLineWithoutTimestamp(line string) string {
	fields := strings.Fields(line)
	return strings.Join(append(fields[:2:2], fields[3:]...), " ")
}

func TestSendFlushStats(t *testing.T) {
	publisher, lines := startMetricsListener(t)
	publisher.SendFlushStats(flushStats{
		Table:     "flamedb.samples",
		Reason:    FlushReasonTimer,
		Rows:      651,
		BufferAge: 29500 * time.Millisecond,
		Duration:  1250 * time.Microsecond,
		Err:       errors.New("insert failed"),
	})

	received := receiveLines(t, lines, 3)
	for i := range received {
		received[i] = metricLineWithoutTimestamp(received[i])
	}
	sort.Strings(received)
	tags := "service=gprofiler-indexer reason=timer result=error table=flamedb.samples"
	expected := []string{
		"put gprofiler_indexer.flush.buffer_age_ms 29500 " + tags,
		"put gprofiler_indexer.flush.duration_ms 1.25 " + tags,
		"put gprofiler_indexer.flush.rows 651 " + tags,
	}
	for i := range expected {
		if received[i] != expected[i] {
			t.Fatalf("wrong line:\n got: %s\nwant: %s", received[i], expected[i])
		}
	}
}

func TestSendMessageTimings(t *testing.T) {
	publisher, lines := startMetricsListener(t)
	publisher.SendMessageTimings(messageTimings{
		QueueWait: 3 * time.Second,
		Dispatch:  time.Millisecond,
		S3Fetch:   40 * time.Millisecond,
		Process:   700 * time.Millisecond,
		Delete:    10 * time.Millisecond,
		Total:     3751 * time.Millisecond,
	})

	received := receiveLines(t, lines, 6)
	byStage := map[string]string{}
	for _, line := range received {
		fields := strings.Fields(line)
		if fields[1] != MetricMessageStageMs {
			t.Fatalf("unexpected metric: %s", line)
		}
		byStage[strings.TrimPrefix(fields[5], "stage=")] = fields[3]
	}
	expected := map[string]string{"queue_wait": "3000", "dispatch": "1", "s3_fetch": "40",
		"process": "700", "delete": "10", "total": "3751"}
	for stage, value := range expected {
		if byStage[stage] != value {
			t.Fatalf("stage %s: got %q, want %q (all: %v)", stage, byStage[stage], value, byStage)
		}
	}
}

func TestLatencyMetricsDisabled(t *testing.T) {
	var nilPublisher *MetricsPublisher
	nilPublisher.SendFlushStats(flushStats{Rows: 1})
	nilPublisher.SendMessageTimings(messageTimings{})

	publisher, lines := startMetricsListener(t)
	publisher.enabled = false
	publisher.SendFlushStats(flushStats{Rows: 1})
	publisher.SendMessageTimings(messageTimings{})
	select {
	case line := <-lines:
		t.Fatalf("disabled publisher sent: %s", line)
	case <-time.After(200 * time.Millisecond):
	}
}
