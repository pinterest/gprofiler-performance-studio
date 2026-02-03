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
