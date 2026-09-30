from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.shared.database import engine, Base
from app.shared import datetime_utils as _datetime_utils
from app.shared.exceptions import DomainException, global_exception_handler, validation_exception_handler
from app.shared.config import settings
from app.infrastructure.stats_cache import PublicStatsCache
from app.api.routes import router as usage_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.public_stats_cache = PublicStatsCache.from_url(
        settings.REDIS_URL, "mtgmods:usage:public_stats:v1",
    )
    try:
        yield
    finally:
        await app.state.public_stats_cache.aclose()
        await engine.dispose()

app = FastAPI(
    title="Usage Analytics Service",
    description="Microservice for tracking launches",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_exception_handler(DomainException, global_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(usage_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "https://mtgmods.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/health", tags=["System"])
async def health_check():
    database = settings.DATABASE_POSTGRES_URL if not settings.DEBUG_MODE else settings.DATABASE_URL
    return {
        "status": "UP",
        "service": "Usage Analytics",
        "version": settings.APP_VERSION,
        "database": database.split("://")[0]
    }
