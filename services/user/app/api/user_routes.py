from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.database import get_db
from app.application.user_service import UserService
from app.domain.models import User
from app.domain.schemas import LinkSocialPayload, LinkTicketPayload, LinkTicketResponse, UnlinkSocialPayload
from app.application.jwt_utils import create_link_ticket, get_current_user_id

router = APIRouter(prefix="/api/v1/users", tags=["User Dashboard"])

@router.get("/me", response_model=User)
async def get_my_profile(current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    service = UserService(db)
    return await service.get_user_by_id(current_user_id)

@router.post("/me/link-ticket", response_model=LinkTicketResponse)
async def create_my_link_ticket(payload: LinkTicketPayload, current_user_id: int = Depends(get_current_user_id)):
    return LinkTicketResponse(ticket=create_link_ticket(current_user_id, payload.provider))

@router.post("/me/link", response_model=User)
async def link_social_account(payload: LinkSocialPayload, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    service = UserService(db)
    return await service.link_social(user_id=current_user_id, telegram_id=payload.telegram_id, discord_id=payload.discord_id)

@router.post("/me/unlink", response_model=User)
async def unlink_social_account(payload: UnlinkSocialPayload, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    service = UserService(db)
    return await service.unlink_social(user_id=current_user_id, provider=payload.provider)
