from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, Response, Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.shared.database import get_db
from app.domain.schemas import GeneratePurchaseDTO, UpdateLicenseDTO
from app.application.service import LicenseService
from app.application.jwt_utils import get_admin_user_id
from app.shared.exceptions import DomainException, global_exception_handler

router = APIRouter(prefix="/api/v1/license", tags=["Admin Panel"])

@router.post("/generate", description="Generate a new unactivated license key")
async def generate_new_key(payload: GeneratePurchaseDTO, admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    payload.count = 1
    service = LicenseService(db)
    keys, tx_ids = await service.generate_and_bill(payload)
    return {"status": "success", "data": {"key": keys[0], "transaction_id": tx_ids[0]}}

@router.post("/generate/bulk", description="Mass generate unactivated license keys")
async def generate_bulk_keys(payload: GeneratePurchaseDTO, admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    service = LicenseService(db)
    keys, _tx_ids = await service.generate_and_bill(payload)
    return {
        "status": "success",
        "count": len(keys),
        "data": {"keys": keys}
    }

@router.get("/find", description="Find licenses by user_id or key")
async def admin_find_licenses(
    user_id: Optional[int] = Query(None, description="Search by owner User ID"),
    key: Optional[str] = Query(None, description="Search by exact License Key"),
    admin_id: int = Depends(get_admin_user_id),
    db: AsyncSession = Depends(get_db)
):
    service = LicenseService(db)
    result = await service.admin_find_licenses(user_id=user_id, key=key)
    return {"status": "success", "data": result}

@router.get("/{license_id}", description="Get a specific license by ID")
async def admin_get_license(request: Request, response: Response, license_id: int = Path(ge=1, le=2147483647), admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    service = LicenseService(db)
    cache = getattr(request.app.state, 'negative_license_cache', None)
    if cache is None or not cache.enabled:
        result = await service.admin_get_license(license_id)
        return {"status": "success", "data": result}
    async def lookup():
        try:
            return await service.admin_get_license(license_id)
        except DomainException as exc:
            if exc.status_code == 404 and exc.error_code == 'NOT_FOUND':
                return None
            raise
    result, source = await cache.read(license_id, lookup)
    response.headers['X-Negative-Cache'] = source
    if result is None:
        response = await global_exception_handler(request, DomainException('License not found', 404, 'NOT_FOUND'))
        response.headers['X-Negative-Cache'] = source
        return response
    return {"status": "success", "data": result}

@router.patch("/{license_id}", description="Update license status, owner, duration, amount, reset limits, or max devices")
async def admin_update_license(license_id: int, payload: UpdateLicenseDTO, admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    service = LicenseService(db)
    result = await service.admin_update_license(license_id, payload)
    return {"status": "success", "message": "License updated successfully", "data": result}

@router.delete("/{license_id}", description="Delete a license with its purchase and device activations")
async def admin_delete_license(license_id: int, admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    service = LicenseService(db)
    await service.admin_delete_license(license_id)
    return {"status": "success", "message": "License deleted successfully"}

@router.delete("/devices/{device_id}", description="Manually remove a HWID lock (Admin override)")
async def admin_reset_device(device_id: int, admin_id: int = Depends(get_admin_user_id), db: AsyncSession = Depends(get_db)):
    service = LicenseService(db)
    await service.remove_device_admin(device_id)
    return {"status": "success", "message": "Device HWID removed manually."}
