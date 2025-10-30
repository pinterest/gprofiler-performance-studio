# Local Testing Guide: gProfiler Performance Studio

## Complete End-to-End Testing Steps with Verification

This guide documents how to test the gProfiler Performance Studio locally with all metrics and data flow verification at each stage.

---

## Prerequisites

- Docker and Docker Compose installed
- gProfiler agent built (`./build/x86_64/gprofiler`)
- Sufficient permissions (some commands require `sudo`)

---

## Stage 1: Environment Setup

### 1.1 Verify Configuration Files

**Check `.env` configuration:**
```bash
cd deploy
cat .env | grep -E "(BUCKET_NAME|METRICS|AWS)"
```

**Expected output:**
```
BUCKET_NAME=performance-studio-bucket
METRICS_ENABLED=true
METRICS_AGENT_URL=tcp://host.docker.internal:18126
METRICS_SERVICE_NAME=gprofiler-webapp
METRICS_SLI_UUID=test-sli-uuid-12345
INDEXER_METRICS_ENABLED=true
INDEXER_METRICS_AGENT_URL=tcp://host.docker.internal:18126
INDEXER_METRICS_SERVICE_NAME=gprofiler-indexer
INDEXER_METRICS_SLI_UUID=test-sli-uuid-indexer-67890
AWS_ENDPOINT_URL=http://localstack:4566
S3_ENDPOINT_URL=http://localstack:4566
```

**Check LocalStack initialization script:**
```bash
cat localstack_init/01_init_s3_sqs.sh | grep "mb s3"
```

**Expected:** Bucket name matches `.env` (with hyphens):
```bash
awslocal s3 mb s3://performance-studio-bucket
```

---

## Stage 2: Start Services

### 2.1 Start All Services
```bash
cd deploy
sudo docker-compose --profile with-clickhouse down
sudo docker-compose --profile with-clickhouse up -d --build
```

### 2.2 Wait for Services to Initialize
```bash
sleep 15
sudo docker-compose ps
```

**Expected output:** All services in "running" or "running (healthy)" state:
```
NAME                               STATUS
gprofiler-ps-agents-logs-backend   running
gprofiler-ps-ch-indexer            running
gprofiler-ps-ch-rest-service       running (healthy)
gprofiler-ps-clickhouse            running
gprofiler-ps-localstack            running (healthy)
gprofiler-ps-nginx-load-balancer   running
gprofiler-ps-periodic-tasks        running
gprofiler-ps-postgres              running
gprofiler-ps-webapp                running
```

---

## Stage 3: Verify LocalStack (S3 + SQS)

### 3.1 Check LocalStack Initialization Logs
```bash
docker-compose logs localstack | grep -E "(bucket|queue|Ready)"
```

**Expected output:**
```
✅ S3 bucket 'performance-studio-bucket' created
✅ SQS queue 'performance_studio_queue' created
✅ Queue URL: http://sqs.us-east-1.localstack:4566/000000000000/performance_studio_queue
Ready.
```

### 3.2 Verify S3 Bucket Exists
```bash
aws --endpoint-url=http://localhost:4566 s3 ls
```

**Expected output:**
```
2025-10-30 20:51:39 performance-studio-bucket
```

### 3.3 Verify SQS Queue Exists
```bash
aws --endpoint-url=http://localhost:4566 sqs list-queues
```

**Expected output:**
```json
{
    "QueueUrls": [
        "http://sqs.us-east-1.localstack:4566/000000000000/performance_studio_queue"
    ]
}
```

---

## Stage 4: Verify Indexer Startup

### 4.1 Check Indexer Logs for Metrics Publisher
```bash
docker-compose logs ch-indexer | head -20
```

**Expected output:**
```
INFO    src/main.go:48    Starting gprofiler-indexer
level=info msg="MetricsPublisher initialized: service=gprofiler-indexer, server=host.docker.internal:18126, sli_enabled=true"
DEBUG   src/main.go:93    start listening SQS queue performance_studio_queue
```

