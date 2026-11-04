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
	"testing"
	"time"
)

func TestFlushTimeoutDefaults(t *testing.T) {
	args := NewCliArgs()
	if args.stacksFlushInterval() != 30*time.Second {
		t.Fatalf("stacks flush interval should default to 30s, got %s", args.stacksFlushInterval())
	}
	if args.metricsFlushInterval() != 30*time.Second {
		t.Fatalf("metrics flush interval should default to 30s, got %s", args.metricsFlushInterval())
	}
	if err := args.validateFlushTimeouts(); err != nil {
		t.Fatalf("defaults should be valid: %v", err)
	}
}

func TestFlushTimeoutConfigured(t *testing.T) {
	args := NewCliArgs()
	args.ClickHouseStacksFlushTimeout = 10
	args.ClickHouseMetricsFlushTimeout = 1
	if err := args.validateFlushTimeouts(); err != nil {
		t.Fatalf("10s and 1s should be valid: %v", err)
	}
	if args.stacksFlushInterval() != 10*time.Second {
		t.Fatalf("expected 10s, got %s", args.stacksFlushInterval())
	}
	if args.metricsFlushInterval() != time.Second {
		t.Fatalf("expected 1s, got %s", args.metricsFlushInterval())
	}
}

func TestFlushTimeoutRejectsNonPositive(t *testing.T) {
	for _, value := range []int{0, -5} {
		args := NewCliArgs()
		args.ClickHouseStacksFlushTimeout = value
		if args.validateFlushTimeouts() == nil {
			t.Fatalf("stacks flush timeout %d should be rejected", value)
		}

		args = NewCliArgs()
		args.ClickHouseMetricsFlushTimeout = value
		if args.validateFlushTimeouts() == nil {
			t.Fatalf("metrics flush timeout %d should be rejected", value)
		}
	}
}
