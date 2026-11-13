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
	"sync"
	"sync/atomic"
	"time"

	"github.com/aws/aws-sdk-go/aws/session"
	log "github.com/sirupsen/logrus"
)

// messageAck counts the rows of one SQS message that aren't in ClickHouse yet,
// plus one hold for the worker while it's still handing rows to the writer.
// onDone runs once, when the count reaches zero.
type messageAck struct {
	pending atomic.Int64
	onDone  func()
}

func newMessageAck(onDone func()) *messageAck {
	a := &messageAck{onDone: onDone}
	a.pending.Store(1)
	return a
}

func (a *messageAck) hold() {
	if a != nil {
		a.pending.Add(1)
	}
}

func (a *messageAck) release(n int64) {
	if a != nil && a.pending.Add(-n) == 0 {
		a.onDone()
	}
}

// pendingMessage is a processed SQS message waiting for its rows to be inserted.
type pendingMessage struct {
	task        SQSMessage
	timings     messageTimings
	handedOffAt time.Time
	insertedAt  time.Time
}

// messageDeleter deletes SQS messages whose rows are all in ClickHouse, off the
// writer's goroutine, so SQS calls don't hold up the next batch.
type messageDeleter struct {
	sess    *session.Session
	pending chan *pendingMessage
	wg      sync.WaitGroup
}

func newMessageDeleter(sess *session.Session, workers int, queueSize int) *messageDeleter {
	d := &messageDeleter{sess: sess, pending: make(chan *pendingMessage, queueSize)}
	for i := 0; i < workers; i++ {
		d.wg.Add(1)
		go d.run()
	}
	return d
}

func (d *messageDeleter) enqueue(m *pendingMessage) {
	m.insertedAt = time.Now()
	d.pending <- m
}

// close waits for the queued deletes. Call it after the writer's final flush.
func (d *messageDeleter) close() {
	close(d.pending)
	d.wg.Wait()
}

func (d *messageDeleter) run() {
	defer d.wg.Done()
	for m := range d.pending {
		m.timings.InsertWait = sinceOrZero(m.handedOffAt, m.insertedAt)
		deleteStart := time.Now()
		deleteMessageWithMetrics(d.sess, m.task)
		deletedAt := time.Now()
		m.timings.Delete = deletedAt.Sub(deleteStart)
		m.timings.Total = sinceOrZero(m.task.SentAt, deletedAt)

		log.Infof("processed %s from service %s: queue_wait=%s dispatch=%s s3_fetch=%s process=%s insert_wait=%s delete=%s total=%s receive_count=%d",
			m.task.Filename, m.task.Service, m.timings.QueueWait, m.timings.Dispatch, m.timings.S3Fetch,
			m.timings.Process, m.timings.InsertWait, m.timings.Delete, m.timings.Total, m.task.ReceiveCount)
		GetMetricsPublisher().SendMessageTimings(m.timings)

		// SLI Metric: Success! Event processed completely, its rows are in ClickHouse
		GetMetricsPublisher().SendSLIMetric(
			ResponseTypeSuccess,
			"event_processing",
			map[string]string{
				"service":  m.task.Service,
				"filename": m.task.Filename,
			},
		)
	}
}