**Verification Points:**
- ✅ `MetricsPublisher initialized`
- ✅ `sli_enabled=true`
- ✅ `start listening SQS queue`
- ❌ No errors like "connection refused" or "queue not found"

---

## Stage 5: Verify Webapp Startup

### 5.1 Check Webapp is Responding
```bash
curl -s http://localhost:8080/health | head -5
```

**Expected:** HTML response (webapp is up)

### 5.2 Check Webapp Logs (No Errors)
```bash
docker-compose logs webapp --tail=20 | grep -E "(ERROR|Exception)"
```

**Expected:** No critical errors (warnings about duplicate MIME types are OK)

---

## Stage 6: Run gProfiler Agent

### 6.1 Get Service Token
```bash
docker exec gprofiler-ps-postgres psql -U performance_studio -d performance_studio -t -c \
  "SELECT token FROM tokens WHERE service_id = 1 LIMIT 1;"
```

**Example token:** `BuzKxoS1CbzPyJD0o6AEveisxWFoMYIkDznc_vfUBq8`

### 6.2 Start Agent
```bash
export GPROFILER_TOKEN="BuzKxoS1CbzPyJD0o6AEveisxWFoMYIkDznc_vfUBq8"
export GPROFILER_SERVICE="devapp"
export GPROFILER_SERVER="http://localhost:8080"

sudo ./build/x86_64/gprofiler \
  --continuous \
  --upload-results \
  --token=$GPROFILER_TOKEN \
  --service-name=$GPROFILER_SERVICE \
  --server-host=$GPROFILER_SERVER \
  --dont-send-logs \
  --server-upload-timeout 10 \
  --disable-metrics-collection \
  --profiling-duration 60 \
  --java-no-version-check \
  --nodejs-mode=attach-maps \
  --enable-heartbeat-server \
  --api-server=$GPROFILER_SERVER \
  --max-processes-runtime-profiler 10 \
  --skip-system-profilers-above 600 \
  --python-skip-pyperf-profiler-above 50 \
  --perf-mode disabled
```

### 6.3 Verify Agent Started
**Watch agent output for:**
```
INFO: gprofiler: Snapshot starting with memory usage: X.XMB
INFO: gprofiler.profilers.java: Profiling process XXXX with async-profiler
```

---

## Stage 7: Verify Profile Upload (Agent → Webapp)

### 7.1 Wait for Profile Collection
Wait ~60 seconds for the agent to collect a profile.

### 7.2 Check Agent Output for Upload Success
**Look for in agent terminal:**
```
INFO: gprofiler: Successfully uploaded profiling data to the server
```

### 7.3 Verify Webapp Received Profile
```bash
cd deploy
docker-compose logs webapp --since=2m | grep -E "(send task to queue|profiles)"
```

**Expected output:**
```
INFO: backend.routers.profiles_routes: send task to queue
```

---

## Stage 8: Verify S3 Storage (Webapp → LocalStack)

### 8.1 Check S3 Bucket for Uploaded Files
```bash
aws --endpoint-url=http://localhost:4566 s3 ls s3://performance-studio-bucket/products/devapp/stacks/
```

**Expected output:** List of `.gz` files
```
2025-10-30 20:58:25    123456 2025-10-30T20:56:10_xxxxx.gz
```

### 8.2 Verify SQS Queue Has Messages
```bash
aws --endpoint-url=http://localhost:4566 sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/performance_studio_queue \
  --attribute-names ApproximateNumberOfMessages
```

**Expected:** At least 1 message (may be 0 if indexer already processed it)

---

## Stage 9: Verify Indexer Processing (SQS → Indexer → ClickHouse)

### 9.1 Check Indexer Logs for File Processing
```bash
cd deploy
docker-compose logs ch-indexer --since=2m | grep -E "(got new file|SLI|metric)"
```

**Expected output:**
```
level=info msg="📊 Sending SLI metric: put error-budget.counters.test-sli-uuid-indexer-67890 1761857905 1 
   service=gprofiler-indexer response_type=success method_name=event_processing 
   service=devapp filename=2025-10-30T20:56:10_xxxxx.gz"
```

