from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "MEMODI_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_ignore_empty": True,
        "extra": "ignore",
    }

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "memodi"
    db_password: str = "memodi_dev"
    db_name: str = "memodi"
    workspace: str | None = None

    @field_validator("workspace", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        if v == "":
            return None
        return v

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
