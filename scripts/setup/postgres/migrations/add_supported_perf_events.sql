-- Migration script to add supported_perf_events column to hostheartbeats table
-- This allows the backend to store and validate PMU event support per host

-- Add the column (idempotent - won't fail if column already exists)
ALTER TABLE hostheartbeats 
ADD COLUMN IF NOT EXISTS supported_perf_events text[] NULL;

-- Verify the column was added
SELECT 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns 
WHERE table_name = 'hostheartbeats' 
  AND column_name = 'supported_perf_events';

-- Expected output:
--  column_name            | data_type | is_nullable
-- ------------------------+-----------+-------------
--  supported_perf_events  | ARRAY     | YES
