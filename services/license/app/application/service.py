import secrets
import string
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks

from app.shared.database import AsyncSessionLocal
from app.shared.datetime_utils import format_utc, to_utc
from app.domain.models import LicenseStatus
from app.domain.schemas import GeneratePurchaseDTO, ActivateKeyDTO, UpdateLicenseDTO
from app.infrastructure.repository import LicenseRepository, TransactionRepository, LicenseModel, TransactionModel
from app.shared.exceptions import DomainException
from app.infrastructure.external_clients import UserServiceClient
from app.infrastructure.stats_cache import PublicStatsCache

class LicenseService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.license_repo = LicenseRepository(db)
        self.tx_repo = TransactionRepository(db)
        self.user_client = UserServiceClient()

    async def check_for_client(self, key: str, device: str, ip_address: str, user_agent: str) -> tuple[int, dict]:
        license_row = await self.license_repo.get_by_key(key)
        if not license_row:
            return 404, {"detail": "Key not found"}

        if license_row.status == LicenseStatus.NOT_ACTIVATED or license_row.user_id is None:
            return 200, {"valid": False, "expires": False, "error": "NOT_ACTIVATED"}

        now = datetime.now(timezone.utc)
        expired = (
            license_row.status in (LicenseStatus.EXPIRED, LicenseStatus.BANNED)
            or (
                license_row.expires_at is not None
                and to_utc(license_row.expires_at) < now
            )
        )
        if expired or license_row.status != LicenseStatus.ACTIVE:
            return 200, {"valid": False, "expires": True}

        known_devices = [d.device for d in license_row.devices]
        if device not in known_devices and len(known_devices) >= license_row.max_devices:
            return 403, {
                "error": "HWID_LIMIT_REACHED",
                "message": f"Device limit reached (Max {license_row.max_devices}). Reset HWID in profile.",
            }

        await self.license_repo.log_device(
            license_row.id, device, ip_address, user_agent
        )

        profile = await self.user_client.get_user_profile(license_row.user_id)
        if not profile:
            return 400, {"valid": False, "error": "Internal server error"}

        return 200, {
            "valid": True,
            "user": profile["nickname"],
            "id": license_row.user_id,
        }

    def _serialize_dashboard_license(self, db_sub: LicenseModel) -> dict:
        devices_list = []
        for d in db_sub.devices:
            hwid_masked = f"{d.device[:3]}******{d.device[-3:]}" if len(d.device) > 6 else "***"
            devices_list.append({
                "id": d.id,
                "hwid": hwid_masked,
                "ip": d.ip_address,
                "first_used_at": format_utc(d.first_used_at),
                "last_used_at": format_utc(d.last_used_at),
            })

        tx = db_sub.transaction
        return {
            "license": {
                "id": db_sub.id,
                "user_id": db_sub.user_id,
                "key": db_sub.key,
                "status": db_sub.status.value,
                "duration_days": db_sub.duration_days,
                "max_devices": db_sub.max_devices,
                "reset_limit": db_sub.reset_limit,
                "activated_at": format_utc(db_sub.activated_at),
                "expires_at": format_utc(db_sub.expires_at),
            },
            "devices": devices_list,
            "transaction": {
                "amount": tx.amount,
                "method": tx.payment_method,
                "status": tx.status,
                "purchased_at": format_utc(tx.purchased_at),
            } if tx else None,
        }

    async def get_license_info(self, user_id: int) -> dict:
        db_sub = await self.license_repo.get_active_by_user(user_id)
        if not db_sub:
            raise DomainException(
                message="You don't have an active license.",
                status_code=404,
                error_code="NO_ACTIVE_LICENSE",
            )
        return self._serialize_dashboard_license(db_sub)

    async def get_license_history(self, user_id: int) -> list:
        licenses = await self.license_repo.get_history_by_user(user_id)
        return [self._serialize_dashboard_license(lic) for lic in licenses]

    async def reset_device(self, user_id: int, device_id: int):
        db_sub = await self.license_repo.get_active_by_user(user_id)
        if not db_sub:
            raise DomainException(message="No active license found.", status_code=404, error_code="NOT_FOUND")
            
        if db_sub.reset_limit <= 0: 
            raise DomainException(message="You have no device resets left.", status_code=403, error_code="RESET_LIMIT_REACHED")

        if not any(d.id == device_id for d in db_sub.devices):
            raise DomainException(message="Device not found on your license.", status_code=404, error_code="NOT_FOUND")
            
        deleted = await self.license_repo.remove_device(device_id)

        if deleted:
            db_sub.reset_limit -= 1
            await self.db.commit()
            return {"status": "success", "message": "Device has been successfully unlinked."}
        else:
            raise DomainException(message="Device delete is fail.", status_code=404, error_code="DELETE_FAILED")

    async def get_bot_info_by_social(self, telegram_id: int = None, discord_id: int = None) -> dict:
        user_client = UserServiceClient()
        user_id = await user_client.resolve_social_to_user_id(telegram_id, discord_id)
        if not user_id:
            return {"is_vip": False}
            
        license_obj = await self.license_repo.get_active_by_user(user_id)
        if not license_obj:
            return {"is_vip": False}
            
        transaction = license_obj.transaction
        return {
            "is_vip": True,
            "license": {
                "id": license_obj.id,
                "activated_at": format_utc(license_obj.activated_at),
                "expires_at": format_utc(license_obj.expires_at),
                "duration_days": license_obj.duration_days,
                "purchased_at": format_utc(transaction.purchased_at) if transaction else None,
                "purchase_method": transaction.payment_method if transaction else None,
                "purchase_price": transaction.amount if transaction else None
            }
        }

    async def remove_device_admin(self, device_id: int):
        deleted = await self.license_repo.remove_device(device_id)
        if not deleted:
            raise DomainException(
                message=f"Device with ID {device_id} not found.", 
                status_code=404, 
                error_code="NOT_FOUND"
            )
        return {"status": "success"}

    def _make_key(self, n=16) -> str:
        raw = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n))
        return '-'.join(raw[i:i+4] for i in range(0, n, 4))
    
    async def generate_and_bill(self, payload: GeneratePurchaseDTO) -> tuple[list[str], list[int]]:
        keys = [self._make_key() for _ in range(payload.count)]
        
        licenses_to_insert = [
            LicenseModel(
                key=k,
                duration_days=payload.duration_days,
                status=LicenseStatus.NOT_ACTIVATED,
                max_devices=payload.max_devices,
                reset_limit=payload.reset_limit,
            ) for k in keys
        ]
        
        inserted_licenses = await self.license_repo.create_licenses_bulk(licenses_to_insert)
        
        txs_to_insert = [
            TransactionModel(
                license_id=lic.id,
                amount=payload.amount,
                payment_method=payload.method.value,
                status=payload.status
            ) for lic in inserted_licenses
        ]
        
        inserted_txs = await self.tx_repo.create_transactions_bulk(txs_to_insert)
        
        await self.db.commit()
        return keys, [tx.id for tx in inserted_txs]

    async def activate_key_for_user(self, payload: ActivateKeyDTO, user_id: int) -> int:
        db_sub = await self.license_repo.get_by_key(payload.key)
        if not db_sub or db_sub.status != LicenseStatus.NOT_ACTIVATED:
            raise DomainException(message="Invalid key or already activated", status_code=400, error_code="INVALID_KEY")

        active_sub = await self.license_repo.get_active_by_user(user_id)
        if active_sub:
            if not payload.force:
                raise DomainException(
                    message="You already have an active license. Activating this key will deactivate the old one.", 
                    status_code=409, error_code="ACTIVE_LICENSE_EXISTS"
                )
            else:
                active_sub.status = LicenseStatus.EXPIRED
                active_sub.expires_at = datetime.now(timezone.utc)
        
        expires_at = None
        if db_sub.duration_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=db_sub.duration_days)
        
        db_sub.user_id = user_id
        db_sub.status = LicenseStatus.ACTIVE
        db_sub.activated_at = datetime.now(timezone.utc)
        db_sub.expires_at = expires_at
        
        await self.db.commit()
        return db_sub.id

    async def complete_purchase(self, license_id: int, user_id: int):
        tx = await self.tx_repo.get_by_license(license_id)
        if tx:
            tx.status = "COMPLETED"
            tx.user_id = user_id
            tx.purchased_at = datetime.now(timezone.utc)
            await self.db.commit()

    async def get_active_license(self, user_id: int) -> LicenseModel:
        db_sub = await self.license_repo.get_active_by_user(user_id)
        if not db_sub:
            raise DomainException(message="You don't have an active license.", status_code=403, error_code="NO_ACTIVE_LICENSE")
        return db_sub

    @staticmethod
    def _serialize_admin_license(lic: LicenseModel) -> dict:
        return {
            "id": lic.id,
            "user_id": lic.user_id,
            "key": lic.key,
            "status": lic.status.value if isinstance(lic.status, LicenseStatus) else lic.status,
            "duration_days": lic.duration_days,
            "max_devices": lic.max_devices,
            "reset_limit": lic.reset_limit,
            "created_at": format_utc(lic.created_at),
            "activated_at": format_utc(lic.activated_at),
            "expires_at": format_utc(lic.expires_at),
            "devices": [
                {
                    "id": d.id,
                    "device": d.device,
                    "ip_address": d.ip_address,
                    "user_agent": d.user_agent,
                    "first_used_at": format_utc(d.first_used_at),
                    "last_used_at": format_utc(d.last_used_at),
                }
                for d in lic.devices
            ],
            "transaction": {
                "id": lic.transaction.id,
                "amount": lic.transaction.amount,
                "method": lic.transaction.payment_method,
                "status": lic.transaction.status,
                "purchased_at": format_utc(lic.transaction.purchased_at),
            } if lic.transaction else None,
        }

    @staticmethod
    def _purchase_anchor(lic: LicenseModel) -> datetime:
        tx = lic.transaction
        if tx is not None and tx.purchased_at is not None:
            return to_utc(tx.purchased_at)
        if lic.activated_at is not None:
            return to_utc(lic.activated_at)
        if lic.created_at is not None:
            return to_utc(lic.created_at)
        return datetime.now(timezone.utc)

    def _recalc_expiry(self, lic: LicenseModel) -> None:
        if lic.duration_days is None:
            lic.expires_at = None
            return
        lic.expires_at = self._purchase_anchor(lic) + timedelta(days=lic.duration_days)

    async def admin_get_license(self, license_id: int) -> dict:
        license_obj = await self.license_repo.get_by_id(license_id)
        if not license_obj:
            raise DomainException(message="License not found", status_code=404, error_code="NOT_FOUND")
        return self._serialize_admin_license(license_obj)

    async def admin_find_licenses(self, user_id: int = None, key: str = None) -> list:
        if not user_id and not key:
            return []

        licenses = await self.license_repo.search_licenses(user_id=user_id, key=key)
        return [self._serialize_admin_license(lic) for lic in licenses]

    async def admin_update_license(self, license_id: int, payload: UpdateLicenseDTO) -> dict:
        license_obj = await self.license_repo.get_by_id(license_id)
        if not license_obj:
            raise DomainException(message="License not found", status_code=404, error_code="NOT_FOUND")

        update_data = payload.model_dump(exclude_unset=True)
        previous_status = license_obj.status
        previous_duration = license_obj.duration_days

        if "reset_limit" in update_data:
            license_obj.reset_limit = update_data["reset_limit"]
        if "max_devices" in update_data:
            license_obj.max_devices = update_data["max_devices"]
        if "duration_days" in update_data:
            license_obj.duration_days = update_data["duration_days"]
        if "user_id" in update_data:
            license_obj.user_id = update_data["user_id"]
            if license_obj.transaction is not None:
                license_obj.transaction.user_id = update_data["user_id"]
        if "amount" in update_data:
            if license_obj.transaction is None:
                raise DomainException(
                    message="License has no transaction to update amount",
                    status_code=400,
                    error_code="NO_TRANSACTION",
                )
            license_obj.transaction.amount = update_data["amount"]
        if "status" in update_data:
            license_obj.status = update_data["status"]

        became_invalid = (
            previous_status == LicenseStatus.ACTIVE
            and license_obj.status in (LicenseStatus.EXPIRED, LicenseStatus.BANNED)
        )
        became_active = (
            previous_status != LicenseStatus.ACTIVE
            and license_obj.status == LicenseStatus.ACTIVE
        )
        duration_changed = (
            "duration_days" in update_data
            and update_data["duration_days"] != previous_duration
        )

        if became_invalid:
            license_obj.expires_at = datetime.now(timezone.utc)
        elif license_obj.status == LicenseStatus.ACTIVE and (became_active or duration_changed):
            if became_active and license_obj.activated_at is None:
                license_obj.activated_at = datetime.now(timezone.utc)
            self._recalc_expiry(license_obj)

        if license_obj.status == LicenseStatus.ACTIVE and license_obj.user_id is not None:
            while True:
                other = await self.license_repo.get_active_by_user(
                    license_obj.user_id, exclude_id=license_obj.id
                )
                if other is None:
                    break
                other.status = LicenseStatus.EXPIRED
                other.expires_at = datetime.now(timezone.utc)

        await self.db.commit()
        updated = await self.license_repo.get_by_id(license_id)
        if not updated:
            raise DomainException(message="License not found", status_code=404, error_code="NOT_FOUND")
        return self._serialize_admin_license(updated)

    async def admin_delete_license(self, license_id: int):
        deleted = await self.license_repo.delete_license(license_id)
        if not deleted:
            raise DomainException(message="License not found", status_code=404, error_code="NOT_FOUND")

class LicenseStatsService:
    def __init__(self, cache: PublicStatsCache):
        self.cache = cache

    async def get_website_stats(self, background_tasks: BackgroundTasks):
        return await self.cache.get(self._load_stats, background_tasks)

    @staticmethod
    async def _load_stats():
        # A refresh owns its DB session, including when it runs after the response.
        async with AsyncSessionLocal() as db:
            return await LicenseRepository(db).get_heavy_public_stats()
