import aiohttp
from typing import Optional
from app.config import BACKEND_API_URL, BOT_SECRET_TOKEN 

class APIClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def check_vip_status(self, telegram_id: int) -> dict:
        if not self.session:
            return {"error": True}

        url = f"{BACKEND_API_URL}/check/info?telegram_id={telegram_id}"
        
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

    async def get_tariffs(self) -> dict:
        if not self.session:
            return {"error": True}

        url = f"{BACKEND_API_URL}/tariffs"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"error": True}
        except Exception as e:
            print(f"[API] Get tariffs Error: {e}")
            return {"error": True}

    async def generate_license(self, duration_days: int, amount: float, max_devices: int, reset_limit: int) -> dict:
        if not self.session:
            return {"error": True}

        url = f"{BACKEND_API_URL}/generate/telegram_bot"

        headers = {"x-bot-token": BOT_SECRET_TOKEN}

        payload = {"duration_days": duration_days, "amount": amount, "max_devices": max_devices, "reset_limit": reset_limit}
        
        try:
            async with self.session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"error": True}
        except Exception as e:
            print(f"[API] Generate License Error: {e}")
            return {"error": True}

api_client = APIClient()
