from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    REDIS_URL: str = "redis://redis:6379/0"
    DATABASE_URL: str
    DATABASE_POSTGRES_URL: str
    RABBITMQ_URL: str
    JWT_SECRET: str
    BOT_SECRET_TOKEN: str
    USER_SERVICE_URL: str
    INTERNAL_SECRET_TOKEN: str
    API_VERSION: str
    APP_VERSION: str
    DEBUG_MODE: bool
    INSTANCE_ID: str | None = None
    INITIALIZE_SCHEMA: bool = True
    RUN_EXPIRY_WORKER: bool = True
    SYNTHETIC_DELAY_MS: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
