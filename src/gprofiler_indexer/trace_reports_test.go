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
	"reflect"
	"testing"
	"time"
)

func TestMergeTraceReportsPrunesAndOrders(t *testing.T) {
	now := time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)
	ts := func(d time.Duration) string { return now.Add(-d).Format(time.RFC3339) }
	idx := &TraceReportsIndex{Reports: []TraceReportEntry{
		{ID: "a", Path: "a/old.html", GeneratedAt: ts(15 * 24 * time.Hour)},
		{ID: "a", Path: "a/mid.html", GeneratedAt: ts(2 * time.Hour)},
		{ID: "b", Path: "b/bad.html", GeneratedAt: "not a time"},
		{ID: "b", Path: "b/new.html", GeneratedAt: ts(time.Hour)},
	}}
	fresh := []TraceReportEntry{
		{ID: "a", Path: "a/new.html", GeneratedAt: ts(0)},
		{ID: "b", Path: "b/new.html", GeneratedAt: ts(time.Hour)},
	}

	pruned := mergeTraceReports(idx, fresh, now, 14)

	var paths []string
	for _, e := range idx.Reports {
		paths = append(paths, e.Path)
	}
	if want := []string{"a/new.html", "b/new.html", "a/mid.html"}; !reflect.DeepEqual(paths, want) {
		t.Errorf("kept %v, want %v", paths, want)
	}
	if want := []string{"a/old.html", "b/bad.html"}; !reflect.DeepEqual(pruned, want) {
		t.Errorf("pruned %v, want %v", pruned, want)
	}
}

func TestNextRunDelay(t *testing.T) {
	now := time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		last time.Time
		want time.Duration
	}{
		{now.Add(-20 * time.Minute), 40 * time.Minute},
		{now.Add(-3 * time.Hour), time.Minute},
		{now.Add(2 * time.Hour), time.Hour}, // clock skew: never wait longer than one interval
	} {
		if got := nextRunDelay(tc.last, now, time.Hour); got != tc.want {
			t.Errorf("nextRunDelay(last=%s) = %s, want %s", tc.last, got, tc.want)
		}
	}
}

func TestTraceReportID(t *testing.T) {
	for id, ok := range map[string]bool{
		"web-home.render_1": true, "": false, "../x": false, "a/b": false, "Upper": false, "-lead": false,
	} {
		if traceReportID.MatchString(id) != ok {
			t.Errorf("traceReportID(%q) = %v, want %v", id, !ok, ok)
		}
	}
}
