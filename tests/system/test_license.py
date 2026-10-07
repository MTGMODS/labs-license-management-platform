from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

pytestmark = [pytest.mark.component("services/license"), pytest.mark.anyio, pytest.mark.integration]


def test_tariff_catalog_is_read_once_on_first_request(component, monkeypatch):
    tariffs = component("app.shared.tariffs")
    monkeypatch.setattr(tariffs, "_tariffs_catalog", None)

    with patch.object(tariffs.json, "load", wraps=tariffs.json.load) as read_json:
        first = tariffs.public_tariffs()
        second = tariffs.public_tariffs()

    assert first == second
    assert first["plans"]
    read_json.assert_called_once()


@pytest.fixture
def service(component, db, monkeypatch):
    module = component("app.application.service")
    monkeypatch.setattr(module.UserServiceClient, "get_user_profile", AsyncMock(return_value={"nickname": "Tester"}))
    return module.LicenseService(db)


async def purchase(component, service, **overrides):
    values = dict(amount=5, method="Stars", duration_days=30, max_devices=2, reset_limit=1)
    values.update(overrides)
    dto = component("app.domain.schemas").GeneratePurchaseDTO(**values)
    return await service.generate_and_bill(dto)


async def activate(component, service, key, user_id=42, force=False):
    dto = component("app.domain.schemas").ActivateKeyDTO(key=key, force=force)
    return await service.activate_key_for_user(dto, user_id)


async def test_purchase_activation_check_and_dashboard(component, db, service):
    keys, tx_ids = await purchase(component, service, count=2)
    assert len(set(keys)) == len(tx_ids) == 2
    assert all(len(key) == 19 for key in keys)
    license_id = await activate(component, service, keys[0])
    await service.complete_purchase(license_id, 42)
    assert await service.check_for_client(keys[0], "device-123", "127.0.0.1", "test") == (
        200, {"valid": True, "user": "Tester", "id": 42},
    )
    db.expire_all()  # Re-query relationships as a new HTTP request would.
    info = await service.get_license_info(42)
    assert info["license"]["status"] == "ACTIVE"
    assert info["transaction"]["status"] == "COMPLETED"
    assert info["transaction"]["amount"] == 5
    assert info["devices"][0]["hwid"] == "dev******123"
    assert info["license"]["expires_at"].endswith("Z")


@pytest.mark.parametrize("status,owned,elapsed,expected", [
    ("NOT_ACTIVATED", False, False, {"valid": False, "expires": False, "error": "NOT_ACTIVATED"}),
    ("ACTIVE", True, True, {"valid": False, "expires": True}),
    ("EXPIRED", True, False, {"valid": False, "expires": True}),
    ("BANNED", True, False, {"valid": False, "expires": True}),
])
async def test_invalid_license_cannot_register_device(component, db, service, status, owned, elapsed, expected):
    repo = component("app.infrastructure.repository")
    row = repo.LicenseModel(key="AAAA-BBBB-CCCC-DDDD", status=status, user_id=42 if owned else None,
                            expires_at=datetime.now(timezone.utc) - timedelta(days=1) if elapsed else None)
    db.add(row)
    await db.commit()
    code, body = await service.check_for_client(row.key, "device-123", "127.0.0.1", "test")
    assert code == 200 and body == expected
    assert await db.scalar(select(func.count()).select_from(repo.DeviceModel)) == 0
    service.user_client.get_user_profile.assert_not_awaited()


async def test_missing_key(service):
    assert await service.check_for_client("AAAA-BBBB-CCCC-DDDD", "device", "ip", "ua") == (404, {"detail": "Key not found"})


async def test_device_limit_reuse_and_reset(component, db, service):
    keys, _ = await purchase(component, service, max_devices=1)
    await activate(component, service, keys[0])
    assert (await service.check_for_client(keys[0], "device-one", "ip1", "ua"))[0] == 200
    db.expire_all()
    assert (await service.check_for_client(keys[0], "device-one", "ip2", "ua"))[0] == 200
    db.expire_all()
    assert (await service.check_for_client(keys[0], "device-two", "ip", "ua"))[0] == 403
    info = await service.get_license_info(42)
    assert len(info["devices"]) == 1 and info["devices"][0]["ip"] == "ip2"
    await service.reset_device(42, info["devices"][0]["id"])
    db.expire_all()
    assert (await service.get_license_info(42))["license"]["reset_limit"] == 0
    assert (await service.check_for_client(keys[0], "device-two", "ip", "ua"))[0] == 200
    db.expire_all()
    with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
        await service.reset_device(42, 999)
    assert exc.value.error_code == "RESET_LIMIT_REACHED"


async def test_cannot_reset_other_users_device(component, db, service):
    keys, _ = await purchase(component, service, count=2)
    await activate(component, service, keys[0], 42)
    await activate(component, service, keys[1], 43)
    await service.check_for_client(keys[1], "private-device", "ip", "ua")
    db.expire_all()
    other = await service.get_license_info(43)
    with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
        await service.reset_device(42, other["devices"][0]["id"])
    assert exc.value.status_code == 404
    assert (await service.get_license_info(42))["license"]["reset_limit"] == 1
    assert len((await service.get_license_info(43))["devices"]) == 1


