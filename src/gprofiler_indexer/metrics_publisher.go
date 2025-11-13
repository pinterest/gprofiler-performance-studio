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
	"net"
	"strings"
	"sync"
	"time"

	log "github.com/sirupsen/logrus"
)

// Response type constants for SLI metrics
const (
	ResponseTypeSuccess        = "success"
	ResponseTypeFailure        = "failure"
	ResponseTypeIgnoredFailure = "ignored_failure"
)

// MetricsPublisher handles sending metrics to metrics agent via TCP
type MetricsPublisher struct {
	host               string
	port               string
	serviceName        string
	sliMetricUUID      string
	enabled            bool
	connectionFailed   bool
	lastErrorLogTime   int64
	errorLogInterval   int64
	mutex              sync.Mutex
}

var (
	metricsInstance *MetricsPublisher
	metricsOnce     sync.Once
)

// NewMetricsPublisher creates or returns the singleton MetricsPublisher instance
func NewMetricsPublisher(serverURL, serviceName, sliUUID string, enabled bool) *MetricsPublisher {
	metricsOnce.Do(func() {
		instance := &MetricsPublisher{
			serviceName:      serviceName,
			sliMetricUUID:    sliUUID,
			enabled:          enabled,
			errorLogInterval: 300, // Log errors at most once every 5 minutes
		}

		// Parse server URL (tcp://host:port)
		if strings.HasPrefix(serverURL, "tcp://") {
			urlParts := strings.Split(serverURL[6:], ":")
			instance.host = urlParts[0]
			if len(urlParts) > 1 {
				instance.port = urlParts[1]
			} else {
				instance.port = "18126"
			}
		} else {
			if enabled {
				log.Fatalf("Unsupported server URL format: %s. Expected tcp://host:port", serverURL)
			}
			instance.host = "localhost"
			instance.port = "18126"
		}

		if enabled {
			log.Infof("MetricsPublisher initialized: service=%s, server=%s:%s, sli_enabled=%t",
				serviceName, instance.host, instance.port, sliUUID != "")
		} else {
			log.Info("MetricsPublisher disabled")
		}

		metricsInstance = instance
	})

	return metricsInstance
}

// GetInstance returns the singleton MetricsPublisher instance
// Returns nil if not initialized
func GetMetricsPublisher() *MetricsPublisher {
	return metricsInstance
}

// SendSLIMetric sends an SLI metric for tracking HTTP success rate
// responseType: success, failure, or ignored_failure
// methodName: The method/operation being tracked (e.g., "event_processing")
// extraTags: Additional tags as key-value pairs
func (m *MetricsPublisher) SendSLIMetric(responseType, methodName string, extraTags map[string]string) bool {
	if m == nil || !m.enabled || m.sliMetricUUID == "" {
		return false
	}

	// Build metric name using configured SLI UUID
	metricName := fmt.Sprintf("error-budget.counters.%s", m.sliMetricUUID)

	// Get current epoch timestamp
	timestamp := time.Now().Unix()

	// Build tag string with required SLI tags (Graphite plaintext protocol format)
	tags := []string{
		fmt.Sprintf("service=%s", m.serviceName),
		fmt.Sprintf("response_type=%s", responseType),
		fmt.Sprintf("method_name=%s", methodName),
	}

	if extraTags != nil {
		for key, value := range extraTags {
			tags = append(tags, fmt.Sprintf("%s=%s", key, value))
		}
	}

	tagString := strings.Join(tags, " ")

	// Format: put metric_name timestamp value tag1=value1 tag2=value2 ...
	metricLine := fmt.Sprintf("put %s %d 1 %s", metricName, timestamp, tagString)

	log.Infof("📊 Sending SLI metric: %s", metricLine)

	return m.sendMetric(metricLine)
}

// SendErrorMetric sends an operational error metric
func (m *MetricsPublisher) SendErrorMetric(metricName string, extraTags map[string]string) bool {
	if m == nil || !m.enabled {
		return false
	}

	// Get current epoch timestamp
	timestamp := time.Now().Unix()

	// Build tag string
	tags := []string{
		fmt.Sprintf("service=%s", m.serviceName),
	}

	if extraTags != nil {
		for key, value := range extraTags {
			tags = append(tags, fmt.Sprintf("%s=%s", key, value))
		}
	}

	tagString := strings.Join(tags, " ")

	// Format: put metric_name timestamp value tag1=value1 tag2=value2 ...
	metricLine := fmt.Sprintf("put %s %d 1 %s", metricName, timestamp, tagString)

	log.Debugf("📊 Sending error metric: %s", metricLine)

	return m.sendMetric(metricLine)
}

// sendMetric sends a metric line via TCP socket
func (m *MetricsPublisher) sendMetric(metricLine string) bool {
	if m == nil || !m.enabled {
		return false
	}

	// Check if we should throttle error logging
	m.mutex.Lock()
	now := time.Now().Unix()
	shouldLogError := now-m.lastErrorLogTime >= m.errorLogInterval
	m.mutex.Unlock()

	// Ensure metric line ends with newline
	if !strings.HasSuffix(metricLine, "\n") {
		metricLine = metricLine + "\n"
	}

	// Create TCP connection with timeout
	address := net.JoinHostPort(m.host, m.port)
	conn, err := net.DialTimeout("tcp", address, 1*time.Second)
	if err != nil {
		if shouldLogError {
			log.Warnf("Failed to connect to metrics agent at %s: %v", address, err)
			m.mutex.Lock()
			m.lastErrorLogTime = now
			m.connectionFailed = true
			m.mutex.Unlock()
		}
		return false
	}
	defer conn.Close()

	// Set write timeout
	conn.SetWriteDeadline(time.Now().Add(1 * time.Second))

	// Send metric
	_, err = conn.Write([]byte(metricLine))
	if err != nil {
		if shouldLogError {
			log.Warnf("Failed to send metric: %v", err)
			m.mutex.Lock()
			m.lastErrorLogTime = now
			m.mutex.Unlock()
		}
		return false
	}

	// Reset connection failed flag on success
	m.mutex.Lock()
	if m.connectionFailed {
		log.Info("Successfully reconnected to metrics agent")
		m.connectionFailed = false
	}
	m.mutex.Unlock()

	return true
}

// FlushAndClose flushes any pending metrics and closes the publisher
func (m *MetricsPublisher) FlushAndClose() {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	log.Info("MetricsPublisher closed")
	m.enabled = false
}

