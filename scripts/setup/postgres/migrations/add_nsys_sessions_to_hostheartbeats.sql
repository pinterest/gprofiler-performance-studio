-- Sessions reported by agents from `nsys sessions list`, so the profiling page
-- can offer them in a dropdown. NULL means this host has not reported yet.

ALTER TABLE hostheartbeats
ADD COLUMN IF NOT EXISTS nsys_sessions jsonb NULL;
