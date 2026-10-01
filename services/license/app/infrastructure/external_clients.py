import httpx
from fastapi import HTTPException
from typing import Optional
from app.shared.config import settings

class UserServiceClient:
    def __init__(self):
        self.base_url = settings.USER_SERVICE_URL.rstrip("/")
        self.headers = {"x-internal-token": settings.INTERNAL_SECRET_TOKEN}

    async def resolve_social_to_user_id(self, telegram_id: Optional[int] = None, discord_id: Optional[int] = None) -> Optional[int]:
        async with httpx.AsyncClient() as client:
            try:
                params = {}
                if telegram_id:
                    params["telegram_id"] = telegram_id
                if discord_id:
                    params["discord_id"] = discord_id
                
                response = await client.get(f"{self.base_url}/api/v1/users/resolve", params=params, headers=self.headers)
                if response.status_code == 200:
                    return response.json().get("user_id")
                
                if response.status_code == 404:
                    return None
                    
                print(f"[UserServiceClient] Unexpected status code: {response.status_code}")
                raise HTTPException(status_code=503, detail="User service unavailable")
            except Exception as e:
                print(f"[UserServiceClient] Failed to resolve user: {e}")
                raise HTTPException(status_code=503, detail="User service unavailable")

    async def get_social_ids_by_user_id(self, user_id: int) -> dict:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/api/v1/users/{user_id}/socials", headers=self.headers)
                if response.status_code == 200:
                    return response.json()
                
                if response.status_code == 404:
                    return {}

                print(f"[UserServiceClient] Unexpected status {response.status_code} for user {user_id}")
                raise HTTPException(status_code=503, detail="User service unavailable")
                
            except httpx.RequestError as e:
                print(f"[UserServiceClient] Connection failed: {e}")
                raise HTTPException(status_code=503, detail="User service unavailable")

    async def get_user_profile(self, user_id: int) -> dict | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/api/v1/users/{user_id}/profile",
                    headers=self.headers,
                )
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 404:
                    return None
                print(f"[UserServiceClient] Unexpected status {response.status_code} for profile {user_id}")
                raise HTTPException(status_code=503, detail="User service unavailable")
            except httpx.RequestError as e:
                print(f"[UserServiceClient] Connection failed: {e}")
                raise HTTPException(status_code=503, detail="User service unavailable")
