import jwt
import hmac
import hashlib
import secrets
import time
from jwt import PyJWKClient
from urllib.parse import parse_qsl
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.shared.config import settings

ALGORITHM = "HS256"

def create_access_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": str(user_id),
        "role": role,        
        "type": "access",
        "exp": expire
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)

def create_refresh_token(user_id: int) -> tuple[str, datetime]:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
        "exp": expire
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM), expire

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def verify_refresh_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        
        if user_id is None or token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type or missing payload data"
            )
            
        return int(user_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

# Telegram verification

def verify_telegram_oidc(id_token: str) -> dict | None:
    try:
        signing_key = PyJWKClient("https://oauth.telegram.org/.well-known/jwks.json").get_signing_key_from_jwt(id_token)
        payload = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=str(settings.TELEGRAM_CLIENT_ID), 
            issuer="https://oauth.telegram.org",
            leeway=60
        )
        return payload
        
    except jwt.ExpiredSignatureError as e:
        print(f"[OIDC Error] Token Expired: {e}")
    except jwt.InvalidAudienceError as e:
        print(f"[OIDC Error] Client ID (Audience) != {settings.TELEGRAM_CLIENT_ID}: {e}")
    except Exception as e:
        print(f"[OIDC Error] Validation Error ({type(e).__name__}): {e}")
        
    return None

def verify_telegram_webapp_hash(init_data: str, bot_token: str) -> bool:
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        tg_hash = parsed_data.pop("hash", None)
        if not tg_hash:
            return False

        auth_date = parsed_data.get("auth_date")
        if not auth_date:
            return False

        auth_timestamp = int(auth_date)
        now = int(time.time())
        if auth_timestamp > now + 60 or now - auth_timestamp > settings.TELEGRAM_WEBAPP_MAX_AGE_SECONDS:
            return False
            
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        
        return hmac.compare_digest(calculated_hash, tg_hash)
    except Exception:
        return False
    
def create_link_ticket(user_id: int, provider: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {
        "sub": str(user_id),
        "type": "link",
        "provider": provider,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)

def verify_link_ticket(ticket: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(ticket, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        provider = payload.get("provider")

        if user_id is None or token_type != "link" or provider not in ("telegram", "discord"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid link ticket",
            )

        return int(user_id), provider
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Link ticket has expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid link ticket",
        )

# User authentication dependency
  
security = HTTPBearer()

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        
        if user_id is None or token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type or missing user"
            )
            
        return int(user_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def get_admin_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        role = payload.get("role")
        token_type = payload.get("type")
        
        if user_id is None or token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type or missing user"
            )
            
        if role != "ADMIN":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough privileges. Admins only."
            )
            
        return int(user_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
