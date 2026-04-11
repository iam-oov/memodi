from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MEMODI_"}

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