**Verification Points:**
- ✅ SLI metric sent
- ✅ `response_type=success`
- ✅ `method_name=event_processing`
- ✅ Service name matches: `service=devapp`
- ✅ Filename matches the S3 file

### 9.2 Check for Processing Errors (Should be None)
```bash
docker-compose logs ch-indexer --since=5m | grep -E "(Error|Failed)"
```

**Expected:** No errors related to S3 fetch, parsing, or ClickHouse insertion

---

## Stage 10: Verify ClickHouse Data Storage

### 10.1 Check Profile Samples Count
```bash
docker exec gprofiler-ps-clickhouse clickhouse-client --query \
  "SELECT COUNT(*) as profile_count, ServiceId FROM flamedb.samples WHERE ServiceId > 0 GROUP BY ServiceId FORMAT Pretty"
```

**Expected output:**
```
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ profile_count ┃ ServiceId ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│        344491 │         1 │
└───────────────┴───────────┘
```

**Verification:** `profile_count > 0`

### 10.2 Verify Latest Profile Timestamp
```bash
docker exec gprofiler-ps-clickhouse clickhouse-client --query \
  "SELECT max(Timestamp) as latest_profile FROM flamedb.samples FORMAT Pretty"
```

**Expected:** Recent timestamp (within last few minutes)

### 10.3 Check Service Details
```bash
docker exec gprofiler-ps-postgres psql -U performance_studio -d performance_studio -c \
  "SELECT id, name FROM services WHERE name = 'devapp';"
```

**Expected output:**
```
 id |  name  
----+--------
  1 | devapp
```

---

## Stage 11: Verify Metrics Publisher (Optional - If Metrics Agent Running)

### 11.1 Check SLI Metrics Were Sent
From indexer logs (already checked in Stage 9.1):
```
📊 Sending SLI metric: put error-budget.counters.test-sli-uuid-indexer-67890
```

### 11.2 Verify Metrics Format
**Graphite plaintext protocol format:**
```
put <metric_name> <timestamp> <value> tag1=value1 tag2=value2
```

**Example:**
```
put error-budget.counters.test-sli-uuid-indexer-67890 1761857905 1 
    service=gprofiler-indexer 
    response_type=success 
    method_name=event_processing 
    service=devapp 
    filename=2025-10-30T20:56:10_xxxxx.gz
```

---

## Complete Data Flow Summary

```
┌──────────────┐
│ gProfiler    │ Collects profiling data
│ Agent        │ (60 second cycles)
└──────┬───────┘
       │ HTTP POST /api/v2/profiles
       ▼
┌──────────────┐
│ Webapp       │ Receives profile
│ (Backend)    │ Stores to S3
└──────┬───────┘ Sends message to SQS
       │
       ├─────────────────────────┐
       │                         │
       ▼                         ▼
┌──────────────┐         ┌──────────────┐
│ LocalStack   │         │ LocalStack   │
│ S3           │         │ SQS Queue    │
│ (profile.gz) │         │ (message)    │
└──────────────┘         └──────┬───────┘
                                │
                                │ Polls queue
                                ▼
                         ┌──────────────┐
                         │ Indexer      │ Fetches from S3
                         │ (Go)         │ Parses profile
                         └──────┬───────┘ Sends SLI metric
                                │
                    ┌───────────┼────────────┐
                    │                        │
                    ▼                        ▼
            ┌──────────────┐         ┌──────────────┐
            │ ClickHouse   │         │ Metrics      │
            │ (Samples)    │         │ Agent        │
            │ 344k+ rows   │         │ (TCP:18126)  │
            └──────────────┘         └──────────────┘
```

---

## Troubleshooting Common Issues

### Issue 1: Bucket Name Mismatch
**Symptom:** `NoSuchBucket` error in webapp logs

**Check:**
```bash
# Compare these two:
cat deploy/.env | grep BUCKET_NAME
cat deploy/localstack_init/01_init_s3_sqs.sh | grep "mb s3"
```

**Fix:** Ensure both use `performance-studio-bucket` (hyphens, not underscores)

