import asyncio
import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from app.shared.database import engine, Base
from app.shared.config import settings
from app.infrastructure.stats_cache import PublicStatsCache
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
    if settings.INITIALIZE_SCHEMA:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    expiry_task = None
    if settings.RUN_EXPIRY_WORKER:
        expiry_task = asyncio.create_task(check_expired_licenses_task())

    app.state.public_stats_cache = PublicStatsCache.from_url(
        settings.REDIS_URL, "mtgmods:license:public_stats:v1",
    )
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
    response = await call_next(request)
    response.headers["X-Instance-ID"] = INSTANCE_ID
    return response

app.include_router(client_router)
app.include_router(user_router)
app.include_router(stats_router)
app.include_router(admin_router)
app.include_router(bot_routes)

@app.get("/health", tags=["System"])
async def health_check():
    database = settings.DATABASE_POSTGRES_URL if not settings.DEBUG_MODE else settings.DATABASE_URL
    return {
        "status": "UP",
        "service": "License Service",
        "instance_id": INSTANCE_ID,
        "version": settings.APP_VERSION,
        "database": database.split("://")[0]
    }
