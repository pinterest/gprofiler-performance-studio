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
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/awserr"
	"github.com/aws/aws-sdk-go/aws/session"
	"github.com/aws/aws-sdk-go/service/s3"
)

// Trace reports: periodically render trace <-> profile HTML reports (trace_reports/trace_flame.py)
// and publish them to S3 under <S3_PATH_PREFIX>/<TRACE_REPORTS_S3_PREFIX>/<report id>/<stamp>.html,
// with an index.json the webapp lists. Disabled unless TRACE_REPORTS_CONFIG is set.

const (
	traceReportsIndexFile = "index.json"
	// Arbitrary constant shared by all indexer replicas so only one of them renders per interval.
	traceReportsLockKey = 728_314_001
	traceReportStampFmt = "20060102T1504Z"
)

var traceReportID = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)

type TraceReportEntry struct {
	ID          string          `json:"id"`
	Label       string          `json:"label"`
	GeneratedAt string          `json:"generated_at"`
	Path        string          `json:"path"`
	TraceID     string          `json:"trace_id,omitempty"`
	Root        string          `json:"root,omitempty"`
	WallMs      int64           `json:"wall_ms,omitempty"`
	NSpans      int64           `json:"n_spans,omitempty"`
	TraceStart  string          `json:"trace_start,omitempty"`
	TraceEnd    string          `json:"trace_end,omitempty"`
	Platform    string          `json:"platform,omitempty"`
	Device      string          `json:"device,omitempty"`
	Flow        string          `json:"flow,omitempty"`
	Stats       json.RawMessage `json:"stats,omitempty"`
	Clusters    json.RawMessage `json:"clusters,omitempty"`
}

type TraceReportError struct {
	ID    string `json:"id"`
	Error string `json:"error"`
}

type TraceReportsIndex struct {
	UpdatedAt string             `json:"updated_at"`
	Reports   []TraceReportEntry `json:"reports"`
	Errors    []TraceReportError `json:"errors,omitempty"`
}

type traceReportsManifest struct {
	GeneratedAt string `json:"generated_at"`
	Reports     []struct {
		TraceReportEntry
		File  string `json:"file"`
		Error string `json:"error"`
	} `json:"reports"`
}

type TraceReporter struct {
	args      *CLIArgs
	sess      *session.Session
	keyPrefix string
	now       func() time.Time
}

func NewTraceReporter(args *CLIArgs) *TraceReporter {
	sessionOptions := session.Options{SharedConfigState: session.SharedConfigEnable}
	if args.AWSEndpoint != "" {
		sessionOptions.Config = aws.Config{
			Region:           aws.String(args.AWSRegion),
			Endpoint:         aws.String(args.AWSEndpoint),
			S3ForcePathStyle: aws.Bool(true),
		}
	}
	return &TraceReporter{
		args:      args,
		sess:      session.Must(session.NewSessionWithOptions(sessionOptions)),
		keyPrefix: s3KeyPath(args.S3PathPrefix, args.TraceReportsS3Prefix),
		now:       time.Now,
	}
}

func (tr *TraceReporter) key(rel string) string {
	return tr.keyPrefix + "/" + rel
}

// Run renders reports every TraceReportsInterval until ctx is cancelled. After a restart it
// waits out the remainder of the interval measured from the last published index.
func (tr *TraceReporter) Run(ctx context.Context, wg *sync.WaitGroup) {
	defer wg.Done()
	interval := tr.args.TraceReportsInterval
	logger.Infof("trace reports enabled: every %s, config %s, s3 %s/", interval, tr.args.TraceReportsConfig, tr.keyPrefix)
	wait := time.Minute
	if idx, err := tr.loadIndex(); err == nil {
		if last, err := time.Parse(time.RFC3339, idx.UpdatedAt); err == nil {
			wait = nextRunDelay(last, tr.now(), interval)
		}
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
		start := tr.now()
		if err := tr.runLocked(ctx); err != nil {
			logger.Errorf("trace reports run failed: %v", err)
		}
		wait = interval - tr.now().Sub(start)
		if wait < time.Minute {
			wait = time.Minute
		}
	}
}

