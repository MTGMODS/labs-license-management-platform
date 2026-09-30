import os
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest

pytestmark = [pytest.mark.component("services/distribution"), pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def builds(component, monkeypatch, tmp_path):
    module = component("app.application.service")
    template = tmp_path / "template.lua"
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(module, "BASE_TEMPLATE_PATH", str(template))
    monkeypatch.setattr(module, "BUILDS_DIR", str(output))
    return module, template, output


@pytest.mark.parametrize("source", ["-- Привіт\nfunction main()\nend", "function main() end", "function main(...) end"])
async def test_timed_build_injects_expiry_and_preserves_template(component, builds, source):
    module, template, output = builds
    template.write_text(source, encoding="cp1251")
    payload = component("app.domain.schemas").GenerationPayloadDTO(user_id=42, expire_date="2026-12-31T20:15:00Z")
    url = await module.DistributionService().build_vip_file(payload, "abcdef12-3456-7890")
    assert url.endswith("/abcdef1234")
    result = (output / "abcdef1234.lua").read_text(encoding="cp1251")
    assert "year=2026, month=12, day=31, hour=20, min=15" in result
    assert "os.remove(thisScript().path)" in result
    assert template.read_text(encoding="cp1251") == source


async def test_forever_build_has_no_time_bomb(component, builds):
    module, template, output = builds
    source = "function main() end"
    template.write_text(source, encoding="cp1251")
    payload = component("app.domain.schemas").GenerationPayloadDTO(user_id=42)
    await module.DistributionService().build_vip_file(payload, "abcdef1234")
    assert (output / "abcdef1234.lua").read_text(encoding="cp1251") == source


async def test_missing_template_fails_without_output(component, builds):
    module, _, output = builds
    with pytest.raises(RuntimeError, match="template is missing"):
        await module.DistributionService().build_vip_file(component("app.domain.schemas").GenerationPayloadDTO(user_id=42), "abcdef1234")
    assert list(output.iterdir()) == []


async def test_download_response_deletes_file_after_delivery(component, builds, monkeypatch):
    _, _, output = builds
    routes = component("app.api.routes")
    errors = component("app.shared.exceptions")
    monkeypatch.setattr(routes, "BUILDS_DIR_PATH", output)
    app = FastAPI()
    app.include_router(routes.router)
    app.add_exception_handler(errors.DomainException, errors.global_exception_handler)
    file = output / "abcdef1234.lua"
    file.write_bytes(b"function main() end")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/files/downloads/vip/abcdef1234")
        assert response.status_code == 200 and response.content == b"function main() end"
        assert "Arizona%20Helper.lua" in response.headers["content-disposition"] or "Arizona Helper.lua" in response.headers["content-disposition"]
        assert not file.exists()
        assert (await client.get("/api/v1/files/downloads/vip/abcdef1234")).status_code == 404
        assert (await client.get("/api/v1/files/downloads/vip/not-a-token")).status_code == 422


async def test_cleanup_removes_only_expired_builds(component, monkeypatch, tmp_path):
    worker = component("app.application.worker")
    old = tmp_path / "old.lua"
    fresh = tmp_path / "fresh.lua"
    nested = tmp_path / "vip"
    nested.mkdir()
    template = nested / "template.lua"
    for path in (old, fresh, template):
        path.write_text("test", encoding="utf-8")
    now = datetime.now(timezone.utc).timestamp()
    os.utime(old, (now-7200, now-7200))
    os.utime(template, (now-7200, now-7200))
    monkeypatch.setattr(worker, "DOWNLOADS_DIR", tmp_path)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await worker.cleanup_old_files_task()
    assert not old.exists() and fresh.exists() and template.exists()
