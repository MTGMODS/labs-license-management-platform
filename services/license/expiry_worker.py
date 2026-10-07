import asyncio

from app.application.worker import check_expired_licenses_task
from app.shared.database import engine


async def run() -> None:
    try:
        await check_expired_licenses_task()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
