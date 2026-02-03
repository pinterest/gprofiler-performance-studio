--
-- Migration: Create AdhocFlamegraphMetadata table
-- Purpose: Store metadata for adhoc profiling flamegraphs including PMU events
-- Author: Performance Studio Team
-- Date: 2026-02-02
--
-- This table stores metadata for adhoc flamegraph HTML files created by the indexer.
-- The indexer populates this table after successfully creating and uploading the HTML file.
-- This ensures data consistency: metadata exists only when the HTML file exists.
--

-- Create table for adhoc flamegraph metadata
CREATE TABLE IF NOT EXISTS AdhocFlamegraphMetadata (
    ID bigserial PRIMARY KEY,
    service_id bigint NOT NULL,
    hostname text NOT NULL,
    s3_key text NOT NULL UNIQUE,
    perf_events text[],
    start_time timestamp NOT NULL,
    end_time timestamp NOT NULL,
    file_size bigint,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_adhoc_flamegraph_service 
        FOREIGN KEY (service_id) 
        REFERENCES Services(ID) 
        ON DELETE CASCADE
);

-- Create indexes for efficient querying
-- Index for listing flamegraphs by service and time (most common query)
CREATE INDEX IF NOT EXISTS idx_adhoc_metadata_service_time 
ON AdhocFlamegraphMetadata(service_id, start_time DESC);

-- Index for looking up metadata by S3 key (used by indexer for upserts)
CREATE INDEX IF NOT EXISTS idx_adhoc_metadata_s3_key 
ON AdhocFlamegraphMetadata(s3_key);

-- Index for filtering by hostname
CREATE INDEX IF NOT EXISTS idx_adhoc_metadata_hostname 
ON AdhocFlamegraphMetadata(hostname);

-- Add table and column comments for documentation
COMMENT ON TABLE AdhocFlamegraphMetadata IS 
'Stores metadata for adhoc profiling flamegraph HTML files. Populated by the indexer after successful HTML creation to ensure data consistency.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.service_id IS 
'Foreign key to Services table. Identifies which service this flamegraph belongs to.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.hostname IS 
'Hostname where the profile was collected. Used for filtering and display.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.s3_key IS 
'Full S3 path to the flamegraph HTML file (e.g., products/service/stacks/flamegraph/2026-02-02T12:00:00Z_abc123_hostname_adhoc_flamegraph.html)';

COMMENT ON COLUMN AdhocFlamegraphMetadata.perf_events IS 
'Array of perf event names used during profiling (e.g., {cycles, instructions, cache-misses}). NULL for profiles without PMU events.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.start_time IS 
'Profile collection start timestamp. Used for time-based filtering and display.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.end_time IS 
'Profile collection end timestamp. Typically same as start_time for adhoc profiles.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.file_size IS 
'Size of the flamegraph HTML file in bytes. Used for display and monitoring.';

COMMENT ON COLUMN AdhocFlamegraphMetadata.created_at IS 
'Timestamp when this metadata record was created by the indexer. Reflects when the HTML file was successfully uploaded to S3.';
