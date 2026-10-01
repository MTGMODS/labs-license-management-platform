import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, String, Text, cast, BigInteger, DateTime, delete, select, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.shared.database import Base
from app.shared.config import settings
from app.shared.datetime_utils import to_utc
from app.domain.models import UserStatus

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    discord_id = Column(BigInteger, unique=True, index=True, nullable=True)
    nickname = Column(String(50), nullable=False, index=True)
    avatar_url = Column(String, nullable=True)
    role = Column(String(20), default="USER", nullable=False)
    status = Column(SQLEnum(UserStatus), default=UserStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class OAuthHandoffModel(Base):
    __tablename__ = "oauth_handoffs"

    ticket = Column(String(64), primary_key=True)
    payload = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class RefreshSessionModel(Base):
    __tablename__ = "refresh_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class RefreshSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            delete(RefreshSessionModel).where(RefreshSessionModel.expires_at <= now)
        )
        await self.db.commit()
        return result.rowcount or 0

    async def create(self, user_id: int, token_hash: str, expires_at: datetime) -> None:
        await self.purge_expired()
        self.db.add(
            RefreshSessionModel(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        await self.db.commit()

    async def get_active_by_hash(self, token_hash: str) -> RefreshSessionModel | None:
        result = await self.db.execute(
            select(RefreshSessionModel).where(RefreshSessionModel.token_hash == token_hash)
        )
        row = result.scalars().first()
        if not row:
            return None
        if to_utc(row.expires_at) <= datetime.now(timezone.utc):
            return None
        return row

    async def delete_by_hash(self, token_hash: str) -> None:
        await self.db.execute(
            delete(RefreshSessionModel).where(RefreshSessionModel.token_hash == token_hash)
        )
        await self.db.commit()


class OAuthHandoffRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            delete(OAuthHandoffModel).where(OAuthHandoffModel.expires_at <= now)
        )
        await self.db.commit()
        return result.rowcount or 0

    async def issue(self, message: dict) -> str:
        await self.purge_expired()
        ticket = secrets.token_urlsafe(32)
        row = OAuthHandoffModel(
            ticket=ticket,
            payload=json.dumps(message, ensure_ascii=False),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.OAUTH_HANDOFF_TTL_SECONDS),
        )
        self.db.add(row)
        await self.db.commit()
        return ticket

    async def consume(self, ticket: str) -> dict | None:
        result = await self.db.execute(
            select(OAuthHandoffModel).where(OAuthHandoffModel.ticket == ticket)
        )
        row = result.scalars().first()
        if not row:
            return None

        expired = to_utc(row.expires_at) <= datetime.now(timezone.utc)
        payload_text = row.payload
        await self.db.delete(row)
        await self.db.commit()
        if expired:
            return None
        return json.loads(payload_text)


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: int) -> UserModel | None:
        result = await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        return result.scalars().first()

    async def get_by_telegram_id(self, tg_id: int) -> UserModel | None:
        result = await self.db.execute(select(UserModel).where(UserModel.telegram_id == tg_id))
        return result.scalars().first()

    async def get_by_discord_id(self, ds_id: int) -> UserModel | None:
        result = await self.db.execute(select(UserModel).where(UserModel.discord_id == ds_id))
        return result.scalars().first()
    
    async def search_users(self, nickname: str = None, telegram_id: str = None, discord_id: str = None) -> list[UserModel]:
        stmt = select(UserModel)

        if nickname:
            stmt = stmt.where(UserModel.nickname.ilike(f"%{nickname}%"))
            
        if telegram_id:
            stmt = stmt.where(cast(UserModel.telegram_id, String).ilike(f"%{telegram_id}%"))
            
        if discord_id:
            stmt = stmt.where(cast(UserModel.discord_id, String).ilike(f"%{discord_id}%"))
        
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, nickname: str, telegram_id: int = None, discord_id: int = None, avatar_url: str = None) -> UserModel:
        new_user = UserModel(
            nickname=nickname, 
            telegram_id=telegram_id, 
            discord_id=discord_id,
            avatar_url=avatar_url
        )
        self.db.add(new_user)
        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user

    async def update(self, user: UserModel) -> UserModel:
        await self.db.commit()
        await self.db.refresh(user)
        return user