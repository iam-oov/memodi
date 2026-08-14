import hashlib
import secrets

import psycopg
from psycopg.errors import UniqueViolation

from memodi.database.connection import get_connection

API_KEY_PREFIX = "mmd_"


def _generate_api_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _insert_api_key(conn: psycopg.Connection, user_id: str) -> str:
    api_key = _generate_api_key()
    conn.execute(
        "INSERT INTO api_keys (user_id, key_hash) VALUES (%s, %s)",
        (user_id, _hash_api_key(api_key)),
    )
    return api_key


def create_api_key(user_id: str) -> str:
    conn = get_connection()
    try:
        api_key = _insert_api_key(conn, user_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return api_key


def create_user(email: str) -> dict:
    normalized = email.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            INSERT INTO users (email)
            VALUES (%s)
            RETURNING id, email, created_at
            """,
            (normalized,),
        ).fetchone()
        api_key = _insert_api_key(conn, row["id"])
    except UniqueViolation as e:
        conn.rollback()
        if e.diag.constraint_name == "users_email_key":
            raise ValueError(f"User with email '{normalized}' already exists") from e
        raise
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    result = dict(row)
    result["api_key"] = api_key
    return result


def login_with_email(email: str) -> dict:
    normalized = email.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            INSERT INTO users (email)
            VALUES (%s)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id, email, created_at
            """,
            (normalized,),
        ).fetchone()
        api_key = _insert_api_key(conn, row["id"])
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    result = dict(row)
    result["api_key"] = api_key
    return result


def revoke_api_key(api_key: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM api_keys WHERE key_hash = %s",
            (_hash_api_key(api_key),),
        )
        revoked = cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return revoked


def get_user_by_api_key(api_key: str) -> dict | None:
    api_key_hash = _hash_api_key(api_key)
    conn = get_connection()
    row = conn.execute(
        """
        SELECT users.id, users.email, users.created_at
        FROM users
        JOIN api_keys ON api_keys.user_id = users.id
        WHERE api_keys.key_hash = %s
        """,
        (api_key_hash,),
    ).fetchone()
    return dict(row) if row else None
