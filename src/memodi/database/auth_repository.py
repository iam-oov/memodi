import hashlib
import secrets

from psycopg.errors import UniqueViolation

from memodi.database.connection import get_connection

API_KEY_PREFIX = "mmd_"


def _generate_api_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def create_user(email: str) -> dict:
    api_key = _generate_api_key()
    api_key_hash = _hash_api_key(api_key)

    conn = get_connection()
    try:
        row = conn.execute(
            """
            INSERT INTO users (email, api_key_hash)
            VALUES (%s, %s)
            RETURNING id, email, created_at
            """,
            (email, api_key_hash),
        ).fetchone()
    except UniqueViolation as e:
        conn.rollback()
        if e.diag.constraint_name == "users_email_key":
            raise ValueError(f"User with email '{email}' already exists") from e
        raise
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    result = dict(row)
    result["api_key"] = api_key
    return result


def get_user_by_api_key(api_key: str) -> dict | None:
    api_key_hash = _hash_api_key(api_key)
    conn = get_connection()
    row = conn.execute(
        "SELECT id, email, created_at FROM users WHERE api_key_hash = %s",
        (api_key_hash,),
    ).fetchone()
    return dict(row) if row else None
