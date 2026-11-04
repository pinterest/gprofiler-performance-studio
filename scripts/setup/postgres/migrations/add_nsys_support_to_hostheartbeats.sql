ALTER TABLE hostheartbeats
    ADD COLUMN IF NOT EXISTS nsys_available boolean NULL,
    ADD COLUMN IF NOT EXISTS nsys_path text NULL;
