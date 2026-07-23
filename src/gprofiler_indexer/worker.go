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
	"fmt"
	"io/ioutil"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/session"
	log "github.com/sirupsen/logrus"
)

// deleteMessageWithMetrics handles SQS message deletion and SLI metric tracking for failures
func deleteMessageWithMetrics(sess *session.Session, task SQSMessage) {
	errDelete := deleteMessage(sess, task.QueueURL, task.MessageHandle)
	if errDelete != nil {
		log.Errorf("Unable to delete message from %s, err %v", task.QueueURL, errDelete)

		// SLI Metric: SQS delete failure (server error - counts against SLO)
		// The event was processed but we couldn't clean up
		// SendSLIMetric handles nil/enabled checks internally
		GetMetricsPublisher().SendSLIMetric(
			ResponseTypeFailure,
			"event_processing",
			map[string]string{
				"service":  task.Service,
				"error":    "sqs_delete_failed",
				"filename": task.Filename,
			},
		)
	}
}

func Worker(workerIdx int, args *CLIArgs, tasks <-chan SQSMessage, pw *ProfilesWriter, wg *sync.WaitGroup) {
	var buf []byte
	var err error
	var temp string

	defer wg.Done()

	sessionOptions := session.Options{
		SharedConfigState: session.SharedConfigEnable,
	}
	if args.AWSEndpoint != "" {
		sessionOptions.Config = aws.Config{
			Region:           aws.String(args.AWSRegion),
			Endpoint:         aws.String(args.AWSEndpoint),
			S3ForcePathStyle: aws.Bool(true),
		}
	}
	sess := session.Must(session.NewSessionWithOptions(sessionOptions))

	for task := range tasks {
		useSQS := task.Service != ""
		serviceName := task.Service
		startProcessing := time.Now()
		log.Debugf("got new file %s from service %s (ID: %d)", task.Filename, serviceName, task.ServiceId)
		
		// Calculate queue pickup delay if SentTimestamp is available
		if useSQS && task.SentTimestamp > 0 {
			sentTime := time.Unix(0, task.SentTimestamp*int64(time.Millisecond))
			queueDelayMs := time.Since(sentTime).Milliseconds()
			log.Infof("📨 Received SQS message: service=%s file=%s queue_delay_ms=%d", serviceName, task.Filename, queueDelayMs)
		} else {
			log.Infof("📨 Received SQS message: service=%s file=%s", serviceName, task.Filename)
		}

		if useSQS {
			fullPath := fmt.Sprintf("products/%s/stacks/%s", task.Service, task.Filename)
			buf, err = GetFileFromS3(sess, args.S3Bucket, fullPath)
			if err != nil {
				log.Errorf("Error while fetching file from S3: %v", err)
				// SLI Metric: S3 fetch failure (server error - counts against SLO)
				// Only tracks SQS events; SendSLIMetric handles nil/enabled checks internally
				GetMetricsPublisher().SendSLIMetric(
					ResponseTypeFailure,
					"event_processing",
					map[string]string{
						"service":  serviceName,
						"error":    "s3_fetch_failed",
						"filename": task.Filename,
					},
				)

				// Delete message from SQS after unsuccessful S3 fetch
				deleteMessageWithMetrics(sess, task)
				continue
			}
			log.Infof("✅ S3 fetch complete: service=%s file=%s size=%d bytes fetch_time_ms=%d", 
				serviceName, task.Filename, len(buf), time.Since(startProcessing).Milliseconds())
			temp = strings.Split(task.Filename, "_")[0]
		} else {
			buf, _ = ioutil.ReadFile(task.Filename)
			tokens := strings.Split(filepath.Base(task.Filename), "_")
			if len(tokens) > 2 {
				temp = strings.Join(tokens[:3], ":")
			}
		}

		layout := ISODateTimeFormat
		timestamp, tsErr := time.Parse(layout, temp)
		log.Debugf("parsed timestamp is: %v", timestamp)
		if tsErr != nil {
			log.Infof("⚠️  Timestamp parse failed, using fallback: filename=%s parsed_string='%s' error=%v", 
				task.Filename, temp, tsErr)
			timestamp = time.Now().UTC()
		} else {
			ageSeconds := time.Since(timestamp).Seconds()
			log.Infof("📅 Profile timestamp: %s (age: %.1f seconds)", timestamp.Format(time.RFC3339), ageSeconds)
		}

		// Parse stack frame file and write to ClickHouse
		parseStart := time.Now()
		err := pw.ParseStackFrameFile(sess, task, args.S3Bucket, timestamp, buf)
		if err != nil {
			log.Errorf("Error while parsing stack frame file: %v", err)

			// SLI Metric: Parse event failure or write profile to column DB failure (server error - counts against SLO)
			// Only tracks SQS events; SendSLIMetric handles nil/enabled checks internally
			if useSQS {
				GetMetricsPublisher().SendSLIMetric(
					ResponseTypeFailure,
					"event_processing",
					map[string]string{
						"service":  serviceName,
						"error":    "parse_or_write_failed",
						"filename": task.Filename,
					},
				)

				// Delete message from SQS after unsuccessful parse/write into column DB
				deleteMessageWithMetrics(sess, task)
			}
			continue
		}

		// Delete message from SQS after successful processing
		if useSQS {
			totalProcessingTime := time.Since(startProcessing).Milliseconds()
			endToEndLatency := time.Since(timestamp).Seconds()
			
			log.Infof("✅ Processing complete: service=%s file=%s parse_time_ms=%d total_processing_ms=%d end_to_end_latency_sec=%.1f", 
				serviceName, task.Filename, time.Since(parseStart).Milliseconds(), totalProcessingTime, endToEndLatency)
			
			deleteMessageWithMetrics(sess, task)

			// SLI Metric: Success! Event processed completely
			// SendSLIMetric handles nil/enabled checks internally
			GetMetricsPublisher().SendSLIMetric(
				ResponseTypeSuccess,
				"event_processing",
				map[string]string{
					"service":  serviceName,
					"filename": task.Filename,
				},
			)
		}
	}
	log.Debugf("Worker %d finished", workerIdx)
}