// nextRunDelay is how long to wait before the next run given when the last one published.
func nextRunDelay(last, now time.Time, interval time.Duration) time.Duration {
	d := last.Add(interval).Sub(now)
	if d < time.Minute {
		return time.Minute
	}
	if d > interval {
		return interval
	}
	return d
}

func (tr *TraceReporter) runLocked(ctx context.Context) error {
	if db == nil {
		return tr.runOnce(ctx)
	}
	conn, err := db.Conn(ctx)
	if err != nil {
		return fmt.Errorf("postgres connection for advisory lock: %w", err)
	}
	defer conn.Close()
	var locked bool
	if err := conn.QueryRowContext(ctx, "SELECT pg_try_advisory_lock($1)", traceReportsLockKey).Scan(&locked); err != nil {
		return fmt.Errorf("advisory lock: %w", err)
	}
	if !locked {
		logger.Infof("trace reports: another indexer holds the lock, skipping this interval")
		return nil
	}
	defer func() {
		if _, err := conn.ExecContext(context.Background(), "SELECT pg_advisory_unlock($1)", traceReportsLockKey); err != nil {
			logger.Warnf("trace reports: advisory unlock: %v", err)
		}
	}()
	// Another replica may have published while this one waited for the lock.
	if idx, err := tr.loadIndex(); err == nil {
		if last, err := time.Parse(time.RFC3339, idx.UpdatedAt); err == nil &&
			tr.now().Sub(last) < tr.args.TraceReportsInterval/2 {
			logger.Infof("trace reports: published %s ago by another indexer, skipping", tr.now().Sub(last).Round(time.Second))
			return nil
		}
	}
	return tr.runOnce(ctx)
}

func (tr *TraceReporter) runOnce(ctx context.Context) error {
	outDir, err := os.MkdirTemp("", "trace_reports_")
	if err != nil {
		return err
	}
	defer os.RemoveAll(outDir)

	runCtx, cancel := context.WithTimeout(ctx, tr.args.TraceReportsTimeout)
	defer cancel()
	cmd := exec.CommandContext(runCtx, tr.args.TraceReportsPython, tr.args.TraceReportsScript,
		"--config", tr.args.TraceReportsConfig, "--batch", "--out-dir", outDir)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	started := tr.now()
	if err := cmd.Run(); err != nil {
		logger.Errorf("trace reports generator output:\n%s", tail(stderr.String(), 4000))
		return fmt.Errorf("generator: %w", err)
	}
	logger.Debugf("trace reports generator output:\n%s", tail(stderr.String(), 4000))

	raw, err := os.ReadFile(filepath.Join(outDir, "manifest.json"))
	if err != nil {
		return fmt.Errorf("manifest: %w", err)
	}
	var manifest traceReportsManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return fmt.Errorf("manifest: %w", err)
	}

	stamp := started.UTC().Format(traceReportStampFmt)
	var fresh []TraceReportEntry
	var failures []TraceReportError
	for _, r := range manifest.Reports {
		if !traceReportID.MatchString(r.ID) {
			logger.Warnf("trace reports: skipping report with invalid id %q", r.ID)
			continue
		}
		if r.File == "" {
			failures = append(failures, TraceReportError{ID: r.ID, Error: r.Error})
			continue
		}
		html, err := os.ReadFile(filepath.Join(outDir, filepath.Base(r.File)))
		if err != nil {
			failures = append(failures, TraceReportError{ID: r.ID, Error: err.Error()})
			continue
		}
		entry := r.TraceReportEntry
		entry.Path = r.ID + "/" + stamp + ".html"
		entry.GeneratedAt = started.UTC().Format(time.RFC3339)
		if err := tr.putHTML(tr.key(entry.Path), html); err != nil {
			failures = append(failures, TraceReportError{ID: r.ID, Error: "upload failed"})
			logger.Errorf("trace reports: upload %s: %v", entry.Path, err)
			continue
		}
		fresh = append(fresh, entry)
	}

	idx, err := tr.loadIndex()
	if err != nil {
		idx = &TraceReportsIndex{}
	}
	pruned := mergeTraceReports(idx, fresh, tr.now(), tr.args.TraceReportsRetentionDays)
	idx.Errors = failures
	idx.UpdatedAt = tr.now().UTC().Format(time.RFC3339)
	if err := tr.saveIndex(idx); err != nil {
		return fmt.Errorf("index: %w", err)
	}
	for _, p := range pruned {
		if err := tr.delete(tr.key(p)); err != nil {
			logger.Warnf("trace reports: delete %s: %v", p, err)
		}
	}
	logger.Infof("trace reports: published %d, failed %d, pruned %d", len(fresh), len(failures), len(pruned))
	return nil
}

