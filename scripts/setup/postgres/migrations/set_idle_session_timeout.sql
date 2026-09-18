-- Reap idle DB connections for the webapp DB role.
--
-- The webapp uses per-thread connections (GPROFILER_POSTGRES_CONN_PER_THREAD=TRUE): each
-- worker thread keeps its own connection, so the connection count tracks demand but the
-- high-water is retained after a burst. psycopg2 has no client-side idle timeout, so we
-- reap idle sessions on the server: PostgreSQL closes connections left idle beyond the
-- timeout, and a thread simply reopens on its next query (PostgresDB.execute retries the
-- reconnect). This keeps the footprint (and Aurora ACU) proportional to recent activity
-- while still allowing organic fleet growth and bursts.
--
-- Scope: the gprofileradmin role only (does not touch rdsadmin/others). Only closes IDLE
-- sessions -- never active queries or open transactions. Takes effect for new sessions.
-- Tune the interval: long enough to avoid churning connections about to be reused, short
-- enough to release idle ones after a burst subsides.

ALTER ROLE gprofileradmin SET idle_session_timeout = '5min';
