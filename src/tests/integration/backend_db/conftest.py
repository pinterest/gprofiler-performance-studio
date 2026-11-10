import psycopg2
import pytest


@pytest.fixture(scope="session")
def postgres_connection(pytestconfig):
    """Autocommit PostgreSQL connection for direct row inspection."""
    conn = psycopg2.connect(
        host=pytestconfig.getoption("--postgres-host"),
        port=pytestconfig.getoption("--postgres-port"),
        user=pytestconfig.getoption("--postgres-user"),
        password=pytestconfig.getoption("--postgres-password"),
        database=pytestconfig.getoption("--postgres-db"),
    )
    conn.autocommit = True
    yield conn
    conn.close()
