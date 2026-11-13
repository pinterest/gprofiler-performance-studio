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
	"context"
	"crypto/tls"
	"fmt"
	"sync"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
)

type StackRecord struct {
	Timestamp          time.Time
	ServiceId          uint32
	InstanceType       string
	ContainerEnvName   string
	HostName           string
	ContainerName      string
	NumSamples         int
	CallStackHash      uint64
	HasParent          string
	Name               string
	Parent             uint64
	InsertionTimestamp time.Time
	ack                *messageAck
}

type MetricRecord struct {
	Timestamp                time.Time
	ServiceId                uint32
	InstanceType             string
	HostName                 string
	CPUAverageUsedPercent    float64
	MemoryAverageUsedPercent float64
	HTMLPath                 string
	ack                      *messageAck
}

type RecordsAttributesUnpack interface {
	getDbAttributes() []interface{}
}

func (mr MetricRecord) getDbAttributes() []interface{} {
	dbAttributes := []interface{}{
		mr.Timestamp,
		mr.ServiceId,
		mr.InstanceType,
		mr.HostName,
		mr.CPUAverageUsedPercent,
		mr.MemoryAverageUsedPercent,
		mr.HTMLPath,
	}
	return dbAttributes
}

func (sr StackRecord) getDbAttributes() []interface{} {
	dbAttributes := []interface{}{
		sr.Timestamp,
		sr.ServiceId,
		sr.InstanceType,
		sr.ContainerEnvName,
		sr.HostName,
		sr.ContainerName,
		uint32(sr.NumSamples),
		sr.CallStackHash,
		sr.Name,
		sr.Parent,
		sr.InsertionTimestamp,
		uint32(0),
	}
	return dbAttributes
}

type ClickHouseClient struct {
	conn clickhouse.Conn
}

type ClickHouseSettings struct {
	Addr                   string
	Username               string
	Password               string
	Database               string
	UseTLS                 bool
	ClickHouseStacksTable  string
	ClickHouseMetricsTable string
}

func NewClickHouseClient(settings *ClickHouseSettings) (*ClickHouseClient, error) {
	logger.Debugf("Connecting to ClickHouse: %s", settings.Addr)
	var tlsCfg *tls.Config
	if settings.UseTLS {
		tlsCfg = &tls.Config{}
	}
	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr: []string{settings.Addr},
		Auth: clickhouse.Auth{
			Database: settings.Database,
			Username: settings.Username,
			Password: settings.Password,
		},
		TLS:   tlsCfg,
		Debug: false,
		Debugf: func(format string, v ...interface{}) {
		},
		Settings: clickhouse.Settings{
			"max_execution_time": 60,
		},
		Compression: &clickhouse.Compression{
			Method: clickhouse.CompressionLZ4,
		},
		DialTimeout:          time.Second * 30,
		MaxOpenConns:         5,
		MaxIdleConns:         5,
		ConnMaxLifetime:      time.Duration(10) * time.Minute,
		BlockBufferSize:      10,
		MaxCompressionBuffer: 10240,
	})
	if err != nil {
		logger.Errorf("unable to connect to clickhouse: %v", err)
		return nil, err
	}
	return &ClickHouseClient{
		conn: conn,
	}, nil
}

func (c *ClickHouseClient) clickHouseWrite(records []RecordsAttributesUnpack, tableName string) error {
	if len(records) == 0 {
		return nil
	}
	ctx := context.Background()
	batch, err := c.conn.PrepareBatch(ctx, fmt.Sprintf("INSERT INTO %s", tableName))
	if err != nil {
		logger.Errorf("unable to prepare batch: %v", err)
		return err
	}

	for _, temp := range records {
		if err = batch.Append(temp.getDbAttributes()...); err != nil {
			logger.Errorf("unable to append record to batch: %v", err)
		}
	}
	if err = batch.Send(); err != nil {
		logger.Errorf("unable to send batch: %v", err)
		return err
	}
	logger.Debugf("successfully sent %d records to %s", len(records), tableName)
	return nil
}

var clickHouseInsertRetryBackoff = time.Second

// insertWithRetry tries insert up to attempts times, doubling the backoff
// between tries. It returns how many attempts it made.
func insertWithRetry(insert func() error, attempts int, backoff time.Duration) (int, error) {
	for attempt := 1; ; attempt++ {
		err := insert()
		if err == nil || attempt >= attempts {
			return attempt, err
		}
		logger.Warnf("insert attempt %d of %d failed, retrying in %s: %v", attempt, attempts, backoff, err)
		time.Sleep(backoff)
		backoff *= 2
	}
}

