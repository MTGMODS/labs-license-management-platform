from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

pytestmark = [pytest.mark.component("services/usage"), pytest.mark.anyio, pytest.mark.integration]

PAYLOAD = {"version": "1.2.3 Free", "mode": "police", "server": 1, "device": "PC", "hwid": "test-hwid"}


@pytest.mark.parametrize("field,value", [("server", 34), ("server", -1), ("mode", "unknown"),
    ("device", "CONSOLE"), ("hwid", "x"), ("version", "1.2"), ("version", "1.2 Premium")])
async def test_invalid_launch_is_not_saved(component, db, api, field, value):
    response = await api.post("/api/v1/usage/launch", json={**PAYLOAD, field: value})
    assert response.status_code == 422 and response.json()["error_code"] == "VALIDATION_ERROR"
    model = component("app.infrastructure.repository").LaunchModel
    assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("server", [0, 1, 33, 101, 103, 200, 301, 307, 401, 403])
async def test_supported_server_ranges(component, server):
    assert component("app.domain.models").LaunchPayload(**{**PAYLOAD, "server": server}).server == server


async def test_launch_alias_persistence_and_unique_users(component, db, api):
    payload = {**PAYLOAD, "server_id": PAYLOAD["server"]}
    del payload["server"]
    for version in ("1.2.3 Free", "1.2.3 VIP"):
        response = await api.post("/api/v1/usage/launch", json={**payload, "version": version})
        assert response.status_code == 200 and response.json()["status"] == "success"
    # Fresh session: writes must actually be committed, not just in identity map.
    database = component("app.shared.database")
    repo = component("app.infrastructure.repository")
    async with database.AsyncSessionLocal() as other:
        rows = (await other.scalars(select(repo.LaunchModel))).all()
        assert len(rows) == 2 and all(row.server == 1 for row in rows)
        stats = await repo.LaunchRepository(other).get_heavy_public_stats()
    assert stats["overview"]["launches"]["all_time"] == 2
    assert stats["analytics"]["timeline"]["daily"][0]["users"] == 1
    assert stats["analytics"]["timeline"]["daily"][0]["vip_users"] == 1


async def test_stats_periods_exclude_old_launches(component, db):
    repo = component("app.infrastructure.repository")
    now = datetime.now(timezone.utc)
    for index, age in enumerate([timedelta(minutes=10), timedelta(hours=2), timedelta(days=2), timedelta(days=40)]):
        db.add(repo.LaunchModel(**{**PAYLOAD, "hwid": f"device-{index}"}, launched_at=now-age))
    await db.commit()
    stats = await repo.LaunchRepository(db).get_heavy_public_stats()
    assert stats["overview"]["launches"] == {"all_time": 4, "30d": 3, "24h": 2, "1h": 1}
    assert len(stats["distribution"]["servers"]) == 1
    assert stats["distribution"]["servers"][0]["server"] == 1


async def test_empty_stats_no_division_by_zero(component, db):
    stats = await component("app.infrastructure.repository").LaunchRepository(db).get_heavy_public_stats()
    assert stats["overview"]["launches"] == {"all_time": 0, "30d": 0, "24h": 0, "1h": 0}
    assert stats["analytics"]["timeline"]["daily"] == []