### Issue 2: Indexer Can't Connect to SQS
**Symptom:** `connection refused` in indexer logs

**Fix:**
```bash
# Restart indexer after LocalStack is ready
docker-compose restart ch-indexer
```

### Issue 3: Metrics Publisher Not Initialized
**Symptom:** No metrics logs in indexer

**Check:**
```bash
docker-compose logs ch-indexer | grep "MetricsPublisher"
```

**Expected:** `MetricsPublisher initialized: ... sli_enabled=true`

### Issue 4: Agent Upload Fails
**Symptom:** `500 Server Error` in agent output

**Check webapp logs:**
```bash
docker-compose logs webapp --tail=50 | grep -E "(Error|Exception)"
```

**Common causes:**
- S3 bucket doesn't exist
- AWS credentials not set (should be `test/test` for LocalStack)
- S3 endpoint not configured

---

## Success Criteria Checklist

Use this checklist to verify complete end-to-end functionality:

- [ ] All 9 Docker services are running
- [ ] LocalStack initialized S3 bucket and SQS queue
- [ ] S3 bucket name matches `.env` configuration
- [ ] Indexer shows "MetricsPublisher initialized: sli_enabled=true"
- [ ] Indexer connected to SQS queue (no connection errors)
- [ ] Agent started and collecting profiles
- [ ] Agent successfully uploaded profile (no 500 errors)
- [ ] Webapp sent task to SQS queue
- [ ] Profile file exists in S3 bucket
- [ ] Indexer processed SQS message
- [ ] Indexer sent SLI success metric
- [ ] ClickHouse contains profile samples (count > 0)
- [ ] Latest profile timestamp is recent (< 5 minutes old)

---

## Test Results

**Date:** 2025-10-30  
**Tester:** Development Testing  
**Environment:** Local Docker Compose with LocalStack

| Stage | Component | Status | Notes |
|-------|-----------|--------|-------|
| 1 | Environment Setup | ✅ PASS | Configuration verified |
| 2 | Services Startup | ✅ PASS | All 9 services running |
| 3 | LocalStack S3/SQS | ✅ PASS | Bucket and queue created |
| 4 | Indexer Startup | ✅ PASS | Metrics publisher enabled |
| 5 | Webapp Startup | ✅ PASS | Responding to health checks |
| 6 | Agent Startup | ✅ PASS | Profiling processes |
| 7 | Profile Upload | ✅ PASS | Successfully uploaded |
| 8 | S3 Storage | ✅ PASS | Profile stored in bucket |
| 9 | Indexer Processing | ✅ PASS | SLI success metric sent |
| 10 | ClickHouse Storage | ✅ PASS | 344,491 samples inserted |

**Overall Status:** ✅ **ALL TESTS PASSED**

---

## Appendix: Key Configuration Files

### A. Docker Compose Metrics Config
```yaml
# docker-compose.yml - Indexer service
ch-indexer:
  environment:
    - METRICS_ENABLED=$INDEXER_METRICS_ENABLED
    - METRICS_AGENT_URL=$INDEXER_METRICS_AGENT_URL
    - METRICS_SERVICE_NAME=$INDEXER_METRICS_SERVICE_NAME
    - METRICS_SLI_UUID=$INDEXER_METRICS_SLI_UUID
    - AWS_ENDPOINT_URL=$AWS_ENDPOINT_URL
```

### B. Environment Variables
```bash
# .env
BUCKET_NAME=performance-studio-bucket
INDEXER_METRICS_ENABLED=true
INDEXER_METRICS_SLI_UUID=test-sli-uuid-indexer-67890
AWS_ENDPOINT_URL=http://localstack:4566
```

### C. Source Code Changes
**Modified Files:**
- `src/gprofiler_indexer/metrics_publisher.go` (NEW)
- `src/gprofiler_indexer/args.go`
- `src/gprofiler_indexer/main.go`
- `src/gprofiler_indexer/worker.go`
- `src/gprofiler-dev/gprofiler_dev/config.py`
- `src/gprofiler-dev/gprofiler_dev/s3_profile_dal.py`

---

**End of Testing Guide**

