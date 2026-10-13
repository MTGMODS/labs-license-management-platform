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
    STATS_CACHE_ASIDE: bool = False
    STATS_CACHE_TTL_SECONDS: int = 300
    STATS_CACHE_ENABLED: bool = True
    STATS_CACHE_COALESCE: bool = True
    STATS_CACHE_JITTER_SECONDS: int = 0
    STATS_LAB_GENERATION_DELAY_MS: int = 0
    STATS_LAB_INSTRUMENT: bool = False
    STATS_CACHE_REDIS_TIMEOUT_SECONDS: float = 0.5
    NEGATIVE_CACHE_ENABLED: bool = False
    NEGATIVE_CACHE_TTL_SECONDS: int = 15
    LAB8_EXPERIMENTS_ENABLED: bool = False
    LAB8_AGGREGATE_TTL_SECONDS: int = 30
    LAB8_AGGREGATE_JITTER_SECONDS: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
