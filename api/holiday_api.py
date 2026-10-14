"""优先使用 ALAPI 放假安排，缺失时按真实节日日期计算倒计时。"""
import asyncio
from datetime import date, datetime
from time import monotonic
from typing import Dict, List, Optional

import aiohttp

from astrbot.api import logger
from .base_api import BaseAPI
from .date_utils import get_current_datetime


class HolidayAPI(BaseAPI):
    """保持原插件接口；本地节日日期不代表官方调休安排。"""

    FIXED_FESTIVALS = {"元旦": (1, 1), "劳动节": (5, 1), "国庆节": (10, 1)}
    LUNAR_FESTIVALS = {"春节": (1, 1), "端午节": (5, 5), "中秋节": (8, 15)}

    def __init__(self, token: str, session: Optional[aiohttp.ClientSession] = None,
                 year: Optional[int] = None):
        super().__init__(session)
        self.token = token
        self.url = "https://v3.alapi.cn/api/holiday"
        self.headers = {"Content-Type": "application/json"}
        # 不在初始化时固定当前年，以兼容长时间运行、跨年的机器人。
        self.year = year
        self._cache = {}
        self._request_lock = asyncio.Lock()
        self._last_request = 0

    async def get_holidays_async(self, year: Optional[int] = None) -> Optional[Dict]:
        if not self.token:
            return None
        target_year = year if year is not None else (self.year or get_current_datetime().year)
        async with self._request_lock:
            cached = self._cache.get(target_year)
            if cached and monotonic() - cached[0] < 3600:
                return cached[1]
            # ALAPI 有频率限制；跨年查询和并发日报共用请求节流。
            wait_seconds = 1.1 - (monotonic() - self._last_request)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last_request = monotonic()
            return await self._request_holidays(target_year)

    async def _request_holidays(self, target_year: int) -> Optional[Dict]:
        cached = self._cache.get(target_year)
        if cached and monotonic() - cached[0] < 3600:
            return cached[1]
        try:
            session = await self._get_session()
            # token 放在请求体，避免 URL 或 HTTP 异常信息包含凭据。
            async with session.post(
                self.url, headers=self.headers,
                json={"token": self.token, "year": target_year},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
            if not isinstance(payload, dict) or payload.get("code") not in (None, 200, "200"):
                code = payload.get("code") if isinstance(payload, dict) else None
                logger.warning(f"节假日 API 未返回有效数据（年份={target_year}，状态={code}），使用本地节日日期")
                return None
            # 次年尚未公布时 data=null 也缓存，避免每次生成都重复查询。
            self._cache[target_year] = (monotonic(), payload)
            return payload
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("节假日 API 请求失败，使用本地节日日期")
        except Exception:
            logger.warning("节假日 API 响应无法解析，使用本地节日日期")
        return None

    def _get_default_holidays(self, today: Optional[date] = None) -> List[Dict]:
        """动态兜底。周日仍在周末内；农历节日使用已依赖的 zhdate。"""
        today = today or get_current_datetime().date()
        weekend_days = 0 if today.weekday() >= 5 else 5 - today.weekday()
        result = [{"name": "周末", "days_left": weekend_days, "kind": "weekend"}]
        for name, (month, day) in self.FIXED_FESTIVALS.items():
            festival = date(today.year, month, day)
            if festival < today:
                festival = date(today.year + 1, month, day)
            result.append({"name": name, "days_left": (festival - today).days, "kind": "festival"})

        # 清明日的 21 世纪公式；超出有效世纪时省略，不编造日期。
        for year in (today.year, today.year + 1):
            if not 2000 <= year <= 2099:
                continue
            y = year % 100
            festival = date(year, 4, int(y * 0.2422 + 4.81) - y // 4)
            if festival >= today:
                result.append({"name": "清明节", "days_left": (festival - today).days, "kind": "festival"})
                break

        try:
            from zhdate import ZhDate
            for name, (month, day) in self.LUNAR_FESTIVALS.items():
                # 遍历前一年，兼容公历一月仍属于上一农历年的情况。
                candidates = []
                for year in (today.year - 1, today.year, today.year + 1):
                    try:
                        festival = ZhDate(year, month, day).to_datetime().date()
                    except (ValueError, TypeError, IndexError):
                        continue
                    if festival >= today:
                        candidates.append(festival)
                if candidates:
                    result.append({"name": name, "days_left": (min(candidates) - today).days, "kind": "festival"})
        except ImportError:
            logger.warning("未安装 zhdate，暂不显示农历节日倒计时")
        return sorted(result, key=lambda item: item["days_left"])

    def parse_holidays(self, api_data: Optional[Dict], max_count: int = 3,
                       today: Optional[date] = None) -> List[Dict]:
        """合并放假安排和本地日期，按最近日期去重并遵守数量配置。"""
        if max_count <= 0:
            return []
        today = today or get_current_datetime().date()
        by_name = {item["name"]: item for item in self._get_default_holidays(today)}
        rows = []
        if isinstance(api_data, dict) and api_data.get("code") in (None, 200, "200"):
            data = api_data.get("data")
            if isinstance(data, list):
                rows = data

        official = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("is_off_day") not in (1, "1", True):
                continue
            name, date_str = row.get("name"), row.get("date")
            if not isinstance(name, str) or not name.strip() or not isinstance(date_str, str):
                continue
            try:
                holiday_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if holiday_date < today:
                continue
            name = name.strip()
            days_left = (holiday_date - today).days
            if name not in official or days_left < official[name]["days_left"]:
                official[name] = {"name": name, "days_left": days_left, "kind": "holiday"}
        by_name.update(official)
        return sorted(by_name.values(), key=lambda item: item["days_left"])[:max_count]

    async def get_moyu_list_async(self, max_count: int = 3) -> List[Dict]:
        if max_count <= 0:
            return []
        today = get_current_datetime().date()
        year = self.year or today.year
        payloads = await asyncio.gather(
            self.get_holidays_async(year), self.get_holidays_async(year + 1),
        )
        rows = []
        for payload in payloads:
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                rows.extend(payload["data"])
        return self.parse_holidays({"data": rows}, max_count, today=today)
