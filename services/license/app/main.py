import asyncio
import logging
import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from app.shared.database import engine, Base
from app.shared.config import settings
from app.infrastructure.stats_cache import PublicStatsCache
from app.infrastructure.cache_aside import CacheAsideStats, STATS_KEY
from redis.exceptions import RedisError
from fastapi.responses import JSONResponse
from app.shared import datetime_utils as _datetime_utils  # noqa: F401 - register UTC JSON encoder
from app.shared.exceptions import DomainException, global_exception_handler, validation_exception_handler
from app.application.worker import check_expired_licenses_task
from app.api.client_routes import router as client_router
from app.api.user_routes import router as user_router
from app.api.admin_routes import router as admin_router
from app.api.stats_routes import router as stats_router
from app.api.bot_routes import router as bot_routes



@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.STATS_CACHE_ASIDE and settings.RUN_EXPIRY_WORKER:
        raise RuntimeError("Lab 4 strict cache requires RUN_EXPIRY_WORKER=false; HTTP mutations own invalidation")
    if settings.INITIALIZE_SCHEMA:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    expiry_task = None
    if settings.RUN_EXPIRY_WORKER:
        expiry_task = asyncio.create_task(check_expired_licenses_task())

    if settings.STATS_CACHE_ASIDE:
        cache = CacheAsideStats.from_url(settings.REDIS_URL, STATS_KEY,
                                         timeout_seconds=settings.STATS_CACHE_REDIS_TIMEOUT_SECONDS)
        cache.ttl = max(1, settings.STATS_CACHE_TTL_SECONDS)
        cache.enabled = settings.STATS_CACHE_ENABLED
        cache.coalesce = settings.STATS_CACHE_COALESCE
        cache.ttl_jitter_seconds = max(0, settings.STATS_CACHE_JITTER_SECONDS)
        cache.generation_delay_ms = max(0, settings.STATS_LAB_GENERATION_DELAY_MS)
        cache.instrument = settings.STATS_LAB_INSTRUMENT
    else:
        cache = PublicStatsCache.from_url(settings.REDIS_URL, "mtgmods:license:public_stats:v1")
    app.state.public_stats_cache = cache
    try:
        yield
    finally:
        if expiry_task is not None:
            expiry_task.cancel()
            await asyncio.gather(expiry_task, return_exceptions=True)
        await app.state.public_stats_cache.aclose()
        await engine.dispose()

app = FastAPI(
    title="License Service",
    description="Core Micro SaaS System for License Management",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "https://mtgmods.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.add_exception_handler(DomainException, global_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

INSTANCE_ID = settings.INSTANCE_ID or socket.gethostname()


@app.middleware("http")
async def add_instance_id_header(request, call_next):
    delay_ms = max(0, settings.SYNTHETIC_DELAY_MS)
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)
    cache = getattr(app.state, "public_stats_cache", None)
    mutation = (
        isinstance(cache, CacheAsideStats)
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path.startswith("/api/v1/license/")
    )
    token = None
    if mutation:
        try:
            token = await cache.begin_write()
        except RedisError:
            response = JSONResponse(
                status_code=503,
                content={"detail": "Redis unavailable: mutation not started; statistics remain readable"},
            )
            response.headers["X-Instance-ID"] = INSTANCE_ID
            response.headers["X-Cache-Invalidation"] = "UNAVAILABLE"
            return response
    try:
        response = await call_next(request)
    finally:
        invalidated = False
        if token is not None:
            try:
                await cache.end_write(token)
                invalidated = True
            except RedisError:
                # Marker intentionally stays present. Recovery reads bypass
                # Redis until an operator clears it with writers quiesced.
                logging.getLogger(__name__).warning("Mutation completed but dirty marker retained")
    if mutation:
        response.headers["X-Cache-Invalidation"] = "INVALIDATED" if invalidated else "DIRTY"
    response.headers["X-Instance-ID"] = INSTANCE_ID
    response.headers["X-Instance-Delay-Ms"] = str(delay_ms)
    return response

app.include_router(client_router)
app.include_router(user_router)
app.include_router(stats_router)
app.include_router(admin_router)
app.include_router(bot_routes)

@app.get("/health/live", tags=["System"])
async def liveness_check():
    return {
        "status": "UP",
        "service": "License Service",
        "instance_id": INSTANCE_ID,
    }


@app.get("/health", tags=["System"])
async def readiness_check():
    database = settings.DATABASE_POSTGRES_URL if not settings.DEBUG_MODE else settings.DATABASE_URL
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc

    return {
        "status": "UP",
        "service": "License Service",
        "instance_id": INSTANCE_ID,
        "version": settings.APP_VERSION,
        "database": database.split("://")[0],
        "database_status": "UP",
    }
