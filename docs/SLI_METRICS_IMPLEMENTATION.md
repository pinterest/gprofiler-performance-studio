# SLI Metrics Implementation for Backend

**Author:** Performance Studio Team  
**Date:** October 28, 2025  
**Version:** 1.0  
**Status:** Production Ready

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Implementation Details](#implementation-details)
4. [Configuration](#configuration)
5. [Usage](#usage)
6. [Testing](#testing)
7. [Monitoring](#monitoring)
8. [Troubleshooting](#troubleshooting)
9. [References](#references)

---

## Overview

### Purpose

This document describes the implementation of **SLI (Service Level Indicator) metrics** for the gprofiler-performance-studio backend. SLI metrics enable HTTP success rate tracking for profiler uploads from agents, which is critical for:

- **SLO Monitoring:** Track service availability against defined Service Level Objectives
- **Error Budget Management:** Calculate error budgets for release decisions
- **Meaningful Availability:** Measure if the service can perform its primary function (profile uploads)
- **Operational Visibility:** Distinguish between client errors and server failures

### Key Features

- ✅ **Synchronous TCP-based metrics publisher** (no internal queuing)
- ✅ **Graphite plaintext protocol** format for metrics
- ✅ **Singleton pattern** for efficient resource usage
- ✅ **Three metric types:** `success`, `failure`, `ignored_failure`
- ✅ **Linux Docker networking support** via `host.docker.internal`
- ✅ **Graceful degradation** when metrics are disabled
- ✅ **No performance impact** on profile upload flow

### Metric Types

| Type | Description | Counts Against SLO? | Use Case |
|------|-------------|---------------------|----------|
| `success` | Profile uploaded successfully | ✅ Yes (positive) | Normal operation |
| `failure` | Server error (DB, S3, internal) | ❌ Yes (negative) | Service unavailable |
| `ignored_failure` | Client error (bad auth, metadata) | ⬜ No | Client-side issues |

**SLO Calculation:**
```
HTTP Success Rate = success / (success + failure) × 100
Target: ≥99.9%
```

---

## Architecture

### System Diagram

```
┌─────────────────┐
│  Agent/Client   │
│  (Profile Data) │
└────────┬────────┘
         │ HTTP POST
         ▼
┌─────────────────────────────────────┐
│  Backend (/v2/profiles endpoint)    │
│                                     │
│  ┌───────────────────────────────┐ │
│  │  1. Validate Auth             │ │──► ignored_failure (auth_failed)
│  │  2. Parse Profile Metadata    │ │──► ignored_failure (invalid_metadata)
│  │  3. Upload to S3              │ │──► failure (s3_error)
│  │  4. Queue to SQS              │ │──► success
│  └───────────────────────────────┘ │
│              │                      │
│              │ SLI Metric           │
│              ▼                      │
│  ┌───────────────────────────────┐ │
│  │   MetricsPublisher            │ │
│  │   (Singleton)                 │ │
│  └───────────────────────────────┘ │
└──────────────┬──────────────────────┘
               │ TCP Socket
               │ Graphite Format
               ▼
┌─────────────────────────────────────┐
│      Metrics Agent (Port 18126)     │
│  (Batching, Queuing, Forwarding)    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│         Stats Board / Grafana       │
│     (Visualization & Alerting)      │
└─────────────────────────────────────┘
```

### Design Principles

1. **Single Responsibility:** MetricsPublisher only sends metrics, agent handles batching/queuing
2. **Fail-Safe:** Metric failures never crash the main application
3. **Synchronous:** Direct TCP send with 1-second timeout (no internal queue)
4. **Singleton:** One publisher instance per application lifetime
5. **Graceful Degradation:** Returns Noop instance when disabled

---

## Implementation Details

### Files Changed

#### New Files

| File | Purpose | Lines |
|------|---------|-------|
| `src/gprofiler/backend/utils/metrics_publisher.py` | Core metrics publisher implementation | 345 |
| `test_metrics_with_mock.py` | Automated test script for DEV | 289 |
| `docs/SLI_METRICS_IMPLEMENTATION.md` | This document | - |

#### Modified Files

| File | Changes | Purpose |
|------|---------|---------|
| `src/gprofiler/backend/main.py` | +38 lines | Initialize/cleanup MetricsPublisher |
| `src/gprofiler/backend/config.py` | +8 lines | Metrics configuration variables |
| `src/gprofiler/backend/routers/profiles_routes.py` | +40 lines | Send SLI metrics on profile upload |
| `deploy/.env` | +4 lines | Metrics environment variables |
| `deploy/docker-compose.yml` | +5 lines | Linux networking fix + env vars |

### Core Components

#### 1. MetricsPublisher Class

```python
class MetricsPublisher:
    """
    Thread-safe singleton for publishing SLI metrics.
    """
    
    def __init__(
        self,
        server_url: str,
        service_name: str,
        sli_metric_uuid: Optional[str],
        enabled: bool
    ):
        # Initialize TCP connection parameters
        # Parse server URL (tcp://host:port)
        
    @classmethod
    def get_instance(cls) -> 'MetricsPublisher':
        # Always returns valid object (or NoopMetricsPublisher)
        
    def send_sli_metric(
        self,
        response_type: str,
        method_name: str,
        extra_tags: Optional[Dict[str, Any]] = None
    ) -> bool:
        # Send metric via TCP socket
        # Format: put {metric_name} {timestamp} 1 {tags}
```

#### 2. Metric Format (Graphite Plaintext Protocol)

```
put error-budget.counters.{uuid} {timestamp} 1 service={name} response_type={type} method_name={method} {extra_tags}
```

**Example:**
```
put error-budget.counters.prod-sli-uuid-789 1730131200 1 service=gprofiler-webapp response_type=success method_name=profile_upload service=devapp hostname=host-123
```

#### 3. Response Type Constants

```python
RESPONSE_TYPE_SUCCESS = "success"           # Profile uploaded successfully
RESPONSE_TYPE_FAILURE = "failure"           # Server error (counts against SLO)
RESPONSE_TYPE_IGNORED_FAILURE = "ignored_failure"  # Client error (doesn't count)
```

### Integration Points

#### In `main.py` (Application Lifecycle)

```python
@app.on_event("startup")
async def startup_event():
    # Initialize MetricsPublisher
    metrics_publisher = MetricsPublisher(
        server_url=config.METRICS_AGENT_URL,
        service_name=config.METRICS_SERVICE_NAME,
        sli_metric_uuid=config.METRICS_SLI_UUID,
        enabled=config.METRICS_ENABLED
    )

@app.on_event("shutdown")
async def shutdown_event():
    # Cleanup MetricsPublisher
    publisher = MetricsPublisher.get_instance()
    if publisher:
        publisher.flush_and_close()
```

#### In `profiles_routes.py` (Profile Upload Endpoint)

```python
def new_profile_v2(...):
    hostname = "unknown"
    
    try:
        # Validate authentication
        service_name, token_id = get_service_by_api_key(...)
        if not service_name:
            MetricsPublisher.get_instance().send_sli_metric(
                response_type=RESPONSE_TYPE_IGNORED_FAILURE,
                method_name='profile_upload',
                extra_tags={'reason': 'authentication_failed'}
            )
            raise HTTPException(400, ...)
        
        # Upload profile to S3, queue to SQS
        # ...
        
        # Success
        MetricsPublisher.get_instance().send_sli_metric(
            response_type=RESPONSE_TYPE_SUCCESS,
            method_name='profile_upload',
            extra_tags={'service': service_name, 'hostname': hostname}
        )
        
    except HTTPException:
        # Already sent metric, don't double-count
        raise
    except Exception as e:
        # Server error
        MetricsPublisher.get_instance().send_sli_metric(
            response_type=RESPONSE_TYPE_FAILURE,
            method_name='profile_upload',
            extra_tags={'error': type(e).__name__}
        )
        raise
```

---

## Configuration

### Environment Variables

Add to `deploy/.env`:

```bash
# Metrics Publisher Configuration
METRICS_ENABLED=true
METRICS_AGENT_URL=tcp://host.docker.internal:18126
METRICS_SERVICE_NAME=gprofiler-webapp
METRICS_SLI_UUID=your-sli-uuid-here
```

### Docker Compose Configuration

Add to `deploy/docker-compose.yml` (webapp service):

```yaml
webapp:
  extra_hosts:
    - "host.docker.internal:host-gateway"  # Linux compatibility
  environment:
    - METRICS_ENABLED=$METRICS_ENABLED
    - METRICS_AGENT_URL=$METRICS_AGENT_URL
    - METRICS_SERVICE_NAME=$METRICS_SERVICE_NAME
    - METRICS_SLI_UUID=$METRICS_SLI_UUID
```

### Configuration Variables

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `METRICS_ENABLED` | `false` | Enable/disable metrics publishing | Yes |
| `METRICS_AGENT_URL` | `tcp://localhost:18126` | Metrics agent TCP endpoint | Yes |
| `METRICS_SERVICE_NAME` | `gprofiler-webapp` | Service identifier in metrics | Yes |
| `METRICS_SLI_UUID` | `None` | UUID for SLI metrics namespace | Yes |

---

## Usage

### Basic Usage

```python
from backend.utils.metrics_publisher import (
    MetricsPublisher,
    RESPONSE_TYPE_SUCCESS,
    RESPONSE_TYPE_FAILURE,
    RESPONSE_TYPE_IGNORED_FAILURE,
)

# Get singleton instance (always safe to call)
publisher = MetricsPublisher.get_instance()

# Send success metric
publisher.send_sli_metric(
    response_type=RESPONSE_TYPE_SUCCESS,
    method_name='profile_upload',
    extra_tags={'service': 'my-service', 'hostname': 'host-123'}
)

# Send failure metric (server error)
publisher.send_sli_metric(
    response_type=RESPONSE_TYPE_FAILURE,
    method_name='profile_upload',
    extra_tags={'error': 'DatabaseError'}
)

# Send ignored failure (client error)
publisher.send_sli_metric(
    response_type=RESPONSE_TYPE_IGNORED_FAILURE,
    method_name='profile_upload',
    extra_tags={'reason': 'authentication_failed'}
)
```

### When to Use Each Type

#### ✅ `success` - Use When:
- Profile uploaded to S3 successfully
- Data queued to SQS successfully
- All validation passed
- Service performed its primary function

#### ❌ `failure` - Use When:
- Database connection failed
- S3 upload failed
- SQS queue unavailable
- Service registration failed
- Any server-side error preventing service function

#### ⚠️ `ignored_failure` - Use When:
- Invalid authentication credentials
- Malformed profile metadata
- Missing required parameters
- Client sent bad data
- Service is available but client error prevents operation

---

## Testing

### Development Environment Testing

Since DEV doesn't have S3/SQS access, use the provided test script:

```bash
# Run automated tests
python test_metrics_with_mock.py
```

**Tests Covered:**
1. ✅ Authentication failed → `ignored_failure`
2. ✅ Invalid metadata → `ignored_failure`
3. ✅ Missing parameter → `ignored_failure`
4. ✅ Network connectivity to metrics agent
5. ✅ No double-counting of metrics

### Manual Testing

```bash
# Test invalid authentication
curl -X POST http://127.0.0.1:8080/api/v2/profiles \
  -H 'gprofiler-api-key: INVALID' \
  -H 'gprofiler-service-name: test' \
  -H 'Content-Type: application/json' \
  -d '{"start_time": "2025-10-28T00:00:00.000Z", "profile": "test", "gpid": "123"}'

# Check logs for metric
docker logs gprofiler-ps-webapp 2>&1 | grep '📊 Sending SLI metric'
```

### Expected Log Output

```
[INFO] MetricsPublisher initialized: service=gprofiler-webapp, server=host.docker.internal:18126, sli_enabled=True
[INFO] 📊 Sending SLI metric: put error-budget.counters.test-sli-uuid-12345 1730131200 1 service=gprofiler-webapp response_type=ignored_failure method_name=profile_upload reason=authentication_failed
```

### Test Results Summary

| Test | Status | Notes |
|------|--------|-------|
| MetricsPublisher Initialization | ✅ PASS | Singleton working correctly |
| Network Connectivity | ✅ PASS | host.docker.internal resolves on Linux |
| Authentication Failed Metric | ✅ PASS | Single metric sent (no double counting) |
| Invalid Metadata Metric | ✅ PASS | Correct tags |
| Metric Format | ✅ PASS | Graphite plaintext protocol |
| No Connection Errors | ✅ PASS | All metrics delivered to agent |

---

## Monitoring

### Key Metrics to Track

#### 1. HTTP Success Rate (Primary SLI)

```promql
# Success rate over 5 minutes
sum(rate(error-budget.counters.{your-uuid}{response_type="success"}[5m]))
/ 
(
  sum(rate(error-budget.counters.{your-uuid}{response_type="success"}[5m])) 
  + sum(rate(error-budget.counters.{your-uuid}{response_type="failure"}[5m]))
) * 100
```

**Target:** ≥99.9% (SLO)

#### 2. Total Requests by Type

```promql
sum by (response_type) (error-budget.counters.{your-uuid})
```

#### 3. Client Error Rate

```promql
sum(rate(error-budget.counters.{your-uuid}{response_type="ignored_failure"}[5m]))
```

**Use:** Identify client-side integration issues

#### 4. Metric Send Success Rate

Monitor backend logs for:
- `Failed to send metric` errors
- Connection failures to metrics agent

**Alert if:** Connection failure rate > 1%

### Dashboard Queries

```
# Request volume by response type
sum by (response_type) (rate(error-budget.counters.{your-uuid}[5m]))

# Failure breakdown by error type
sum by (error) (rate(error-budget.counters.{your-uuid}{response_type="failure"}[5m]))

# Client error breakdown by reason
sum by (reason) (rate(error-budget.counters.{your-uuid}{response_type="ignored_failure"}[5m]))
```

### Alerting Rules

```yaml
# SLO Violation
- alert: SLOViolation
  expr: http_success_rate < 99.9
  for: 5m
  annotations:
    summary: "HTTP success rate below SLO target"
    
# High Failure Rate
- alert: HighFailureRate
  expr: rate(error-budget.counters.{your-uuid}{response_type="failure"}[5m]) > 0.01
  for: 5m
  annotations:
    summary: "High server failure rate detected"
```

---

## Troubleshooting

### Issue: Connection Errors to Metrics Agent

**Symptoms:**
```
[WARNING] Failed to send metric: [Errno -2] Name or service not known
```

**Cause:** `host.docker.internal` DNS not resolving (Linux)

**Fix:**
```yaml
# docker-compose.yml
webapp:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

Then restart:
```bash
docker-compose restart webapp
```

---

### Issue: Double Metrics Being Sent

**Symptoms:**
```
[INFO] 📊 Sending SLI metric: ... response_type=ignored_failure reason=authentication_failed
[INFO] 📊 Sending SLI metric: ... response_type=failure error=HTTPException
```

**Cause:** `HTTPException` caught by generic `except Exception` handler

**Fix:** Already implemented - `except HTTPException: raise` added before generic exception handler

---

### Issue: Metrics Not Visible on Stats Board

**Troubleshooting Steps:**

1. **Check backend logs:**
```bash
docker logs gprofiler-ps-webapp 2>&1 | grep '📊 Sending SLI metric'
```

2. **Verify UUID matches:**
```bash
# Check backend config
docker exec gprofiler-ps-webapp env | grep METRICS_SLI_UUID

# Compare with stats board query
```

3. **Test metrics agent connectivity:**
```bash
docker exec gprofiler-ps-webapp nc -zv host.docker.internal 18126
```

4. **Check metrics agent logs:**
- Verify agent is receiving metrics
- Check forwarding configuration

---

### Issue: Performance Impact

**Symptoms:** Slow profile upload response times

**Investigation:**
- Metrics are sent synchronously with 1-second timeout
- Check for repeated connection failures
- Consider if 1-second timeout is too high

**Mitigation:**
```bash
# Disable metrics temporarily
METRICS_ENABLED=false
docker-compose restart webapp
```

---

## Bug Fixes Applied

### Bug #1: Double Metric Sending

**Problem:** When `HTTPException` was raised for client errors, two metrics were sent:
1. `ignored_failure` (correct)
2. `failure` (incorrect - double counting)

**Root Cause:** Generic `except Exception` handler caught `HTTPException` after already sending `ignored_failure` metric.

**Fix:**
```python
except HTTPException:
    # Already handled above, don't double-count
    raise
except Exception as e:
    # Server error
    MetricsPublisher.get_instance().send_sli_metric(...)
```

**Verification:** ✅ Only one metric per request

---

### Bug #2: Misleading Metric Reason

**Problem:** Validation error message said "Invalid GPROFILER-SERVICE-NAME header" but metric said `reason=invalid_api_key`

**Root Cause:** `get_service_by_api_key()` validates BOTH API key AND service name, but metric only mentioned API key.

**Fix:** Changed reason from `invalid_api_key` to `authentication_failed` to accurately reflect validation of both credentials.

**Verification:** ✅ Metric reason now matches error message intent

---

## References

### Internal Documentation
- [Test Plan: METRICS_TEST_PLAN.md](../METRICS_TEST_PLAN.md)
- [Meaningful Availability: SLI_MEANINGFUL_AVAILABILITY.md](../SLI_MEANINGFUL_AVAILABILITY.md)
- [Backend Categories: METRICS_CATEGORIES_BACKEND.md](../METRICS_CATEGORIES_BACKEND.md)

### External References
- [Graphite Plaintext Protocol](http://graphite.readthedocs.io/en/latest/feeding-carbon.html)
- [Google SRE Book - Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)
- [Docker host.docker.internal](https://docs.docker.com/desktop/networking/#i-want-to-connect-from-a-container-to-a-service-on-the-host)

### Related Pull Requests
- [Backend Metrics PR #34](https://github.com/pinterest/gprofiler-performance-studio/pull/34)

---

## Appendix

### Complete Example: Profile Upload with Metrics

```python
def new_profile_v2(
    request: Request,
    agent_data: AgentData,
    gprofiler_api_key: str = Header(...),
    gprofiler_service_name: str = Header(...),
):
    hostname = "unknown"
    
    try:
        # Step 1: Authentication
        service_name, token_id = get_service_by_api_key(
            gprofiler_api_key, 
            gprofiler_service_name
        )
        
        if not service_name:
            # CLIENT ERROR: Invalid credentials
            MetricsPublisher.get_instance().send_sli_metric(
                response_type=RESPONSE_TYPE_IGNORED_FAILURE,
                method_name='profile_upload',
                extra_tags={'reason': 'authentication_failed'}
            )
            raise HTTPException(400, {"message": "Invalid credentials"})
        
        # Step 2: Parse metadata
        try:
            metadata = parse_profile_metadata(agent_data.profile)
            hostname = metadata["hostname"]
        except Exception:
            # CLIENT ERROR: Invalid metadata
            MetricsPublisher.get_instance().send_sli_metric(
                response_type=RESPONSE_TYPE_IGNORED_FAILURE,
                method_name='profile_upload',
                extra_tags={'reason': 'invalid_metadata', 'hostname': hostname}
            )
            raise HTTPException(400, {"message": "Invalid metadata"})
        
        # Step 3: Upload to S3
        upload_to_s3(profile_data)
        
        # Step 4: Queue to SQS
        queue_to_sqs(message)
        
    except HTTPException:
        # Already handled with appropriate metric
        raise
        
    except Exception as e:
        # SERVER ERROR: Counts against SLO
        MetricsPublisher.get_instance().send_sli_metric(
            response_type=RESPONSE_TYPE_FAILURE,
            method_name='profile_upload',
            extra_tags={'error': type(e).__name__}
        )
        raise
    
    # SUCCESS: All steps completed
    MetricsPublisher.get_instance().send_sli_metric(
        response_type=RESPONSE_TYPE_SUCCESS,
        method_name='profile_upload',
        extra_tags={'service': service_name, 'hostname': hostname}
    )
    
    return ProfileResponse(message="ok", gpid=int(gpid))
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-10-28 | Initial production release |
| | | - Synchronous TCP-based metrics publisher |
| | | - Three metric types: success/failure/ignored_failure |
| | | - Graphite plaintext protocol format |
| | | - Linux Docker networking support |
| | | - Bug fixes: double counting, misleading reasons |

---

**Document Version:** 1.0  
**Last Updated:** October 28, 2025  
**Maintained By:** Performance Studio Team

