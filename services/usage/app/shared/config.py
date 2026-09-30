from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    REDIS_URL: str = "redis://redis:6379/0"
    DATABASE_URL: str
    DATABASE_POSTGRES_URL: str
    API_VERSION: str
    APP_VERSION: str
    DEBUG_MODE: bool

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
