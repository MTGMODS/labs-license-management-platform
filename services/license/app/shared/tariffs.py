import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class TariffLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_devices: int = Field(ge=1)
    reset_limit: int = Field(ge=0)

class TariffPlan(TariffLimits):
    duration_days: Optional[int] = None
    price: Optional[float] = None
    telegram_stars_price: Optional[int] = None

class TariffsCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: str = "USD"
    plans: tuple[TariffPlan, ...]


_tariffs_catalog: TariffsCatalog | None = None


def load_tariffs() -> TariffsCatalog:
    global _tariffs_catalog
    if _tariffs_catalog is None:
        path = Path(__file__).with_name("tariffs.json")
        with path.open(encoding="utf-8") as f:
            _tariffs_catalog = TariffsCatalog.model_validate(json.load(f))
    return _tariffs_catalog

def public_tariffs() -> dict:
    catalog = load_tariffs()
    return {
        "currency": catalog.currency,
        "plans": [
            {
                "duration_days": plan.duration_days,
                "price": plan.price,
                "telegram_stars_price": plan.telegram_stars_price,
                "max_devices": plan.max_devices,
                "reset_limit": plan.reset_limit,
            }
            for plan in catalog.plans
        ]
    }
