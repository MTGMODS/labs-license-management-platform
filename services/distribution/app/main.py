import asyncio
import aio_pika

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as download_router
from app.application.worker import cleanup_old_files_task
from app.infrastructure.messaging import process_message
from app.shared.config import settings
from app.shared import datetime_utils as _datetime_utils  # noqa: F401 - register UTC JSON encoder
from app.shared.exceptions import DomainException, global_exception_handler, validation_exception_handler


RABBITMQ_RECONNECT_DELAY_SECONDS = 5

async def consume_file_generation_events(app: FastAPI):
    while True:
        connection = None
        try:
            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            channel = await connection.channel()
            queue = await channel.declare_queue("file_generation_queue", durable=True)
            await queue.consume(process_message)
            app.state.rabbitmq_connected = True
            print("[Distribution] Connected to RabbitMQ. Waiting for tasks...")
            await asyncio.Future()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            app.state.rabbitmq_connected = False
            print(f"[Distribution] RabbitMQ connection failed: {e}. Retrying in {RABBITMQ_RECONNECT_DELAY_SECONDS}s")
            await asyncio.sleep(RABBITMQ_RECONNECT_DELAY_SECONDS)
        finally:
            if connection and not connection.is_closed:
                await connection.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.rabbitmq_connected = False
    cleanup_task = asyncio.create_task(cleanup_old_files_task())
    rabbitmq_task = asyncio.create_task(consume_file_generation_events(app))
    try:
        yield
    finally:
        cleanup_task.cancel()
        rabbitmq_task.cancel()
        await asyncio.gather(cleanup_task, rabbitmq_task, return_exceptions=True)


app = FastAPI(
    title="Distribution Service",
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

app.include_router(download_router)


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "UP",
        "service": "Distribution Service",
        "version": settings.APP_VERSION,
        "state": "Stateless (No DB)",
        "rabbitmq": "CONNECTED" if app.state.rabbitmq_connected else "RECONNECTING"
    }
