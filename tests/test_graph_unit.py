"""Unit tests for memodi.database.graph that don't require a DB connection."""

from __future__ import annotations

import os

# memodi.config builds Settings() at import time and requires DB env vars.
# These unit tests don't touch the DB, so dummy values are enough to let
# the import chain resolve.
os.environ.setdefault("MEMODI_DB_USER", "test_user")
os.environ.setdefault("MEMODI_DB_PASSWORD", "test_password")

from unittest.mock import MagicMock

import psycopg
import pytest

from memodi.database.graph import _prepare_connection


class TestPrepareConnection:
    def test_happy_path_load_succeeds(self) -> None:
        """Local Docker dev: memodi user is superuser, LOAD succeeds, no rollback."""
        conn = MagicMock(spec=psycopg.Connection)
        conn.execute.return_value = MagicMock()

        _prepare_connection(conn)

        assert conn.execute.call_count == 2
        conn.rollback.assert_not_called()
        assert "LOAD 'age'" in conn.execute.call_args_list[0].args[0]
        assert "search_path" in conn.execute.call_args_list[1].args[0]

    def test_swallows_insufficient_privilege_on_load(self) -> None:
        """Hetzner production: memodi user is not superuser, but AGE is
        preloaded via session_preload_libraries. The explicit LOAD fails with
        InsufficientPrivilege — we rollback and continue with SET search_path."""
        conn = MagicMock(spec=psycopg.Connection)

        def execute_side_effect(sql: str):
            if "LOAD" in sql:
                raise psycopg.errors.InsufficientPrivilege(
                    'access to library "age" is not allowed'
                )
            return MagicMock()

        conn.execute.side_effect = execute_side_effect

        _prepare_connection(conn)

        assert conn.execute.call_count == 2
        conn.rollback.assert_called_once()

    def test_propagates_other_errors(self) -> None:
        """Errors other than InsufficientPrivilege must propagate."""
        conn = MagicMock(spec=psycopg.Connection)
        conn.execute.side_effect = psycopg.errors.UndefinedObject(
            "something unexpected"
        )

        with pytest.raises(psycopg.errors.UndefinedObject):
            _prepare_connection(conn)

    def test_search_path_runs_even_after_load_rollback(self) -> None:
        """After LOAD fails + rollback, SET search_path must still execute."""
        conn = MagicMock(spec=psycopg.Connection)
        sql_executed: list[str] = []

        def execute_side_effect(sql: str):
            sql_executed.append(sql)
            if "LOAD" in sql:
                raise psycopg.errors.InsufficientPrivilege("denied")
            return MagicMock()

        conn.execute.side_effect = execute_side_effect

        _prepare_connection(conn)

        assert any("LOAD" in s for s in sql_executed)
        assert any("search_path" in s for s in sql_executed)