async def test_activation_conflict_force_and_reuse(component, service):
    keys, _ = await purchase(component, service, count=2)
    await activate(component, service, keys[0])
    error = component("app.shared.exceptions").DomainException
    with pytest.raises(error) as exc:
        await activate(component, service, keys[1])
    assert exc.value.status_code == 409
    await activate(component, service, keys[1], force=True)
    assert (await service.get_license_info(42))["license"]["key"] == keys[1]
    assert (await service.get_license_history(42))[0]["license"]["status"] == "EXPIRED"
    with pytest.raises(error) as exc:
        await activate(component, service, keys[1], user_id=99)
    assert exc.value.error_code == "INVALID_KEY"


async def test_forever_license_has_no_expiry(component, service):
    keys, _ = await purchase(component, service, duration_days=None)
    await activate(component, service, keys[0])
    assert (await service.get_license_info(42))["license"]["expires_at"] is None


async def test_expiry_only_deactivates_elapsed_active_licenses(component, db):
    repo = component("app.infrastructure.repository")
    now = datetime.now(timezone.utc)
    db.add_all([
        repo.LicenseModel(key="expired", user_id=1, status="ACTIVE", expires_at=now-timedelta(days=1)),
        repo.LicenseModel(key="future", user_id=2, status="ACTIVE", expires_at=now+timedelta(days=1)),
        repo.LicenseModel(key="forever", user_id=3, status="ACTIVE"),
        repo.LicenseModel(key="banned", user_id=4, status="BANNED", expires_at=now-timedelta(days=1)),
    ])
    await db.commit()
    repository = repo.LicenseRepository(db)
    assert [row["user_id"] for row in await repository.deactivate_expired_licenses()] == [1]
    await db.commit()
    assert await repository.deactivate_expired_licenses() == []
    assert (await repository.get_by_key("forever")).status == "ACTIVE"


async def test_delete_cascades_devices_and_purchase(component, db, service):
    repo = component("app.infrastructure.repository")
    keys, _ = await purchase(component, service)
    license_id = await activate(component, service, keys[0])
    await service.check_for_client(keys[0], "device", "ip", "ua")
    db.expire_all()
    await service.admin_delete_license(license_id)
    for model in (repo.LicenseModel, repo.DeviceModel, repo.TransactionModel):
        assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("field,value", [("max_devices", None), ("max_devices", 0), ("reset_limit", None),
    ("reset_limit", -1), ("count", 0), ("count", 1001), ("amount", -1), ("duration_days", 0)])
async def test_purchase_input_validation(component, field, value):
    values = dict(amount=5, method="Stars", max_devices=2, reset_limit=1)
    values[field] = value
    with pytest.raises(ValidationError):
        component("app.domain.schemas").GeneratePurchaseDTO(**values)


async def test_http_check_contract_and_validation(api, component, service):
    keys, _ = await purchase(component, service)
    await activate(component, service, keys[0])
    response = await api.post("/api/v1/license/check", json={"key": keys[0], "device": "client-device"})
    assert response.status_code == 200 and response.json()["valid"] is True
    invalid = await api.post("/api/v1/license/check", json={"key": "short", "device": "x"})
    assert invalid.status_code == 422 and invalid.json()["error_code"] == "VALIDATION_ERROR"


async def test_private_routes_require_access_token(api):
    response = await api.get("/api/v1/license/info", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


async def test_sales_stats_exclude_pending_free_and_unowned_keys(component, db, service):
    # One paid subscriber and one paid forever license; other keys are not sales.
    for user_id, overrides in [(1, {}), (2, {"duration_days": None, "amount": 20}),
                               (3, {"amount": 0}), (4, {"status": "PENDING"})]:
        keys, _ = await purchase(component, service, **overrides)
        await activate(component, service, keys[0], user_id)
    await purchase(component, service, amount=999)  # Not activated/owned.
    stats = await service.license_repo.get_heavy_public_stats()
    assert stats["subscriptions"]["overview"]["total_sold"] == 1
    assert stats["subscriptions"]["overview"]["total_money"] == 5
    assert stats["forever"]["overview"]["paid_sold"] == 1
    assert stats["forever"]["overview"]["total_money"] == 20


async def test_admin_duration_update_and_ban(component, service):
    keys, _ = await purchase(component, service)
    license_id = await activate(component, service, keys[0])
    schema = component("app.domain.schemas").UpdateLicenseDTO
    updated = await service.admin_update_license(license_id, schema(duration_days=None, max_devices=3))
    assert updated["expires_at"] is None and updated["max_devices"] == 3
    updated = await service.admin_update_license(license_id, schema(status="BANNED"))
    assert updated["status"] == "BANNED" and updated["expires_at"] is not None
    assert (await service.check_for_client(keys[0], "new-device", "ip", "ua"))[1]["valid"] is False


async def test_download_requires_vip_and_preserves_rpc_payload(component, api, service, monkeypatch):
    routes = component("app.api.user_routes")
    main = component("app.main")
    auth = component("app.application.jwt_utils")
    main.app.dependency_overrides[auth.get_current_user_id] = lambda: 42
    publish = AsyncMock(return_value="https://files.invalid/abcdef1234")
    monkeypatch.setattr(routes, "publish_file_generation_event", publish)
    response = await api.post("/api/v1/license/download")
    assert response.status_code == 403
    publish.assert_not_awaited()
    keys, _ = await purchase(component, service, duration_days=None)
    await activate(component, service, keys[0])
    response = await api.post("/api/v1/license/download")
    assert response.status_code == 200 and response.json()["download_url"] == "https://files.invalid/abcdef1234"
    publish.assert_awaited_once_with(user_id=42, expire_date=None)
    publish.side_effect = RuntimeError("offline")
    response = await api.post("/api/v1/license/download")
    assert response.status_code == 500 and response.json()["error_code"] == "FILE_GENERATION_FAILED"
