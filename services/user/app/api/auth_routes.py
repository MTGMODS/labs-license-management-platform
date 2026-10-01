import httpx
import json
import secrets
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.database import get_db
from app.application.auth_service import AuthService
from app.application.user_service import UserService
from app.infrastructure.repository import OAuthHandoffRepository
from app.domain.schemas import TokenResponse, RefreshRequest, TelegramAuthPayload
from app.application.jwt_utils import (
    verify_link_ticket,
    verify_telegram_oidc,
    verify_telegram_webapp_hash,
)
from app.shared.config import settings
from app.shared.exceptions import DomainException

router = APIRouter(prefix="/api/v1/users/auth", tags=["Authentication"])


def _frontend_origin() -> str:
    return settings.FRONTEND_URL.rstrip("/")

def _oauth_cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "max_age": 300,
        "secure": not settings.DEBUG_MODE,
        "samesite": "lax",
    }

def _apply_oauth_cookies(response: RedirectResponse, *, state: str, ticket: str | None, expected_provider: str) -> RedirectResponse:
    response.set_cookie(key="oauth_state", value=state, **_oauth_cookie_kwargs())
    response.delete_cookie("oauth_link_user_id")
    if not ticket:
        return response

    user_id, provider = verify_link_ticket(ticket)
    if provider != expected_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Link ticket provider mismatch",
        )
    response.set_cookie(key="oauth_link_user_id", value=str(user_id), **_oauth_cookie_kwargs())
    return response

async def _complete_oauth(request: Request, db: AsyncSession, *, telegram_id: int | None = None, discord_id: int | None = None, nickname: str, avatar_url: str | None) -> HTMLResponse:
    link_user_id = request.cookies.get("oauth_link_user_id")
    if link_user_id:
        user = await UserService(db).link_social(
            user_id=int(link_user_id),
            telegram_id=telegram_id,
            discord_id=discord_id,
        )
    else:
        service = AuthService(db)
        if telegram_id is not None:
            user = await service.login_with_telegram(
                telegram_id=telegram_id,
                nickname=nickname,
                avatar_url=avatar_url,
            )
        else:
            user = await service.login_with_discord(
                discord_id=discord_id,
                nickname=nickname,
                avatar_url=avatar_url,
            )

    token = await AuthService(db).issue_token_pair(user)
    response = await _oauth_success(db, token)
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_link_user_id")
    return response

def _json_for_script(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

async def _oauth_popup_html(db: AsyncSession, *, payload: dict | None = None, error_code: str | None = None, message: str | None = None) -> HTMLResponse:
    data = (
        {"type": "mtg_auth_success", "payload": payload}
        if payload is not None
        else {"type": "mtg_auth_error", "error_code": error_code, "message": message}
    )
    ticket = await OAuthHandoffRepository(db).issue(data)

    target = _json_for_script(_frontend_origin())
    payload_js = _json_for_script(payload) if payload is not None else "null"
    error_code_js = _json_for_script(error_code)
    message_js = _json_for_script(message)
    ticket_js = _json_for_script(ticket)

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MTG MODS Auth</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: system-ui, sans-serif;
      background: #0a0a10;
      color: #9d9db4;
    }}
  </style>
