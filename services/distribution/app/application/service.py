import os
import re
import aiofiles
from app.shared.config import settings
from app.domain.schemas import GenerationPayloadDTO

BUILDS_DIR = "app/builds"
BASE_TEMPLATE_PATH = "app/builds/vip/Arizona Helper.lua" 

os.makedirs(BUILDS_DIR, exist_ok=True)

class DistributionService:
    async def build_vip_file(self, payload: GenerationPayloadDTO, correlation_id: str) -> str:
        if not os.path.exists(BASE_TEMPLATE_PATH):
            raise RuntimeError(f"Base LUA template is missing at {BASE_TEMPLATE_PATH}")

        async with aiofiles.open(BASE_TEMPLATE_PATH, mode='r', encoding='cp1251') as f:
            content = await f.read()

        if payload.expire_date:
            expire = payload.expire_date
            time_bomb = f"if os.time() > os.time{{year={expire.year}, month={expire.month}, day={expire.day}, hour={expire.hour}, min={expire.minute}}} then os.remove(thisScript().path) end"

            if "\nfunction main" in content:
                content += f"\n\n{time_bomb}"
            elif "function main" in content:
                content = re.sub(
                    r"(function\s+main\s*\(\s*(?:\.\.\.)?\s*\))",
                    rf"\1 {time_bomb}; ",
                    content
                )

        file_token = correlation_id.replace("-", "")[:10]
        filepath = os.path.join(BUILDS_DIR, f"{file_token}.lua")
        
        async with aiofiles.open(filepath, mode="w", encoding="cp1251") as f:
            await f.write(content)
            
        print(f"[Build] VIP file ready for User {payload.user_id}. Token: {file_token}")

        base = settings.DOWNLOAD_BASE_URL.rstrip("/")
        return f"{base}/{file_token}"

    @staticmethod
    def remove_file(path: str):
        if os.path.exists(path):
            os.remove(path)
            print(f"[Cleanup] Removed file: {path}")