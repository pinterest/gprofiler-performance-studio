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
	"errors"
	"testing"
	"time"
)

func countingAck() (*messageAck, *int) {
	done := 0
	return newMessageAck(func() { done++ }), &done
}

func noRetryBackoff(t *testing.T) {
	saved := clickHouseInsertRetryBackoff
	clickHouseInsertRetryBackoff = 0
	t.Cleanup(func() { clickHouseInsertRetryBackoff = saved })
}

func okWrite([]RecordsAttributesUnpack, string) error { return nil }

func failingWrite([]RecordsAttributesUnpack, string) error { return errors.New("clickhouse down") }

func TestMessageAckDoneOnlyAfterWorkerAndRows(t *testing.T) {
	ack, done := countingAck()
	ack.hold()
	ack.hold()
	ack.release(2)
	if *done != 0 {
		t.Fatal("done before the worker released its hold")
	}
	ack.release(1)
	if *done != 1 {
		t.Fatalf("expected done once, got %d", *done)
	}
}

func TestMessageAckWithNoRows(t *testing.T) {
	ack, done := countingAck()
	ack.release(1)
	if *done != 1 {
		t.Fatalf("a message with no rows should be done when the worker releases it, got %d", *done)
	}
}

func TestNilMessageAckIsNoop(t *testing.T) {
	var ack *messageAck
	ack.hold()
	ack.release(1)
}

func TestRecordBufferGroupsRowsByMessage(t *testing.T) {
	a, _ := countingAck()
	b, _ := countingAck()
	var buf recordBuffer
	now := time.Now()
	buf.add(StackRecord{}, now, a)
	buf.add(StackRecord{}, now, a)
	buf.add(StackRecord{}, now, b)
	buf.add(StackRecord{}, now, nil)
	if len(buf.acks) != 2 || buf.acks[0] != (ackRun{a, 2}) || buf.acks[1] != (ackRun{b, 1}) {
		t.Fatalf("unexpected runs: %+v", buf.acks)
	}
	buf.reset()
	if buf.acks != nil {
		t.Fatal("reset should drop the runs")
	}
}

// addRows hands off rows for a message the way writeStacks does.
func addRows(buf *recordBuffer, ack *messageAck, rows int) {
	for i := 0; i < rows; i++ {
		ack.hold()
		buf.add(StackRecord{}, time.Now(), ack)
	}
}

func TestFlushReleasesMessagesAfterInsert(t *testing.T) {
	ack, done := countingAck()
	var buf recordBuffer
	addRows(&buf, ack, 3)
	ack.release(1)

	flushBuffer(&buf, "flamedb.samples", FlushReasonSize, okWrite)
	if *done != 1 {
		t.Fatalf("message should be done after its rows are inserted, got %d", *done)
	}
	if len(buf.records) != 0 {
		t.Fatal("buffer should be empty after the flush")
	}
}

func TestMessageSplitAcrossBatches(t *testing.T) {
	ack, done := countingAck()
	var first, second recordBuffer
	addRows(&first, ack, 2)
	addRows(&second, ack, 2)
	ack.release(1)

	flushBuffer(&first, "flamedb.samples", FlushReasonSize, okWrite)
	if *done != 0 {
		t.Fatal("message done before its second batch was inserted")
	}
	flushBuffer(&second, "flamedb.samples", FlushReasonTimer, okWrite)
	if *done != 1 {
		t.Fatalf("message should be done after both batches, got %d", *done)
	}
}

func TestFailedFlushKeepsMessages(t *testing.T) {
	noRetryBackoff(t)
	ack, done := countingAck()
	var buf recordBuffer
	addRows(&buf, ack, 2)
	ack.release(1)

	flushBuffer(&buf, "flamedb.samples", FlushReasonSize, failingWrite)
	if *done != 0 {
		t.Fatal("a message whose rows failed to insert must not be deleted")
	}
	if len(buf.records) != 0 {
		t.Fatal("buffer should be emptied even when the insert fails")
	}
}

func TestInsertWithRetry(t *testing.T) {
	calls := 0
	attempts, err := insertWithRetry(func() error {
		calls++
		if calls < 2 {
			return errors.New("transient")
		}
		return nil
	}, 3, 0)
	if err != nil || attempts != 2 || calls != 2 {
		t.Fatalf("expected success on attempt 2, got attempts=%d calls=%d err=%v", attempts, calls, err)
	}

	calls = 0
	attempts, err = insertWithRetry(func() error {
		calls++
		return errors.New("down")
	}, 3, 0)
	if err == nil || attempts != 3 || calls != 3 {
		t.Fatalf("expected 3 failed attempts, got attempts=%d calls=%d err=%v", attempts, calls, err)
	}
}
