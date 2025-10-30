# Metrics Publisher - Indexer Documentation

## Overview

The Metrics Publisher is a lightweight, production-ready component for tracking Service Level Indicators (SLIs) in the gProfiler Performance Studio **indexer** service. It sends metrics in Graphite plaintext protocol format over TCP to a metrics agent for monitoring and alerting.

---

## Table of Contents

1. [Purpose and Use Case](#purpose-and-use-case)
2. [Architecture](#architecture)
3. [Implementation Details](#implementation-details)
4. [Usage Guide](#usage-guide)
5. [Configuration](#configuration)
6. [Metrics Format](#metrics-format)
7. [Code Examples](#code-examples)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)

---

## Purpose and Use Case

### What Problem Does It Solve?

The Metrics Publisher enables **meaningful availability tracking** for the indexer service by measuring:
- **Success Rate**: Percentage of events processed successfully
- **Error Budget**: Tracking failures that count against SLO
- **Service Health**: Real-time monitoring of event processing

### SLI (Service Level Indicator) Tracking

The publisher tracks three response types:
1. **`success`**: Event processed completely (counts toward SLO)
2. **`failure`**: Server error, counts against error budget (impacts SLO)
3. **`ignored_failure`**: Client error, doesn't count against SLO

### Key Use Cases

- **SLO Monitoring**: Track if service meets 99.9% success rate
- **Alerting**: Trigger alerts when error budget is consumed
- **Debugging**: Identify which operations are failing
- **Capacity Planning**: Understand processing volumes

---

## Architecture

### Design Principles

1. **Singleton Pattern**: One instance per application lifecycle
2. **Thread-Safe**: Safe for concurrent use from multiple goroutines
3. **Non-Blocking**: Metrics sending doesn't block main application flow
4. **Fail-Safe**: Application continues if metrics agent is unavailable
5. **Opt-In**: Disabled by default, must be explicitly enabled

### Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    Indexer Application                   │
│                                                          │
│  ┌────────────┐        ┌────────────────────────────┐  │
│  │   Worker   │───────▶│  MetricsPublisher          │  │
│  │  (main.go) │        │  (singleton)               │  │
│  └────────────┘        │                            │  │
│                        │  - SendSLIMetric()         │  │
│                        │  - SendErrorMetric()       │  │
│                        │  - sendMetric()            │  │
│                        └─────────┬──────────────────┘  │
│                                  │                      │
└──────────────────────────────────┼──────────────────────┘
                                   │ TCP Connection
                                   │ (Graphite Protocol)
                                   ▼
                        ┌──────────────────────┐
                        │   Metrics Agent      │
                        │   (port 18126)       │
                        │                      │
                        │  - Receives metrics  │
                        │  - Forwards to       │
                        │    monitoring system │
                        └──────────────────────┘
```

### Data Flow

```
1. Event Processing (worker.go)
   │
   ├─▶ Success?
   │   ├─▶ YES: GetMetricsPublisher().SendSLIMetric("success", ...)
   │   └─▶ NO:  GetMetricsPublisher().SendSLIMetric("failure", ...)
   │
2. MetricsPublisher checks if enabled
   │
   ├─▶ Disabled? → Return immediately (no-op)
   └─▶ Enabled?  → Continue
       │
3. Build metric line (Graphite format)
   │
4. Send via TCP socket (1 second timeout)
   │
   ├─▶ Success? → Log and return
   └─▶ Failed?  → Log error (throttled) and return
```

---

## Implementation Details

### File: `src/gprofiler_indexer/metrics_publisher.go`

#### Key Components

##### 1. MetricsPublisher Struct

```go
type MetricsPublisher struct {
    host               string      // Metrics agent hostname
    port               string      // Metrics agent port
    serviceName        string      // Service identifier
    sliMetricUUID      string      // SLI metric UUID
    enabled            bool        // Master enable/disable flag
    connectionFailed   bool        // Connection state tracking
    lastErrorLogTime   int64       // For error log throttling
    errorLogInterval   int64       // Error log interval (5 minutes)
    mutex              sync.Mutex  // Thread safety
}
```

**Thread Safety:**
- `mutex` protects concurrent access to state fields
- Methods are safe to call from multiple goroutines

##### 2. Singleton Pattern

```go
var (
    metricsInstance *MetricsPublisher  // Global singleton instance
    metricsOnce     sync.Once          // Ensures single initialization
)

// NewMetricsPublisher creates or returns the singleton instance
func NewMetricsPublisher(serverURL, serviceName, sliUUID string, enabled bool) *MetricsPublisher {
    metricsOnce.Do(func() {
        // Initialize once
        metricsInstance = &MetricsPublisher{...}
    })
    return metricsInstance
}

// GetMetricsPublisher returns the singleton (safe to call before init)
func GetMetricsPublisher() *MetricsPublisher {
    return metricsInstance  // May be nil if not initialized
}
```

**Why Singleton?**
- Single TCP connection pool per application
- Consistent state across all callers
- Prevents connection exhaustion

##### 3. Core Methods

###### SendSLIMetric (Public)

```go
func (m *MetricsPublisher) SendSLIMetric(
    responseType string,      // "success", "failure", "ignored_failure"
    methodName string,         // Operation name (e.g., "event_processing")
    extraTags map[string]string  // Additional context tags
) bool
```

**Purpose:** Send SLI metrics for SLO tracking

**Guards:**
- Returns `false` immediately if `m == nil`
- Returns `false` if `!m.enabled`
- Returns `false` if `m.sliMetricUUID == ""`

**Metric Format:**
```
put error-budget.counters.{sliMetricUUID} {timestamp} 1 service={serviceName} response_type={responseType} method_name={methodName} [extraTags...]
```

###### SendErrorMetric (Public)

```go
func (m *MetricsPublisher) SendErrorMetric(
    metricName string,
    extraTags map[string]string
) bool
```

**Purpose:** Send operational error metrics (non-SLI)

**Use Case:** Track internal errors not related to SLO

###### sendMetric (Private)

```go
func (m *MetricsPublisher) sendMetric(metricLine string) bool
```

**Purpose:** Low-level TCP sending with error handling

**Features:**
- Creates new TCP connection per metric (stateless)
- 1 second connection timeout
- 1 second write timeout
- Error log throttling (max once per 5 minutes)
- Connection recovery tracking

##### 4. Error Handling

**Throttled Logging:**
```go
// Only log errors once every 5 minutes to prevent log spam
m.mutex.Lock()
now := time.Now().Unix()
shouldLogError := now-m.lastErrorLogTime >= m.errorLogInterval
m.mutex.Unlock()

if shouldLogError {
    log.Warnf("Failed to connect to metrics agent: %v", err)
    m.mutex.Lock()
    m.lastErrorLogTime = now
    m.mutex.Unlock()
}
```

**Graceful Degradation:**
- Application continues even if metrics agent is down
- No retries (fire-and-forget)
- Connection recovery logged on success

---

## Usage Guide

### Integration Pattern

#### 1. Initialization (main.go)

```go
func main() {
    args := NewCliArgs()
    args.ParseArgs()
    
    // Initialize metrics publisher (singleton)
    metricsPublisher := NewMetricsPublisher(
        args.MetricsAgentURL,      // tcp://host:port
        args.MetricsServiceName,   // "gprofiler-indexer"
        args.MetricsSLIUUID,       // "test-sli-uuid-indexer-67890"
        args.MetricsEnabled,       // true/false
    )
    
    // ... rest of application ...
    
    // Cleanup on shutdown
    if metricsPublisher != nil {
        metricsPublisher.FlushAndClose()
    }
}
```

#### 2. Usage in Workers (worker.go)

**Pattern A: Direct Call (Recommended)**

```go
// Get singleton instance and call directly
GetMetricsPublisher().SendSLIMetric(
    ResponseTypeSuccess,
    "event_processing",
    map[string]string{
        "service":  serviceName,
        "filename": task.Filename,
    },
)
```

**Why this works:**
- `GetMetricsPublisher()` may return `nil` (not initialized)
- `SendSLIMetric()` has nil-safe check: `if m == nil { return false }`
- No panic, graceful no-op if disabled

**Pattern B: Conditional Call (Optional)**

```go
// Only for SQS events (not local file processing)
if useSQS {
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeSuccess,
        "event_processing",
        tags,
    )
}
```

#### 3. Success Tracking Example

```go
// Successful event processing
err := pw.ParseStackFrameFile(sess, task, args.S3Bucket, timestamp, buf)
if err != nil {
    // Track failure
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeFailure,
        "event_processing",
        map[string]string{
            "service":  serviceName,
            "error":    "parse_or_write_failed",
            "filename": task.Filename,
        },
    )
    return
}

// Track success
GetMetricsPublisher().SendSLIMetric(
    ResponseTypeSuccess,
    "event_processing",
    map[string]string{
        "service":  serviceName,
        "filename": task.Filename,
    },
)
```

#### 4. Error Budget Tracking

```go
// S3 fetch failed (counts against SLO)
if err != nil {
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeFailure,  // ← Counts against error budget
        "event_processing",
        map[string]string{
            "service":  serviceName,
            "error":    "s3_fetch_failed",
            "filename": task.Filename,
        },
    )
}

// SQS delete failed (still counts as failure)
if errDelete != nil {
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeFailure,  // ← Also counts against SLO
        "event_processing",
        map[string]string{
            "service":  serviceName,
            "error":    "sqs_delete_failed",
            "filename": task.Filename,
        },
    )
}
```

---

## Configuration

### Environment Variables

```bash
# Enable/disable metrics publishing
METRICS_ENABLED=true

# Metrics agent TCP endpoint
METRICS_AGENT_URL=tcp://localhost:18126

# Service identifier for metrics
METRICS_SERVICE_NAME=gprofiler-indexer

# SLI metric UUID for error budget tracking
METRICS_SLI_UUID=prod-sli-uuid-12345
```

### Command-Line Flags

```bash
./indexer \
  --metrics-enabled=true \
  --metrics-agent-url=tcp://metrics-agent:18126 \
  --metrics-service-name=gprofiler-indexer \
  --metrics-sli-uuid=prod-sli-uuid-12345
```

### Configuration in Code (args.go)

```go
type CLIArgs struct {
    // Metrics Publisher Configuration
    MetricsEnabled     bool
    MetricsAgentURL    string
    MetricsServiceName string
    MetricsSLIUUID     string
}

func NewCliArgs() *CLIArgs {
    return &CLIArgs{
        // Metrics defaults (disabled by default)
        MetricsEnabled:     false,
        MetricsAgentURL:    "tcp://localhost:18126",
        MetricsServiceName: "gprofiler-indexer",
        MetricsSLIUUID:     "",
    }
}
```

### Production vs Development

#### Production Configuration
```bash
METRICS_ENABLED=true
METRICS_AGENT_URL=tcp://prod-metrics-agent.internal:18126
METRICS_SERVICE_NAME=gprofiler-indexer
METRICS_SLI_UUID=a1b2c3d4-5678-90ab-cdef-1234567890ab  # Real UUID
```

#### Development/Local Configuration
```bash
METRICS_ENABLED=true
METRICS_AGENT_URL=tcp://host.docker.internal:18126
METRICS_SERVICE_NAME=gprofiler-indexer
METRICS_SLI_UUID=test-sli-uuid-indexer-67890  # Test UUID
```

#### Disabled Configuration
```bash
METRICS_ENABLED=false
# Other values are ignored when disabled
```

---

## Metrics Format

### Graphite Plaintext Protocol

The publisher uses the [Graphite plaintext protocol](http://graphite.readthedocs.io/en/latest/feeding-carbon.html):

```
put <metric_name> <timestamp> <value> <tag1>=<value1> <tag2>=<value2> ...
```

### SLI Metric Format

```
put error-budget.counters.<sli_uuid> <epoch_timestamp> 1 service=<service_name> response_type=<type> method_name=<method> [extra_tags...]
```

**Example:**
```
put error-budget.counters.test-sli-uuid-indexer-67890 1761857905 1 service=gprofiler-indexer response_type=success method_name=event_processing service=devapp filename=2025-10-30T20:56:10_xxxxx.gz
```

### Field Descriptions

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `metric_name` | string | Metric identifier with UUID | `error-budget.counters.test-sli-uuid-12345` |
| `timestamp` | integer | Unix epoch timestamp (seconds) | `1761857905` |
| `value` | integer | Always `1` (counter increment) | `1` |
| `service` | string (tag) | Service name from configuration | `gprofiler-indexer` |
| `response_type` | string (tag) | Result: `success`, `failure`, `ignored_failure` | `success` |
| `method_name` | string (tag) | Operation being tracked | `event_processing` |
| `extra_tags` | key=value | Additional context tags | `service=devapp filename=xxx.gz error=s3_fetch_failed` |

### Response Types

#### `success`
- Event processed completely
- Counts **FOR** SLO (increases success rate)
- Example: Profile parsed and inserted into ClickHouse

#### `failure`
- Server-side error
- Counts **AGAINST** error budget (decreases availability)
- Example: S3 fetch failed, ClickHouse insertion failed

#### `ignored_failure`
- Client-side error
- Does **NOT** count against SLO
- Example: Invalid profile format, malformed JSON

---

## Code Examples

### Example 1: Basic Success/Failure Tracking

```go
func ProcessEvent(event SQSMessage) error {
    // Attempt to process
    result, err := doProcessing(event)
    
    if err != nil {
        // Track failure
        GetMetricsPublisher().SendSLIMetric(
            ResponseTypeFailure,
            "event_processing",
            map[string]string{
                "service": event.Service,
                "error":   err.Error(),
            },
        )
        return err
    }
    
    // Track success
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeSuccess,
        "event_processing",
        map[string]string{
            "service": event.Service,
        },
    )
    return nil
}
```

### Example 2: Multi-Stage Processing with Detailed Tracking

```go
func ProcessProfile(task SQSMessage) error {
    // Stage 1: Fetch from S3
    buf, err := GetFileFromS3(sess, bucket, task.Filename)
    if err != nil {
        GetMetricsPublisher().SendSLIMetric(
            ResponseTypeFailure,
            "event_processing",
            map[string]string{
                "service":  task.Service,
                "error":    "s3_fetch_failed",
                "filename": task.Filename,
            },
        )
        return err
    }
    
    // Stage 2: Parse and insert
    err = ParseAndInsert(buf)
    if err != nil {
        GetMetricsPublisher().SendSLIMetric(
            ResponseTypeFailure,
            "event_processing",
            map[string]string{
                "service":  task.Service,
                "error":    "parse_or_insert_failed",
                "filename": task.Filename,
            },
        )
        return err
    }
    
    // Stage 3: Cleanup
    err = deleteMessage(sess, task.QueueURL, task.MessageHandle)
    if err != nil {
        GetMetricsPublisher().SendSLIMetric(
            ResponseTypeFailure,
            "event_processing",
            map[string]string{
                "service":  task.Service,
                "error":    "sqs_delete_failed",
                "filename": task.Filename,
            },
        )
        return err
    }
    
    // Success!
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeSuccess,
        "event_processing",
        map[string]string{
            "service":  task.Service,
            "filename": task.Filename,
        },
    )
    return nil
}
```

### Example 3: Conditional Metrics (Only for Production Traffic)

```go
func Worker(tasks <-chan SQSMessage) {
    for task := range tasks {
        useSQS := task.Service != ""
        
        result := processTask(task)
        
        // Only track SQS events, not local file processing
        if useSQS {
            if result.Success {
                GetMetricsPublisher().SendSLIMetric(
                    ResponseTypeSuccess,
                    "event_processing",
                    map[string]string{"service": task.Service},
                )
            } else {
                GetMetricsPublisher().SendSLIMetric(
                    ResponseTypeFailure,
                    "event_processing",
                    map[string]string{
                        "service": task.Service,
                        "error":   result.Error,
                    },
                )
            }
        }
    }
}
```

---

## Testing

### Unit Testing Pattern

```go
func TestMetricsPublisher_SendSLIMetric(t *testing.T) {
    // Test 1: Disabled publisher (no-op)
    pub := &MetricsPublisher{enabled: false}
    result := pub.SendSLIMetric("success", "test_method", nil)
    assert.False(t, result)  // Should return false (no-op)
    
    // Test 2: Nil publisher (safe)
    var nilPub *MetricsPublisher
    result = nilPub.SendSLIMetric("success", "test_method", nil)
    assert.False(t, result)  // Should not panic
    
    // Test 3: Enabled but no UUID
    pub = &MetricsPublisher{
        enabled:     true,
        sliMetricUUID: "",
    }
    result = pub.SendSLIMetric("success", "test_method", nil)
    assert.False(t, result)  // Should return false
}
```

### Integration Testing

```bash
# Terminal 1: Start mock metrics agent (listens on TCP)
nc -l -k 18126

# Terminal 2: Run indexer with metrics enabled
METRICS_ENABLED=true \
METRICS_AGENT_URL=tcp://localhost:18126 \
METRICS_SLI_UUID=test-uuid-12345 \
./indexer

# Terminal 1: Should show metric lines
put error-budget.counters.test-uuid-12345 1761857905 1 service=gprofiler-indexer response_type=success method_name=event_processing
```

### Local Testing with Docker

```bash
# Start services
cd deploy
docker-compose --profile with-clickhouse up -d

# Check metrics in indexer logs
docker-compose logs ch-indexer | grep "📊 Sending SLI metric"

# Expected output:
# level=info msg="📊 Sending SLI metric: put error-budget.counters.test-sli-uuid-indexer-67890 ..."
```

---

## Troubleshooting

### Issue 1: Metrics Not Appearing

**Symptoms:**
- No "Sending SLI metric" logs
- Metrics dashboard shows no data

**Debugging Steps:**

```bash
# Check if metrics enabled
docker-compose logs ch-indexer | grep "MetricsPublisher initialized"

# Expected: "sli_enabled=true"
# If "sli_enabled=false", check METRICS_SLI_UUID is set
```

**Common Causes:**
1. `METRICS_ENABLED=false`
2. `METRICS_SLI_UUID` is empty
3. No events being processed (check SQS queue)

### Issue 2: Connection Refused Errors

**Symptoms:**
```
level=warn msg="Failed to connect to metrics agent at host.docker.internal:18126: connection refused"
```

**Solutions:**

```bash
# Check if metrics agent is running
nc -zv host.docker.internal 18126

# In Docker Compose, ensure host.docker.internal is resolvable
# Add to docker-compose.yml:
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Issue 3: Metrics Sent But Not Visible

**Symptoms:**
- Logs show "📊 Sending SLI metric"
- But metrics dashboard has no data

**Check:**
1. **Metrics agent is receiving:**
   ```bash
   # Monitor metrics agent logs
   tail -f /var/log/metrics-agent.log
   ```

2. **Metric format is correct:**
   ```
   put error-budget.counters.{uuid} {timestamp} 1 service=...
   ```

3. **UUID matches dashboard query:**
   ```
   error-budget.counters.test-sli-uuid-indexer-67890
   ```

### Issue 4: Log Spam from Connection Errors

**Symptoms:**
- Many "Failed to connect" messages in logs

**Why This Happens:**
- Error log throttling limits to once per 5 minutes
- If seeing spam, check `errorLogInterval` setting

**Solution:**
- Normal behavior if metrics agent is down
- Errors are throttled automatically
- Application continues working

### Issue 5: Metrics Delayed

**Why:**
- Metrics sent synchronously (1 second timeout)
- TCP connection created per metric
- Not batched

**Impact:**
- Minimal (<1ms typical)
- Max 1 second per metric (on timeout)
- Does not block processing

---

## Performance Considerations

### Overhead

**Per Metric:**
- TCP connection: ~1-2ms
- Metric formatting: <0.1ms
- Network I/O: 1-5ms (local network)

**Total:** ~5-10ms per metric (negligible)

### Scalability

**Current Design:**
- No connection pooling
- New TCP connection per metric
- Fire-and-forget (no retries)

**Max Throughput:**
- ~100-200 metrics/second (single instance)
- Sufficient for indexer workload (<10 metrics/second typical)

**If Scaling Needed:**
- Add connection pooling
- Batch multiple metrics
- Use UDP instead of TCP

### Resource Usage

**Memory:**
- ~1KB per MetricsPublisher instance (singleton)
- Negligible per-metric allocation

**CPU:**
- <0.1% for typical workload
- Mostly network I/O (non-blocking)

**Network:**
- ~200-300 bytes per metric
- Insignificant bandwidth

---

## Best Practices

### DO ✅

1. **Use Response Types Correctly**
   - `success`: Operation completed successfully
   - `failure`: Server error (counts against SLO)
   - `ignored_failure`: Client error (doesn't count)

2. **Add Context with Tags**
   ```go
   map[string]string{
       "service":  serviceName,
       "filename": filename,
       "error":    errorType,
   }
   ```

3. **Track Only Production Events**
   ```go
   if useSQS {  // Only track real events, not test data
       GetMetricsPublisher().SendSLIMetric(...)
   }
   ```

4. **Let Publisher Handle nil/disabled**
   ```go
   // No need to check if enabled
   GetMetricsPublisher().SendSLIMetric(...)
   ```

### DON'T ❌

1. **Don't Create Multiple Instances**
   ```go
   // ❌ Wrong
   pub := NewMetricsPublisher(...)
   pub.SendSLIMetric(...)
   
   // ✅ Correct
   GetMetricsPublisher().SendSLIMetric(...)
   ```

2. **Don't Block on Metrics**
   ```go
   // ❌ Wrong
   if !GetMetricsPublisher().SendSLIMetric(...) {
       return errors.New("metrics failed")
   }
   
   // ✅ Correct
   GetMetricsPublisher().SendSLIMetric(...)  // Fire and forget
   ```

3. **Don't Track Client Errors as Failures**
   ```go
   // ❌ Wrong
   if err == ErrInvalidInput {
       GetMetricsPublisher().SendSLIMetric(ResponseTypeFailure, ...)
   }
   
   // ✅ Correct
   if err == ErrInvalidInput {
       GetMetricsPublisher().SendSLIMetric(ResponseTypeIgnoredFailure, ...)
   }
   ```

4. **Don't Retry Metric Sending**
   ```go
   // ❌ Wrong
   for i := 0; i < 3; i++ {
       if GetMetricsPublisher().SendSLIMetric(...) {
           break
       }
   }
   
   // ✅ Correct
   GetMetricsPublisher().SendSLIMetric(...)  // Single attempt
   ```

---

## Metrics Dashboard Queries

### SLO Calculation (Success Rate)

```sql
-- Success rate over 5 minutes
sum(rate(error-budget.counters.prod-sli-uuid{response_type="success"}[5m]))
/
(
  sum(rate(error-budget.counters.prod-sli-uuid{response_type="success"}[5m]))
  + 
  sum(rate(error-budget.counters.prod-sli-uuid{response_type="failure"}[5m]))
) * 100
```

### Error Budget Consumption

```sql
-- Errors per hour
sum(increase(error-budget.counters.prod-sli-uuid{response_type="failure"}[1h]))
```

### Top Failure Reasons

```sql
-- Group by error type
sum by (error) (
  increase(error-budget.counters.prod-sli-uuid{response_type="failure"}[1h])
)
```

---

## Migration Guide

### Adding Metrics to Existing Service

**Step 1: Add Configuration**

```go
// args.go
type CLIArgs struct {
    // ... existing fields ...
    MetricsEnabled     bool
    MetricsAgentURL    string
    MetricsServiceName string
    MetricsSLIUUID     string
}
```

**Step 2: Initialize in main()**

```go
// main.go
func main() {
    // ... existing code ...
    
    metricsPublisher := NewMetricsPublisher(
        args.MetricsAgentURL,
        args.MetricsServiceName,
        args.MetricsSLIUUID,
        args.MetricsEnabled,
    )
    
    defer func() {
        if metricsPublisher != nil {
            metricsPublisher.FlushAndClose()
        }
    }()
    
    // ... rest of application ...
}
```

**Step 3: Add Tracking to Workers**

```go
// worker.go
func processEvent(event Event) error {
    result, err := doWork(event)
    
    if err != nil {
        GetMetricsPublisher().SendSLIMetric(
            ResponseTypeFailure,
            "event_processing",
            map[string]string{"error": err.Error()},
        )
        return err
    }
    
    GetMetricsPublisher().SendSLIMetric(
        ResponseTypeSuccess,
        "event_processing",
        nil,
    )
    return nil
}
```

---

## Changelog

### Version 1.0 (2025-10-30)
- Initial implementation
- Singleton pattern with thread safety
- Graphite plaintext protocol
- SLI metric tracking
- Error log throttling
- Graceful degradation
- Nil-safe method calls

---

## References

- **Graphite Protocol**: http://graphite.readthedocs.io/en/latest/feeding-carbon.html
- **SLO/SLI Best Practices**: https://sre.google/sre-book/service-level-objectives/
- **Backend Implementation**: [PR #34](https://github.com/pinterest/gprofiler-performance-studio/pull/34)

---

## Support

For questions or issues:
1. Check troubleshooting section above
2. Review integration tests in `LOCAL_TESTING_GUIDE.md`
3. Check indexer logs for metric output
4. Verify metrics agent is receiving data

---

**Last Updated:** 2025-10-30  
**Version:** 1.0  
**Author:** Development Team

