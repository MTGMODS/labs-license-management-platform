from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from app.infrastructure.repository import LaunchRepository
from app.infrastructure.stats_cache import PublicStatsCache
from app.shared.database import AsyncSessionLocal


class UsageService:
    def __init__(self, db: AsyncSession):
        self.repo = LaunchRepository(db)

    async def log_launch(self, version: str, hwid: str, server: int, device: str, mode: str):
        await self.repo.save(version=version, hwid=hwid, device=device, server=server, mode=mode)
        return {"status": "success", "message": "Launch logged"}


class UsageStatsService:
    def __init__(self, cache: PublicStatsCache):
        self.cache = cache

    async def get_website_stats(self, background_tasks: BackgroundTasks):
        return await self.cache.get(self._load_stats, background_tasks)

    @staticmethod
    async def _load_stats():
        # A refresh owns its DB session, including when it runs after the response.
        async with AsyncSessionLocal() as db:
            return await LaunchRepository(db).get_heavy_public_stats()
