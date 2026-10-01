from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from app.infrastructure.repository import RefreshSessionRepository, UserRepository
from app.domain.models import User, UserStatus
from app.domain.schemas import TokenResponse
from app.application.jwt_utils import create_access_token, create_refresh_token, hash_refresh_token, verify_refresh_token
from app.shared.exceptions import DomainException

class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)
        self.refresh_sessions = RefreshSessionRepository(db)

    async def issue_token_pair(self, user: User) -> TokenResponse:
        refresh_token, expires_at = create_refresh_token(user.id)
        await self.refresh_sessions.create(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=expires_at,
        )
        return TokenResponse(
            access_token=create_access_token(user_id=user.id, role=user.role),
            refresh_token=refresh_token,
            user=user,
        )

    async def rotate_refresh_token(self, refresh_token: str) -> TokenResponse:
        user_id = verify_refresh_token(refresh_token)
        session = await self.refresh_sessions.get_active_by_hash(hash_refresh_token(refresh_token))
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        await self.refresh_sessions.delete_by_hash(hash_refresh_token(refresh_token))
        db_user = await self.get_valid_user_for_refresh(user_id)
        return await self.issue_token_pair(User.model_validate(db_user))

    async def logout(self, refresh_token: str) -> None:
        verify_refresh_token(refresh_token)
        await self.refresh_sessions.delete_by_hash(hash_refresh_token(refresh_token))

    async def login_with_telegram(self, telegram_id: int, nickname: str = None, avatar_url: str = None) -> User:
        db_user = await self.repo.get_by_telegram_id(telegram_id)
        return await self._process_login(db_user, telegram_id=telegram_id, nickname=nickname, avatar_url=avatar_url)

    async def login_with_discord(self, discord_id: int, nickname: str = None, avatar_url: str = None) -> User:
        db_user = await self.repo.get_by_discord_id(discord_id)
        return await self._process_login(db_user, discord_id=discord_id, nickname=nickname, avatar_url=avatar_url)

    async def _process_login(self, db_user, telegram_id: int = None, discord_id: int = None, nickname: str = None, avatar_url: str = None) -> User:
        safe_nickname = nickname[:50] if nickname else str(telegram_id or discord_id)
    
        if db_user:
            if db_user.status == UserStatus.BANNED:
                raise DomainException("This account is banned.", status_code=403, error_code="USER_BANNED")
            elif db_user.status == UserStatus.DELETED:
                raise DomainException("This account was deleted.", status_code=403, error_code="USER_DELETED")
            
            if safe_nickname:
                db_user.nickname = safe_nickname
            if avatar_url:
                db_user.avatar_url = avatar_url
            
            db_user.last_login_at = func.now() 
            db_user = await self.repo.update(db_user)
        else:
            db_user = await self.repo.create(
                nickname=safe_nickname,
                telegram_id=telegram_id,
                discord_id=discord_id,
                avatar_url=avatar_url
            )
        return User.model_validate(db_user)
    
    async def get_valid_user_for_refresh(self, user_id: int):
        db_user = await self.repo.get_by_id(user_id)
        
        if not db_user:
            raise DomainException("User not found.", status_code=404, error_code="USER_NOT_FOUND")
            
        if db_user.status == UserStatus.BANNED:
            raise DomainException("This account is banned.", status_code=403, error_code="USER_BANNED")
        elif db_user.status == UserStatus.DELETED:
            raise DomainException("This account was deleted.", status_code=403, error_code="USER_DELETED")
            
        return db_user
