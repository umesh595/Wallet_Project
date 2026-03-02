from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    DATABASE_URL: str
    LOG_LEVEL: str
    APP_NAME: str
    DB_LOCK_TIMEOUT: int = 5
    DB_MAX_DEADLOCK_RETRIES: int = 3
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True
    )
settings = Settings()