# Deployment Checklist for Heartbeat Profiling System

This checklist covers what needs to be done to deploy the heartbeat profiling system.

## ✅ Completed Implementation

### Backend (Performance Studio)
- [x] API endpoints implemented (`/profile_request`, `/heartbeat`, `/command_completion`)
- [x] Database methods implemented in DBManager
- [x] Pydantic models defined for all request/response types
- [x] Error handling and logging implemented
- [x] Router registration in FastAPI app completed

### Database Schema
- [x] SQL migration files created
- [x] Database functions defined
- [x] Indexes and triggers created
- [x] Enum types defined

### Agent (gprofiler)
- [x] Heartbeat loop implementation
- [x] Command processing logic
- [x] Idempotency handling
- [x] Status reporting

## 🔧 Deployment Steps Required

### 1. Database Migration
Run the following SQL files on your PostgreSQL database:
```bash
# Apply the schema migrations
psql -d your_database -f scripts/setup/postgres/add_profiling_tables.sql
psql -d your_database -f scripts/setup/postgres/add_heartbeat_system_tables.sql
```

### 2. Backend Deployment
The backend code is ready to deploy. No additional configuration needed.

### 3. Agent Deployment
Deploy the updated gprofiler agent code with heartbeat functionality.

## 🧪 Testing Steps

### 1. Test Backend APIs
Use the test script to validate the backend:
```bash
cd /home/prashantpatel/code/pinterest-opensource
python test_heartbeat_system.py
```

### 2. Test Agent Integration
Run the agent simulator:
```bash
cd /home/prashantpatel/code/pinterest-opensource  
python run_heartbeat_agent.py
```

### 3. End-to-End Testing
1. Start the backend service
2. Run database migrations
3. Submit a profiling request via `/api/metrics/profile_request`
4. Start an agent (or simulator)
5. Verify agent receives command via heartbeat
6. Verify command completion is reported

## 📋 Configuration

### Environment Variables
No new environment variables are required. The system uses existing database configurations.

### Database Connection
Ensure PostgreSQL connection is properly configured in your existing DBManager setup.

## 🔍 Monitoring

### Logs to Monitor
- Profiling request submissions
- Heartbeat processing
- Command distribution to agents  
- Command completion status
- Database operation errors

### Key Metrics
- Number of pending vs completed profiling requests
- Agent heartbeat frequency and health
- Command execution success rates
- Database query performance

## 🚨 Potential Issues

### 1. Database Schema Conflicts
If you have existing `ProfilingRequests` table, you may need to modify the migration scripts to update the existing table instead of creating new ones.

### 2. Agent Compatibility
Ensure all agents are updated to support the new heartbeat protocol before submitting profiling requests.

### 3. Database Permissions
Ensure the application database user has permissions to:
- Create/update/delete records in the new tables
- Execute the new functions
- Use array operations and UUID types

## 📖 API Documentation

### Endpoints Available:
- `POST /api/metrics/profile_request` - Submit profiling requests
- `POST /api/metrics/heartbeat` - Agent heartbeat and command polling  
- `POST /api/metrics/command_completion` - Report command execution status

### API Documentation:
FastAPI automatic documentation available at `/api/v1/docs` when the service is running.

## 🔄 Rollback Plan

If needed, rollback steps:

1. **Database Rollback**: Remove the new tables and functions
2. **Code Rollback**: Deploy previous version without heartbeat functionality
3. **Agent Rollback**: Deploy agents without heartbeat support

## ✅ Success Criteria

The deployment is successful when:
- [ ] Database migrations complete without errors
- [ ] Backend service starts and health check passes
- [ ] API endpoints respond correctly to test requests
- [ ] Agents can successfully poll for commands via heartbeat
- [ ] End-to-end profiling workflow works (request → command → execution → completion)
- [ ] All logs show expected behavior without errors

## 📞 Support

For issues during deployment:
1. Check application logs for errors
2. Verify database connectivity and permissions
3. Ensure all migration scripts have been executed
4. Validate agent and backend can communicate
5. Check API endpoint accessibility and responses
