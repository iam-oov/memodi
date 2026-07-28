from memodi.database.connection import ensure_schema, get_connection

INDEX_NAME = "idx_obs_superseded_by"


def test_superseded_by_partial_index_exists():
    """get_observation resolves the reverse pointer by filtering on
    superseded_by; unindexed that is a Seq Scan over rows carrying an inline
    384d embedding, on every successful audit read."""
    ensure_schema()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'observations' AND indexname = %s
        """,
        (INDEX_NAME,),
    ).fetchone()

    assert row is not None, f"missing index {INDEX_NAME} on observations"
    assert "superseded_by" in row["indexdef"]
    assert "superseded_by IS NOT NULL" in row["indexdef"]
