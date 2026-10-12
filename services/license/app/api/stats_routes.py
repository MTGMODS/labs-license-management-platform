from fastapi import APIRouter, BackgroundTasks, Request, Response
from app.shared.tariffs import public_tariffs
from app.application.service import LicenseStatsService
from app.infrastructure.cache_aside import CacheAsideStats

router = APIRouter(prefix="/api/v1/license", tags=["Stats"])

@router.get("/stats/public", description="Public subscription sales analytics + forever legacy block")
async def get_public_stats(request: Request, response: Response, background_tasks: BackgroundTasks):
    service = LicenseStatsService(request.app.state.public_stats_cache)
    if isinstance(service.cache, CacheAsideStats):
        result = await service.cache.read(service._load_stats)
        response.headers["X-Cache"] = result.source
        response.headers["X-Cache-Key"] = service.cache.key
        response.headers["X-Cache-TTL"] = str(service.cache.ttl)
        return result.data
    return await service.get_website_stats(background_tasks)

@router.get("/tariffs", description="Catalog prices and per-plan device limits for the site and bots.")
async def get_tariffs():
    return {"status": "success", "data": public_tariffs()}
