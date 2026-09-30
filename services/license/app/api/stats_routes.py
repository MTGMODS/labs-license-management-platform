from fastapi import APIRouter, BackgroundTasks, Request
from app.shared.tariffs import public_tariffs
from app.application.service import LicenseStatsService

router = APIRouter(prefix="/api/v1/license", tags=["Stats"])

@router.get("/stats/public", description="Public subscription sales analytics + forever legacy block")
async def get_public_stats(request: Request, background_tasks: BackgroundTasks):
    service = LicenseStatsService(request.app.state.public_stats_cache)
    return await service.get_website_stats(background_tasks)

@router.get("/tariffs", description="Catalog prices and per-plan device limits for the site and bots.")
async def get_tariffs():
    return {"status": "success", "data": public_tariffs()}
