import psycopg
import pytest

from memodi.database.connection import ensure_schema, get_connection, run_migration


@pytest.fixture(autouse=True)
def setup_schema():
    ensure_schema()


def test_run_migration_failure_reports_filename_and_rolls_back(tmp_path):
    bad = tmp_path / "099_broken_migration.sql"
    bad.write_text("THIS IS NOT VALID SQL;")
    conn = get_connection()
    try:
        with pytest.raises(Exception) as excinfo:
            run_migration(str(bad))
        assert "099_broken_migration.sql" in str(excinfo.value)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    finally:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
