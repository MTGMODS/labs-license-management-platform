import aiohttp
from typing import Optional
from app.config import API_ENDPOINT_URL, BOT_SECRET_TOKEN 

class APIClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def check_vip_status(self, discord_id: int) -> dict:
        if not self.session:
            return {"error": True}

        url = f"{API_ENDPOINT_URL}?discord_id={discord_id}"
        
        headers = {"x-bot-token": BOT_SECRET_TOKEN}
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status in (404, 400):
                    return {"is_vip": False}
                elif response.status in (401, 403):
                    print("[API Error] Unauthorized: Invalid BOT_SECRET_TOKEN")
                    return {"error": True}
                else:
                    print(f"[API Error] Backend returned status {response.status}")
                    return {"error": True}
        except Exception as e:
            print(f"[API Error] Failed to connect to backend: {e}")
            return {"error": True}

api_client = APIClient()