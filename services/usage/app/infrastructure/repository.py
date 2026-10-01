from sqlalchemy import Column, Integer, String, DateTime, select, func, distinct, case, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from app.shared.database import Base
from app.shared.datetime_utils import format_utc

class LaunchModel(Base):
    __tablename__ = "launches"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String, nullable=False)
    device = Column(String, nullable=False)
    hwid = Column(String, index=True, nullable=False)
    server = Column(Integer, index=True, nullable=True)
    mode = Column(String, index=True, nullable=True)
    launched_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class LaunchRepository:

    PERIODS = ("all_time", "30d", "24h", "1h")

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _period_metrics(users: dict, launches: dict, g_users: dict, vip_users: dict | None = None) -> dict:
        user_share = {}
        launches_per_user = {}
        vip_percent = {}
        for p in LaunchRepository.PERIODS:
            u = users.get(p) or 0
            launch_count = launches.get(p) or 0
            gu = g_users.get(p) or 0
            user_share[p] = round((u / gu) * 100, 1) if gu > 0 else 0
            launches_per_user[p] = round(launch_count / u, 2) if u > 0 else 0
            if vip_users is not None:
                vip_percent[p] = round(((vip_users.get(p) or 0) / u) * 100, 1) if u > 0 else 0
        result = {"user_share": user_share, "launches_per_user": launches_per_user}
        if vip_users is not None:
            result["vip_percent"] = vip_percent
        return result

    @staticmethod
    def _launches_per_user(users, launches) -> float:
        return round(launches / users, 2) if users else 0

    async def save(self, version: str, hwid: str, device: str, server: int, mode: str):
        new_launch = LaunchModel(
            version=version,
            hwid=hwid,
            device=device,
            server=server,
            mode=mode,
        )
        self.db.add(new_launch)
        await self.db.commit()
        await self.db.refresh(new_launch)
        return new_launch


    # ---- Analytics Methods ----

    async def _get_global(self, d30, d1, d1h):
        stmt = select(
            func.count(distinct(LaunchModel.hwid)).label("u_all"),
            func.count(distinct(case((LaunchModel.launched_at >= d30, LaunchModel.hwid)))).label("u_30d"),
            func.count(distinct(case((LaunchModel.launched_at >= d1, LaunchModel.hwid)))).label("u_24h"),
            func.count(distinct(case((LaunchModel.launched_at >= d1h, LaunchModel.hwid)))).label("u_1h"),
            
            func.count(distinct(case((LaunchModel.version.ilike('%VIP%'), LaunchModel.hwid)))).label("vip_all"),
            func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("vip_30d"),
            func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("vip_24h"),
            func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("vip_1h"),

            func.count(LaunchModel.id).label("l_all"),
            func.count(case((LaunchModel.launched_at >= d30, LaunchModel.id))).label("l_30d"),
            func.count(case((LaunchModel.launched_at >= d1, LaunchModel.id))).label("l_24h"),
            func.count(case((LaunchModel.launched_at >= d1h, LaunchModel.id))).label("l_1h"),

            func.count(distinct(case((LaunchModel.device == 'PC', LaunchModel.hwid)))).label("pc_all"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("pc_30d"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("pc_24h"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("pc_1h"),

            func.count(case((LaunchModel.device == 'PC', LaunchModel.id))).label("pc_l_all"),
            func.count(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d30), LaunchModel.id))).label("pc_l_30d"),
            func.count(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d1), LaunchModel.id))).label("pc_l_24h"),
            func.count(case((and_(LaunchModel.device == 'PC', LaunchModel.launched_at >= d1h), LaunchModel.id))).label("pc_l_1h"),

            func.count(distinct(case((LaunchModel.device == 'MOBILE', LaunchModel.hwid)))).label("mob_all"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("mob_30d"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("mob_24h"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("mob_1h"),

            func.count(case((LaunchModel.device == 'MOBILE', LaunchModel.id))).label("mob_l_all"),
            func.count(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d30), LaunchModel.id))).label("mob_l_30d"),
            func.count(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d1), LaunchModel.id))).label("mob_l_24h"),
            func.count(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.launched_at >= d1h), LaunchModel.id))).label("mob_l_1h"),

            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.version.ilike('%VIP%')), LaunchModel.hwid)))).label("pc_vip_all"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("pc_vip_30d"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("pc_vip_24h"),
            func.count(distinct(case((and_(LaunchModel.device == 'PC', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("pc_vip_1h"),

            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.version.ilike('%VIP%')), LaunchModel.hwid)))).label("mob_vip_all"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("mob_vip_30d"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("mob_vip_24h"),
            func.count(distinct(case((and_(LaunchModel.device == 'MOBILE', LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("mob_vip_1h"),
        )
        res = (await self.db.execute(stmt)).first()
        
        g_u_all = res.u_all if res and res.u_all > 0 else 0
        g_users = {
            "all_time": res.u_all or 0,
            "30d": res.u_30d or 0,
            "24h": res.u_24h or 0,
            "1h": res.u_1h or 0,
        }

        vip_conversion = round((res.vip_all / g_u_all) * 100, 2) if g_u_all > 0 else 0
        total_devices = res.pc_all + res.mob_all
        pc_ratio = round((res.pc_all / total_devices) * 100, 1) if total_devices > 0 else 0
        mob_ratio = round(100 - pc_ratio, 1) if total_devices > 0 else 0

        global_data = {
            "metrics": {
                "vip_conversion": vip_conversion,
                "pc_ratio": pc_ratio,
                "mobile_ratio": mob_ratio,
            },
            "users": {
                "total": g_users,
                "vip": {"all_time": res.vip_all, "30d": res.vip_30d, "24h": res.vip_24h, "1h": res.vip_1h},
            },
            "launches": {
                "all_time": res.l_all,
                "30d": res.l_30d,
                "24h": res.l_24h,
                "1h": res.l_1h
            },
            "devices": {
                "pc": {
                    "users": {"all_time": res.pc_all, "30d": res.pc_30d, "24h": res.pc_24h, "1h": res.pc_1h},
                    "vip_users": {"all_time": res.pc_vip_all, "30d": res.pc_vip_30d, "24h": res.pc_vip_24h, "1h": res.pc_vip_1h},
                    "launches": {"all_time": res.pc_l_all, "30d": res.pc_l_30d, "24h": res.pc_l_24h, "1h": res.pc_l_1h},
                    **LaunchRepository._period_metrics(
                        {"all_time": res.pc_all, "30d": res.pc_30d, "24h": res.pc_24h, "1h": res.pc_1h},
                        {"all_time": res.pc_l_all, "30d": res.pc_l_30d, "24h": res.pc_l_24h, "1h": res.pc_l_1h},
                        g_users,
                        {"all_time": res.pc_vip_all, "30d": res.pc_vip_30d, "24h": res.pc_vip_24h, "1h": res.pc_vip_1h},
                    ),
                },
                "mobile": {
                    "users": {"all_time": res.mob_all, "30d": res.mob_30d, "24h": res.mob_24h, "1h": res.mob_1h},
                    "vip_users": {"all_time": res.mob_vip_all, "30d": res.mob_vip_30d, "24h": res.mob_vip_24h, "1h": res.mob_vip_1h},
                    "launches": {"all_time": res.mob_l_all, "30d": res.mob_l_30d, "24h": res.mob_l_24h, "1h": res.mob_l_1h},
                    **LaunchRepository._period_metrics(
                        {"all_time": res.mob_all, "30d": res.mob_30d, "24h": res.mob_24h, "1h": res.mob_1h},
                        {"all_time": res.mob_l_all, "30d": res.mob_l_30d, "24h": res.mob_l_24h, "1h": res.mob_l_1h},
                        g_users,
                        {"all_time": res.mob_vip_all, "30d": res.mob_vip_30d, "24h": res.mob_vip_24h, "1h": res.mob_vip_1h},
                    ),
                }
            }
        }
        return global_data, g_users

    async def _get_servers(self, d30, d1, d1h, g_users):
        stmt = (
            select(
                LaunchModel.server,
                func.count(distinct(LaunchModel.hwid)).label("u_all"),
                func.count(distinct(case((LaunchModel.launched_at >= d30, LaunchModel.hwid)))).label("u_30d"),
                func.count(distinct(case((LaunchModel.launched_at >= d1, LaunchModel.hwid)))).label("u_24h"),
                func.count(distinct(case((LaunchModel.launched_at >= d1h, LaunchModel.hwid)))).label("u_1h"),
                func.count(distinct(case((LaunchModel.version.ilike('%VIP%'), LaunchModel.hwid)))).label("vip_all"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("vip_30d"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("vip_24h"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("vip_1h"),
                func.count(LaunchModel.id).label("l_all"),
                func.count(case((LaunchModel.launched_at >= d30, LaunchModel.id))).label("l_30d"),
                func.count(case((LaunchModel.launched_at >= d1, LaunchModel.id))).label("l_24h"),
                func.count(case((LaunchModel.launched_at >= d1h, LaunchModel.id))).label("l_1h")
            )
            .where(LaunchModel.server != 200)
            .group_by(LaunchModel.server)
            .order_by(LaunchModel.server.asc())
        )
        res = await self.db.execute(stmt)
        result = []
        for row in res.all():
            users = {"all_time": row.u_all, "30d": row.u_30d, "24h": row.u_24h, "1h": row.u_1h}
            launches = {"all_time": row.l_all, "30d": row.l_30d, "24h": row.l_24h, "1h": row.l_1h}
            vip_users = {"all_time": row.vip_all, "30d": row.vip_30d, "24h": row.vip_24h, "1h": row.vip_1h}
            metrics = self._period_metrics(users, launches, g_users, vip_users)
            result.append({
                "server": row.server,
                "user_share": metrics["user_share"],
                "launches_per_user": metrics["launches_per_user"],
                "vip_percent": metrics["vip_percent"],
                "users": users,
                "vip_users": vip_users,
                "launches": launches,
            })
        return result

    async def _get_factions(self, d30, d1, d1h, g_users):
        stmt = (
            select(
                func.coalesce(LaunchModel.mode, 'none').label("m_name"),
                func.count(distinct(LaunchModel.hwid)).label("u_all"),
                func.count(distinct(case((LaunchModel.launched_at >= d30, LaunchModel.hwid)))).label("u_30d"),
                func.count(distinct(case((LaunchModel.launched_at >= d1, LaunchModel.hwid)))).label("u_24h"),
                func.count(distinct(case((LaunchModel.launched_at >= d1h, LaunchModel.hwid)))).label("u_1h"),
                func.count(distinct(case((LaunchModel.version.ilike('%VIP%'), LaunchModel.hwid)))).label("vip_all"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label("vip_30d"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label("vip_24h"),
                func.count(distinct(case((and_(LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label("vip_1h"),
                func.count(LaunchModel.id).label("l_all"),
                func.count(case((LaunchModel.launched_at >= d30, LaunchModel.id))).label("l_30d"),
                func.count(case((LaunchModel.launched_at >= d1, LaunchModel.id))).label("l_24h"),
                func.count(case((LaunchModel.launched_at >= d1h, LaunchModel.id))).label("l_1h")
            )
            .group_by("m_name")
            .order_by(desc("u_all"))
        )
        res = await self.db.execute(stmt)
        result = []
        for row in res.all():
            users = {"all_time": row.u_all, "30d": row.u_30d, "24h": row.u_24h, "1h": row.u_1h}
            launches = {"all_time": row.l_all, "30d": row.l_30d, "24h": row.l_24h, "1h": row.l_1h}
            vip_users = {"all_time": row.vip_all, "30d": row.vip_30d, "24h": row.vip_24h, "1h": row.vip_1h}
            metrics = self._period_metrics(users, launches, g_users, vip_users)
            result.append({
                "mode": row.m_name,
                "user_share": metrics["user_share"],
                "launches_per_user": metrics["launches_per_user"],
                "vip_percent": metrics["vip_percent"],
                "users": users,
                "vip_users": vip_users,
                "launches": launches,
            })
        return result

    async def _get_versions(self, d30, d1, d1h, g_users):
        stmt = (
            select(
                LaunchModel.version,
                func.count(distinct(LaunchModel.hwid)).label("u_all"),
                func.count(distinct(case((LaunchModel.launched_at >= d30, LaunchModel.hwid)))).label("u_30d"),
                func.count(distinct(case((LaunchModel.launched_at >= d1, LaunchModel.hwid)))).label("u_24h"),
                func.count(distinct(case((LaunchModel.launched_at >= d1h, LaunchModel.hwid)))).label("u_1h"),
                func.count(LaunchModel.id).label("l_all"),
                func.count(case((LaunchModel.launched_at >= d30, LaunchModel.id))).label("l_30d"),
                func.count(case((LaunchModel.launched_at >= d1, LaunchModel.id))).label("l_24h"),
                func.count(case((LaunchModel.launched_at >= d1h, LaunchModel.id))).label("l_1h")
            )
            .group_by(LaunchModel.version)
            .order_by(desc("u_all"))
        )
        res = await self.db.execute(stmt)
        result = []
        for row in res.all():
            users = {"all_time": row.u_all, "30d": row.u_30d, "24h": row.u_24h, "1h": row.u_1h}
            launches = {"all_time": row.l_all, "30d": row.l_30d, "24h": row.l_24h, "1h": row.l_1h}
            metrics = self._period_metrics(users, launches, g_users)
            result.append({
                "version": row.version,
                "user_share": metrics["user_share"],
                "launches_per_user": metrics["launches_per_user"],
                "users": users,
                "launches": launches,
            })
        return result

    async def _get_products(self, d30, d1, d1h, g_users):
        families = [
            ("arizona_pc", LaunchModel.server.between(1, 33)),
            ("arizona_mobile", LaunchModel.server.between(101, 103)),
            ("rodina_pc", LaunchModel.server.between(301, 307)),
            ("rodina_mobile", LaunchModel.server.between(401, 403)),
        ]

        select_cols = []
        for name, cond in families:
            select_cols.extend([
                func.count(distinct(case((cond, LaunchModel.hwid)))).label(f"{name}_u_all"),
                func.count(distinct(case((and_(cond, LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label(f"{name}_u_30d"),
                func.count(distinct(case((and_(cond, LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label(f"{name}_u_24h"),
                func.count(distinct(case((and_(cond, LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label(f"{name}_u_1h"),
                func.count(distinct(case((and_(cond, LaunchModel.version.ilike('%VIP%')), LaunchModel.hwid)))).label(f"{name}_vip_all"),
                func.count(distinct(case((and_(cond, LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d30), LaunchModel.hwid)))).label(f"{name}_vip_30d"),
                func.count(distinct(case((and_(cond, LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1), LaunchModel.hwid)))).label(f"{name}_vip_24h"),
                func.count(distinct(case((and_(cond, LaunchModel.version.ilike('%VIP%'), LaunchModel.launched_at >= d1h), LaunchModel.hwid)))).label(f"{name}_vip_1h"),
                func.count(case((cond, LaunchModel.id))).label(f"{name}_l_all"),
                func.count(case((and_(cond, LaunchModel.launched_at >= d30), LaunchModel.id))).label(f"{name}_l_30d"),
                func.count(case((and_(cond, LaunchModel.launched_at >= d1), LaunchModel.id))).label(f"{name}_l_24h"),
                func.count(case((and_(cond, LaunchModel.launched_at >= d1h), LaunchModel.id))).label(f"{name}_l_1h"),
            ])

        res = (await self.db.execute(select(*select_cols))).first()
        products = []
        for name, _ in families:
            users = {
                "all_time": getattr(res, f"{name}_u_all") or 0,
                "30d": getattr(res, f"{name}_u_30d") or 0,
                "24h": getattr(res, f"{name}_u_24h") or 0,
                "1h": getattr(res, f"{name}_u_1h") or 0,
            }
            launches = {
                "all_time": getattr(res, f"{name}_l_all") or 0,
                "30d": getattr(res, f"{name}_l_30d") or 0,
                "24h": getattr(res, f"{name}_l_24h") or 0,
                "1h": getattr(res, f"{name}_l_1h") or 0,
            }
            vip_users = {
                "all_time": getattr(res, f"{name}_vip_all") or 0,
                "30d": getattr(res, f"{name}_vip_30d") or 0,
                "24h": getattr(res, f"{name}_vip_24h") or 0,
                "1h": getattr(res, f"{name}_vip_1h") or 0,
            }
            metrics = self._period_metrics(users, launches, g_users, vip_users)
            products.append({
                "product": name,
                "user_share": metrics["user_share"],
                "launches_per_user": metrics["launches_per_user"],
                "vip_percent": metrics["vip_percent"],
                "users": users,
                "vip_users": vip_users,
                "launches": launches,
            })
        return products

    async def _get_timeline_daily(self):
        stmt = (
            select(
                func.date(LaunchModel.launched_at).label("dt"),
                func.count(distinct(LaunchModel.hwid)).label("u_all"),
                func.count(distinct(case((LaunchModel.version.ilike("%VIP%"), LaunchModel.hwid)))).label("vip_all"),
                func.count(LaunchModel.id).label("l_all")
            )
            .group_by(func.date(LaunchModel.launched_at))
            .order_by(func.date(LaunchModel.launched_at))
        )
        res = await self.db.execute(stmt)
        return [
            {
                "date": str(row.dt) if row.dt else "Unknown",
                "users": row.u_all,
                "vip_users": row.vip_all,
                "launches": row.l_all,
                "launches_per_user": self._launches_per_user(row.u_all, row.l_all),
            }
            for row in res.all()
        ]

    async def _get_activity_hourly(self):
        stmt = (
            select(
                func.extract('hour', LaunchModel.launched_at).label("hr"),
                func.count(distinct(LaunchModel.hwid)).label("u_all"),
                func.count(LaunchModel.id).label("l_all")
            )
            .group_by(func.extract('hour', LaunchModel.launched_at))
            .order_by(func.extract('hour', LaunchModel.launched_at))
        )
        res = await self.db.execute(stmt)
        return [
            {
                "hour": int(row.hr) if row.hr is not None else 0,
                "users": row.u_all,
                "launches": row.l_all,
                "launches_per_user": self._launches_per_user(row.u_all, row.l_all),
            }
            for row in res.all()
        ]

    async def get_heavy_public_stats(self):
        now = datetime.now(timezone.utc)
        d30 = now - timedelta(days=30)
        d1 = now - timedelta(hours=24)
        d1h = now - timedelta(hours=1)

        global_data, g_users = await self._get_global(d30, d1, d1h)

        factions = await self._get_factions(d30, d1, d1h, g_users)
        servers = await self._get_servers(d30, d1, d1h, g_users)
        versions = await self._get_versions(d30, d1, d1h, g_users)
        products = await self._get_products(d30, d1, d1h, g_users)
        
        timeline_daily = await self._get_timeline_daily()
        
        activity_hourly = await self._get_activity_hourly()

        return {
            "updated_at": format_utc(now),
            "overview": {
                "metrics": global_data["metrics"],
                "users": global_data["users"],
                "launches": global_data["launches"],
                "devices": global_data["devices"]
            },
            "distribution": {
                "factions": factions,
                "servers": servers,
                "versions": versions,
                "products": products,
            },
            "analytics": {
                "timeline": {
                    "daily": timeline_daily,
                },
                "activity": {
                    "hourly": activity_hourly
                }
            }
        }
