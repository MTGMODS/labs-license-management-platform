import asyncio

from app.infrastructure import repository as _repository  # noqa: F401 - register models
from app.shared.database import Base, engine


async def initialize_schema() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(initialize_schema())
