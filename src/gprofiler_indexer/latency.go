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
	"strconv"
	"time"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/service/sqs"
)

// Latency metric names
const (
	MetricMessageStageMs   = "gprofiler_indexer.message.stage_ms"
	MetricFlushRows        = "gprofiler_indexer.flush.rows"
	MetricFlushDurationMs  = "gprofiler_indexer.flush.duration_ms"
	MetricFlushBufferAgeMs = "gprofiler_indexer.flush.buffer_age_ms"
)

// Flush reasons
const (
	FlushReasonSize     = "size"
	FlushReasonTimer    = "timer"
	FlushReasonShutdown = "shutdown"
)

// messageTimings holds how long one SQS message spent in each stage, from
// the moment SQS accepted it until the indexer deleted it.
type messageTimings struct {
	QueueWait time.Duration // SQS SentTimestamp -> ReceiveMessage returned
	Dispatch  time.Duration // ReceiveMessage returned -> a worker picked it up
	S3Fetch   time.Duration
	Process   time.Duration // parse + hand-off to the writer + HTML/Postgres side effects
	Delete    time.Duration
	Total     time.Duration // SQS SentTimestamp -> delete finished
}

func (mt messageTimings) stages() map[string]time.Duration {
	return map[string]time.Duration{
		"queue_wait": mt.QueueWait,
		"dispatch":   mt.Dispatch,
		"s3_fetch":   mt.S3Fetch,
		"process":    mt.Process,
		"delete":     mt.Delete,
		"total":      mt.Total,
	}
}

// sinceOrZero returns end - start, or 0 if start is unknown or after end
// (clock skew between SQS and the host).
func sinceOrZero(start, end time.Time) time.Duration {
	if start.IsZero() || end.Before(start) {
		return 0
	}
	return end.Sub(start)
}

func durationMs(d time.Duration) float64 {
	return float64(d.Microseconds()) / 1000
}

// parseSQSSystemAttributes extracts SentTimestamp (epoch milliseconds) and
// ApproximateReceiveCount. Missing or malformed values are returned as zero.
func parseSQSSystemAttributes(attributes map[string]*string) (sentAt time.Time, receiveCount int) {
	if raw := aws.StringValue(attributes[sqs.MessageSystemAttributeNameSentTimestamp]); raw != "" {
		if ms, err := strconv.ParseInt(raw, 10, 64); err == nil {
			sentAt = time.UnixMilli(ms)
		}
	}
	if raw := aws.StringValue(attributes[sqs.MessageSystemAttributeNameApproximateReceiveCount]); raw != "" {
		receiveCount, _ = strconv.Atoi(raw)
	}
	return sentAt, receiveCount
}

func (m *MetricsPublisher) SendMessageTimings(mt messageTimings) {
	if m == nil || !m.enabled {
		return
	}
	for stage, d := range mt.stages() {
		m.SendGaugeMetric(MetricMessageStageMs, durationMs(d), map[string]string{"stage": stage})
	}
}

// recordBuffer is the writer's in-memory batch for one table. It remembers
// when its oldest record was buffered, so a flush can report how long rows
// waited before the insert.
type recordBuffer struct {
	records  []RecordsAttributesUnpack
	oldestAt time.Time
}

func (b *recordBuffer) add(record RecordsAttributesUnpack, bufferedAt time.Time) {
	if len(b.records) == 0 {
		b.oldestAt = bufferedAt
	}
	b.records = append(b.records, record)
}

func (b *recordBuffer) reset() {
	b.records = make([]RecordsAttributesUnpack, 0)
	b.oldestAt = time.Time{}
}

type flushStats struct {
	Table     string
	Reason    string
	Rows      int
	BufferAge time.Duration // flush start - oldest record buffered
	Duration  time.Duration // insert duration
	Err       error
}

func (fs flushStats) result() string {
	if fs.Err != nil {
		return "error"
	}
	return "ok"
}

func (m *MetricsPublisher) SendFlushStats(fs flushStats) {
	if m == nil || !m.enabled {
		return
	}
	tags := map[string]string{"table": fs.Table, "reason": fs.Reason, "result": fs.result()}
	m.SendGaugeMetric(MetricFlushRows, float64(fs.Rows), tags)
	m.SendGaugeMetric(MetricFlushDurationMs, durationMs(fs.Duration), tags)
	m.SendGaugeMetric(MetricFlushBufferAgeMs, durationMs(fs.BufferAge), tags)
}
