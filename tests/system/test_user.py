from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
import jwt
import pytest
from sqlalchemy import select

pytestmark = [pytest.mark.component("services/user"), pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def auth(component, db):
    return component("app.application.auth_service").AuthService(db)


@pytest.fixture
def users(component, db):
    return component("app.application.user_service").UserService(db)


async def test_login_creates_then_updates_same_user(auth):
    first = await auth.login_with_telegram(123456789, "First")
    second = await auth.login_with_telegram(123456789, "Second")
    assert first.id == second.id
    assert second.nickname == "Second" and second.telegram_id == "123456789"
    assert first.role == "USER"


async def test_nickname_truncation_and_fallback(auth):
    assert len((await auth.login_with_discord(123, "x" * 70)).nickname) == 50
    assert (await auth.login_with_telegram(456)).nickname == "456"


@pytest.mark.parametrize("status,code", [("BANNED", "USER_BANNED"), ("DELETED", "USER_DELETED")])
async def test_disabled_user_cannot_login_refresh_or_get_profile(component, db, auth, users, status, code):
    user = await auth.login_with_telegram(123, "Tester")
    row = await auth.repo.get_by_id(user.id)
    row.status = status
    await db.commit()
    for operation in (auth.login_with_telegram(123), auth.get_valid_user_for_refresh(user.id), users.get_user_by_id(user.id)):
        with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
            await operation
        assert exc.value.error_code == code


async def test_link_unlink_and_keep_last_provider(component, auth, users):
    user = await auth.login_with_telegram(123, "Tester")
    linked = await users.link_social(user.id, discord_id=514135796685602827)
    assert linked.discord_id == "514135796685602827"  # Avoid JS integer truncation.
    unlinked = await users.unlink_social(user.id, "telegram")
    assert unlinked.telegram_id is None and unlinked.discord_id == linked.discord_id
    with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
        await users.unlink_social(user.id, "discord")
    assert exc.value.error_code == "LAST_SOCIAL_UNLINK"


async def test_cannot_steal_or_overwrite_social(component, auth, users):
    user = await auth.login_with_telegram(123)
    await auth.login_with_discord(456)
    with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
        await users.link_social(user.id, discord_id=456)
    assert exc.value.status_code == 409
    with pytest.raises(component("app.shared.exceptions").DomainException) as exc:
        await users.link_social(user.id, telegram_id=789)
    assert exc.value.error_code == "CANNOT_OVERWRITE_TELEGRAM"


async def test_refresh_rotates_hashed_token_and_logout_revokes(component, db, auth):
    user = await auth.login_with_telegram(123)
    first = await auth.issue_token_pair(user)
    repo = component("app.infrastructure.repository")
    stored = (await db.scalars(select(repo.RefreshSessionModel))).one()
    assert stored.token_hash == hashlib.sha256(first.refresh_token.encode()).hexdigest()
    assert stored.token_hash != first.refresh_token
    second = await auth.rotate_refresh_token(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    with pytest.raises(HTTPException) as exc:
        await auth.rotate_refresh_token(first.refresh_token)
    assert exc.value.status_code == 401
    await auth.logout(second.refresh_token)
    with pytest.raises(HTTPException):
        await auth.rotate_refresh_token(second.refresh_token)


async def test_handoff_consumed_once_and_expired_ticket_rejected(component, db):
    repo = component("app.infrastructure.repository")
    handoffs = repo.OAuthHandoffRepository(db)
    payload = {"type": "auth", "token": "test-token"}
    ticket = await handoffs.issue(payload)
    assert await handoffs.consume(ticket) == payload
    assert await handoffs.consume(ticket) is None
    expired = await handoffs.issue(payload)
    row = await db.get(repo.OAuthHandoffModel, expired)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    assert await handoffs.consume(expired) is None
    assert await db.get(repo.OAuthHandoffModel, expired) is None


@pytest.mark.parametrize("kind", ["access", "expired", "wrong-signature", "garbage"])
async def test_invalid_refresh_jwt(component, kind):
    tokens = component("app.application.jwt_utils")
    secret = tokens.settings.JWT_SECRET
    values = {"sub": "1", "type": "refresh", "exp": datetime.now(timezone.utc)+timedelta(minutes=1)}
    if kind == "access":
        values["type"] = "access"
    if kind == "expired":
        values["exp"] = datetime.now(timezone.utc)-timedelta(seconds=1)
    encoded = "not-a-jwt" if kind == "garbage" else jwt.encode(values, secret if kind != "wrong-signature" else "different-test-secret-32-characters", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        tokens.verify_refresh_token(encoded)
    assert exc.value.status_code == 401


async def test_admin_authorization_and_link_ticket(component):
    tokens = component("app.application.jwt_utils")
    def credentials(role):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=tokens.create_access_token(42, role))
    assert tokens.get_current_user_id(credentials("USER")) == 42
    assert tokens.get_admin_user_id(credentials("ADMIN")) == 42
    with pytest.raises(HTTPException) as exc:
        tokens.get_admin_user_id(credentials("USER"))
    assert exc.value.status_code == 403
    assert tokens.verify_link_ticket(tokens.create_link_ticket(42, "discord")) == (42, "discord")
    with pytest.raises(HTTPException):
        tokens.verify_link_ticket(tokens.create_access_token(42, "USER"))


@pytest.mark.parametrize("age,tamper,expected", [(0, False, True), (4000, False, False), (-120, False, False), (0, True, False)])
async def test_telegram_signature_and_freshness(component, monkeypatch, age, tamper, expected):
    tokens = component("app.application.jwt_utils")
    monkeypatch.setattr(tokens.time, "time", lambda: 1_800_000_000)
    data = {"auth_date": str(1_800_000_000-age), "user": '{"id":123}'}
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", b"test-token", hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        data["user"] = '{"id":999}'
    assert tokens.verify_telegram_webapp_hash(urlencode(data), "test-token") is expected


async def test_http_profile_and_s2s_auth(api, component, auth):
    user = await auth.login_with_telegram(123, "Tester")
    tokens = await auth.issue_token_pair(user)
    response = await api.get("/api/v1/users/me", headers={"Authorization": f"Bearer {tokens.access_token}"})
    assert response.status_code == 200 and response.json()["telegram_id"] == "123"
    assert (await api.get("/api/v1/users/resolve", params={"telegram_id": 123})).status_code == 403
    response = await api.get("/api/v1/users/resolve", params={"telegram_id": 123}, headers={"X-Internal-Token": "test-internal-secret"})
    assert response.status_code == 200 and response.json() == {"user_id": user.id}
    assert (await api.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid"})).status_code == 401