func (c *ClickHouseClient) flush(buf *recordBuffer, tableName string, reason string) {
	flushBuffer(buf, tableName, reason, c.clickHouseWrite)
}

// flushBuffer writes the buffer to ClickHouse, reports how long its rows waited
// and how long the insert took, lets its SQS messages be deleted if the insert
// succeeded, and empties the buffer.
func flushBuffer(buf *recordBuffer, tableName string, reason string,
	write func([]RecordsAttributesUnpack, string) error) {
	if len(buf.records) == 0 {
		return
	}
	start := time.Now()
	attempts, err := insertWithRetry(func() error { return write(buf.records, tableName) },
		ClickHouseInsertAttempts, clickHouseInsertRetryBackoff)
	stats := flushStats{
		Table:     tableName,
		Reason:    reason,
		Rows:      len(buf.records),
		BufferAge: sinceOrZero(buf.oldestAt, start),
		Duration:  time.Since(start),
		Attempts:  attempts,
		Err:       err,
	}
	logger.Infof("Flush %d records to %s: reason=%s result=%s buffer_age=%s duration=%s attempts=%d",
		stats.Rows, stats.Table, stats.Reason, stats.result(), stats.BufferAge, stats.Duration, stats.Attempts)
	GetMetricsPublisher().SendFlushStats(stats)
	if err == nil {
		buf.releaseAcks()
	} else {
		logger.Errorf("dropping %d records after %d failed inserts into %s; their SQS messages are not deleted and will be redelivered: %v",
			stats.Rows, attempts, tableName, err)
	}
	buf.reset()
}

func BufferedClickHouseWrite(args *CLIArgs, channels *RecordChannels, wg *sync.WaitGroup) {
	defer wg.Done()
	logger.Debug("BufferedClickHouseWrite started")
	settings := ClickHouseSettings{
		Addr:                   args.ClickHouseAddr,
		Database:               "flamedb",
		Username:               args.ClickHouseUser,
		Password:               args.ClickHousePassword,
		ClickHouseMetricsTable: args.ClickHouseMetricsTable,
		ClickHouseStacksTable:  args.ClickHouseStacksTable,
	}
	clickhouseClient, err := NewClickHouseClient(&settings)
	if err != nil {
		logger.Fatal(err)
	}
	stacksTicker := time.NewTicker(time.Second * ClickHouseStacksFlushTimeout)
	metricsTicker := time.NewTicker(time.Second * ClickHouseMetricsFlushTimeout)
	var buffRecords, buffMetricsRecords recordBuffer

	for {
		select {
		case stackRecord, ok := <-channels.StacksRecords:
			if ok {
				// InsertionTimestamp is set just before the record enters the channel,
				// so the buffer age includes any time spent waiting in the channel.
				buffRecords.add(stackRecord, stackRecord.InsertionTimestamp, stackRecord.ack)
				if len(buffRecords.records) >= args.ClickHouseStacksBatchSize {
					clickhouseClient.flush(&buffRecords, args.ClickHouseStacksTable, FlushReasonSize)
					stacksTicker.Reset(time.Second * ClickHouseStacksFlushTimeout)
				}
			} else {
				channels.StacksRecords = nil
			}
		case <-stacksTicker.C:
			clickhouseClient.flush(&buffRecords, args.ClickHouseStacksTable, FlushReasonTimer)
		case metricRecords, ok := <-channels.MetricsRecords:
			if ok {
				buffMetricsRecords.add(metricRecords, time.Now(), metricRecords.ack)
				if len(buffMetricsRecords.records) >= args.ClickHouseMetricsBatchSize {
					clickhouseClient.flush(&buffMetricsRecords, args.ClickHouseMetricsTable, FlushReasonSize)
					metricsTicker.Reset(time.Second * ClickHouseMetricsFlushTimeout)
				}
			} else {
				channels.MetricsRecords = nil
			}
		case <-metricsTicker.C:
			clickhouseClient.flush(&buffMetricsRecords, args.ClickHouseMetricsTable, FlushReasonTimer)
		}
		if channels.StacksRecords == nil {
			stacksTicker.Stop()
		}
		if channels.MetricsRecords == nil {
			metricsTicker.Stop()
		}
		if channels.StacksRecords == nil && channels.MetricsRecords == nil {
			break
		}
	}
	// flush buffer on exit
	clickhouseClient.flush(&buffRecords, args.ClickHouseStacksTable, FlushReasonShutdown)
	clickhouseClient.flush(&buffMetricsRecords, args.ClickHouseMetricsTable, FlushReasonShutdown)
	logger.Debug("BufferedClickHouseWrite finished")
}
