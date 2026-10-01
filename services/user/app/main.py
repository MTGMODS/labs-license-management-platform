import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.shared.database import engine, Base
from app.shared import datetime_utils as _datetime_utils  # noqa: F401 - register UTC JSON encoder
from app.shared.exceptions import DomainException, global_exception_handler, validation_exception_handler
from app.shared.config import settings
from app.application.worker import cleanup_auth_artifacts_once, cleanup_auth_artifacts_task
from app.api.auth_routes import router as auth_router
from app.api.user_routes import router as user_router
from app.api.admin_routes import router as admin_router
from app.api.s2s_routes import router as s2s_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await cleanup_auth_artifacts_once()
    cleanup_task = asyncio.create_task(cleanup_auth_artifacts_task())
    yield
    cleanup_task.cancel()
    await asyncio.gather(cleanup_task, return_exceptions=True)
    await engine.dispose()

app = FastAPI(
    title="User Service",
    description="Microservice for managing user accounts and authentication",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_exception_handler(DomainException, global_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(s2s_router)
app.include_router(admin_router)

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
        "service": "Users Service",
        "version": settings.APP_VERSION,
        "database": database.split("://")[0]
    }
