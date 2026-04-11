from pathlib import Path

from pydantic_settings import BaseSettings


def _find_env_file() -> str | None:
    candidates = [
        Path.home() / ".config" / "memodi" / ".env",
        Path(".env"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "MEMODI_",
        "env_file": _find_env_file(),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str
    db_password: str
    db_name: str = "memodi"

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
