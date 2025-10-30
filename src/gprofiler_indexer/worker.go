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
		if serviceName == "" {
			serviceName = "local-file"
		}
		
		log.Debugf("got new file %s from service %s (ID: %d)", task.Filename, serviceName, task.ServiceId)
		
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
				
				// Clean up the message even though processing failed
				errDelete := deleteMessage(sess, task.QueueURL, task.MessageHandle)
				if errDelete != nil {
					log.Errorf("Unable to delete message from %s, err %v", task.QueueURL, errDelete)
				}
				continue
			}
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
			log.Debugf("Unable to fetch timestamp from filename %s, fallback to the current time", temp)
			timestamp = time.Now().UTC()
		}
		
		// Parse stack frame file and write to ClickHouse
		err := pw.ParseStackFrameFile(sess, task, args.S3Bucket, timestamp, buf)
		if err != nil {
			log.Errorf("Error while parsing stack frame file: %v", err)
			
			// SLI Metric: Parse/Write failure (server error - counts against SLO)
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
			}
			
			// Still delete the message to avoid reprocessing
			if useSQS {
				errDelete := deleteMessage(sess, task.QueueURL, task.MessageHandle)
				if errDelete != nil {
					log.Errorf("Unable to delete message from %s, err %v", task.QueueURL, errDelete)
				}
			}
			continue
		}

		// Delete message from SQS after successful processing
		if useSQS {
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
						"service":  serviceName,
						"error":    "sqs_delete_failed",
						"filename": task.Filename,
					},
				)
				continue
			}
			
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