// mergeTraceReports adds fresh entries to the index (newest first) and drops entries older than
// retentionDays, returning the dropped paths so their objects can be deleted.
func mergeTraceReports(idx *TraceReportsIndex, fresh []TraceReportEntry, now time.Time, retentionDays int) []string {
	cutoff := now.Add(-time.Duration(retentionDays) * 24 * time.Hour)
	all := append(append([]TraceReportEntry{}, fresh...), idx.Reports...)
	seen := map[string]bool{}
	kept := all[:0]
	var pruned []string
	for _, e := range all {
		if seen[e.Path] {
			continue
		}
		seen[e.Path] = true
		t, err := time.Parse(time.RFC3339, e.GeneratedAt)
		if err != nil || t.Before(cutoff) {
			pruned = append(pruned, e.Path)
			continue
		}
		kept = append(kept, e)
	}
	sort.SliceStable(kept, func(i, j int) bool { return kept[i].GeneratedAt > kept[j].GeneratedAt })
	idx.Reports = kept
	return pruned
}

func (tr *TraceReporter) loadIndex() (*TraceReportsIndex, error) {
	out, err := s3.New(tr.sess).GetObject(&s3.GetObjectInput{
		Bucket: aws.String(tr.args.S3Bucket),
		Key:    aws.String(tr.key(traceReportsIndexFile)),
	})
	if err != nil {
		var aerr awserr.Error
		if errors.As(err, &aerr) && aerr.Code() == s3.ErrCodeNoSuchKey {
			return &TraceReportsIndex{}, nil
		}
		return nil, err
	}
	defer out.Body.Close()
	var idx TraceReportsIndex
	if err := json.NewDecoder(out.Body).Decode(&idx); err != nil {
		return nil, err
	}
	return &idx, nil
}

func (tr *TraceReporter) saveIndex(idx *TraceReportsIndex) error {
	body, err := json.Marshal(idx)
	if err != nil {
		return err
	}
	_, err = s3.New(tr.sess).PutObject(&s3.PutObjectInput{
		Bucket:       aws.String(tr.args.S3Bucket),
		Key:          aws.String(tr.key(traceReportsIndexFile)),
		Body:         bytes.NewReader(body),
		ContentType:  aws.String("application/json"),
		CacheControl: aws.String("no-cache"),
	})
	return err
}

func (tr *TraceReporter) putHTML(key string, html []byte) error {
	var gz bytes.Buffer
	w := gzip.NewWriter(&gz)
	if _, err := w.Write(html); err != nil {
		return err
	}
	if err := w.Close(); err != nil {
		return err
	}
	_, err := s3.New(tr.sess).PutObject(&s3.PutObjectInput{
		Bucket:          aws.String(tr.args.S3Bucket),
		Key:             aws.String(key),
		Body:            bytes.NewReader(gz.Bytes()),
		ContentType:     aws.String("text/html; charset=utf-8"),
		ContentEncoding: aws.String("gzip"),
	})
	return err
}

func (tr *TraceReporter) delete(key string) error {
	_, err := s3.New(tr.sess).DeleteObject(&s3.DeleteObjectInput{
		Bucket: aws.String(tr.args.S3Bucket),
		Key:    aws.String(key),
	})
	return err
}

func tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return "..." + s[len(s)-n:]
}