</head>
<body>
  <p id="status">Closing…</p>
  <script>
  (function () {{
    var target = {target};
    var payload = {payload_js};
    var errorCode = {error_code_js};
    var message = {message_js};
    var ticket = {ticket_js};
    var statusEl = document.getElementById("status");

    var data = payload
      ? {{ type: "mtg_auth_success", payload: payload }}
      : {{ type: "mtg_auth_error", error_code: errorCode, message: message }};

    function fallbackRedirect() {{
      statusEl.textContent = "Redirecting…";
      window.location.replace(target + "/auth/callback?ticket=" + encodeURIComponent(ticket));
    }}

    if (window.opener) {{
      try {{
        window.opener.postMessage(data, target);
      }} catch (err) {{
        // fall through — still try close / redirect
      }}
      try {{
        window.close();
      }} catch (err) {{}}
      // Real popup disappears here. If we are still open (tab / blocked popup
      // / full-page with a stale opener), finish via the SPA.
      setTimeout(function () {{
        fallbackRedirect();
      }}, 100);
      return;
    }}

    fallbackRedirect();
  }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=body, headers={"Cache-Control": "no-store"})

async def _oauth_success(db: AsyncSession, token: TokenResponse) -> HTMLResponse:
    return await _oauth_popup_html(db, payload=jsonable_encoder(token))

async def _oauth_error(db: AsyncSession, error_code: str | None = None, message: str | None = None,) -> HTMLResponse:
    return await _oauth_popup_html(db, error_code=error_code, message=message)

async def _oauth_exception_to_html(db: AsyncSession, exc: Exception) -> HTMLResponse:
    if isinstance(exc, DomainException):
        return await _oauth_error(db, error_code=exc.error_code, message=exc.message)
    if isinstance(exc, HTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Authentication failed"
        return await _oauth_error(db, error_code=None, message=message)
    return await _oauth_error(db, error_code=None, message="Authentication failed")


@router.get("/telegram/login")
async def telegram_login_init(ticket: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        state = secrets.token_urlsafe(16)
        url = (
            "https://oauth.telegram.org/auth"
            f"?client_id={settings.TELEGRAM_CLIENT_ID}"
            f"&redirect_uri={settings.TELEGRAM_CALLBACK_URL}"
            "&response_type=code"
            "&scope=openid%20profile"
            f"&state={state}"
        )
        response = RedirectResponse(url)
        return _apply_oauth_cookies(response, state=state, ticket=ticket, expected_provider="telegram")
    except HTTPException as exc:
        return await _oauth_exception_to_html(db, exc)

@router.get("/telegram/callback", response_class=HTMLResponse)
async def telegram_auth_callback(request: Request, code: str, state: str = None, db: AsyncSession = Depends(get_db)):
    try:
        saved_state = request.cookies.get("oauth_state")
        if not state or not saved_state or state != saved_state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSRF validation failed. State parameter is missing or invalid.",
            )

        async with httpx.AsyncClient() as client:
            data = {
                "client_id": settings.TELEGRAM_CLIENT_ID,
                "client_secret": settings.TELEGRAM_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.TELEGRAM_CALLBACK_URL,
            }
            resp = await client.post("https://oauth.telegram.org/token", data=data)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to exchange authorization code")

            id_token = resp.json().get("id_token")

        token_data = verify_telegram_oidc(id_token)
        if not token_data:
            raise HTTPException(status_code=401, detail="Invalid Telegram OIDC token signature.")

        tg_id = int(token_data.get("id"))
        nickname = token_data.get("preferred_username") or token_data.get("name") or tg_id
        avatar_url = token_data.get("picture")

        return await _complete_oauth(
            request,
            db,
            telegram_id=tg_id,
            nickname=str(nickname),
            avatar_url=avatar_url,
        )
    except (HTTPException, DomainException) as exc:
        return await _oauth_exception_to_html(db, exc)


@router.post("/telegram/webapp", response_model=TokenResponse)
async def telegram_webapp_auth(payload: TelegramAuthPayload, db: AsyncSession = Depends(get_db)):
    if not payload.init_data:
        raise HTTPException(status_code=400, detail="Missing init_data in payload")

    is_valid = verify_telegram_webapp_hash(payload.init_data, settings.TELEGRAM_BOT_TOKEN)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Telegram Mini App signature.")

    parsed_data = dict(parse_qsl(payload.init_data))
    user_data_str = parsed_data.get("user", "{}")
    try:
        user_data = json.loads(user_data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid user JSON in initData")

    tg_id = user_data.get("id")
    if not tg_id:
        raise HTTPException(status_code=400, detail="User ID not found in initData")

    nickname = user_data.get("username") or user_data.get("first_name") or f"tg_{tg_id}"
    avatar_url = user_data.get("photo_url")

    service = AuthService(db)
    user = await service.login_with_telegram(
        telegram_id=int(tg_id),
        nickname=nickname,
        avatar_url=avatar_url,
    )

    return await service.issue_token_pair(user)


@router.get("/discord/login", summary="Initiate Discord OAuth2 Flow")
async def discord_oauth_login(ticket: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        state = secrets.token_urlsafe(16)
        url = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={settings.DISCORD_CLIENT_ID}"
            f"&redirect_uri={settings.DISCORD_REDIRECT_URI}"
            f"&response_type=code"
            f"&scope=identify"
            f"&state={state}"
        )
        response = RedirectResponse(url)
        return _apply_oauth_cookies(response, state=state, ticket=ticket, expected_provider="discord")
    except HTTPException as exc:
        return await _oauth_exception_to_html(db, exc)

@router.get("/discord/callback", response_class=HTMLResponse, summary="Discord OAuth2 Callback Receiver")
async def discord_oauth_callback(request: Request, code: str, state: str = None, db: AsyncSession = Depends(get_db)):
    try:
        saved_state = request.cookies.get("oauth_state")
        if not state or not saved_state or state != saved_state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSRF validation failed. State parameter is missing or invalid.",
            )

        async with httpx.AsyncClient() as client:
            token_data = {
                "client_id": settings.DISCORD_CLIENT_ID,
                "client_secret": settings.DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.DISCORD_REDIRECT_URI,
            }
            token_headers = {"Content-Type": "application/x-www-form-urlencoded"}

            token_res = await client.post(
                "https://discord.com/api/oauth2/token",
                data=token_data,
                headers=token_headers,
            )
            if token_res.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange authorization code with Discord",
                )

            discord_access_token = token_res.json().get("access_token")

            profile_headers = {"Authorization": f"Bearer {discord_access_token}"}
            profile_res = await client.get(
                "https://discord.com/api/users/@me",
                headers=profile_headers,
            )
            if profile_res.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to fetch user profile from Discord",
                )

            discord_profile = profile_res.json()

        discord_id = int(discord_profile["id"])
        nickname = discord_profile["username"] or discord_id
        avatar_hash = discord_profile.get("avatar")

        avatar_url = None
        if avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"

        return await _complete_oauth(
            request,
            db,
            discord_id=discord_id,
            nickname=str(nickname),
            avatar_url=avatar_url,
        )
    except (HTTPException, DomainException) as exc:
        return await _oauth_exception_to_html(db, exc)


@router.get("/handoff/{ticket}")
async def consume_oauth_handoff(ticket: str, db: AsyncSession = Depends(get_db)):
    if not ticket or len(ticket) > 64:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Auth handoff ticket is invalid or expired",
        )

    message = await OAuthHandoffRepository(db).consume(ticket)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Auth handoff ticket is invalid or expired",
        )
    return message


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await AuthService(db).rotate_refresh_token(request.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    await AuthService(db).logout(request.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)