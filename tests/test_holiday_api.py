"""独立验证节日逻辑，不需要启动 AstrBot。"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import astrbot.api
except ModuleNotFoundError:
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = logging.getLogger("holiday-test")
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", astrbot_api)

from api.holiday_api import HolidayAPI
from api.date_utils import BEIJING_TZ, get_current_date_info, get_lunar_date


def row(name, when, off=1):
    return {"name": name, "date": when, "is_off_day": off}


class HolidayTests(unittest.TestCase):
    def setUp(self):
        self.api = HolidayAPI("")

    def test_after_national_day_and_next_year(self):
        result = self.api.parse_holidays({"data": [row("国庆节", "2026-10-07")]}, today=date(2026, 10, 14))
        self.assertEqual([(x["name"], x["days_left"]) for x in result],
                         [("周末", 3), ("元旦", 79), ("春节", 115)])
        self.assertEqual(result[-1]["kind"], "festival")

    def test_year_end_and_new_year(self):
        result = self.api.parse_holidays(None, 8, today=date(2026, 12, 31))
        self.assertEqual(result[0]["name"], "元旦")
        self.assertEqual(result[0]["days_left"], 1)
        result = self.api.parse_holidays(None, 8, today=date(2027, 1, 1))
        self.assertEqual(result[0]["days_left"], 0)
        self.assertEqual(next(x for x in result if x["name"] == "春节")["days_left"], 36)

    def test_lunar_festivals(self):
        for name, when in [("春节", date(2027, 2, 6)), ("端午节", date(2026, 6, 19)),
                           ("中秋节", date(2026, 9, 25)), ("清明节", date(2026, 4, 5)),
                           ("清明节", date(2024, 4, 4))]:
            with self.subTest(name=name, when=when):
                result = self.api.parse_holidays(None, 8, today=when)
                self.assertEqual(next(x for x in result if x["name"] == name)["days_left"], 0)

    def test_api_schedule_overrides_festival_and_deduplicates(self):
        payload = {"data": [row("春节", "2027-02-07"), row("春节", "2027-02-05", "1"),
                            row("春节", "2027-02-06"), row("春节", "2027-02-04", 0)]}
        result = self.api.parse_holidays(payload, 8, today=date(2027, 2, 1))
        spring = [x for x in result if x["name"] == "春节"]
        self.assertEqual(spring, [{"name": "春节", "days_left": 4, "kind": "holiday"}])
        result = self.api.parse_holidays(payload, 8, today=date(2027, 2, 6))
        self.assertEqual(next(x for x in result if x["name"] == "春节")["days_left"], 0)

    def test_weekend(self):
        for when, expected in [(date(2026, 10, 16), 1), (date(2026, 10, 17), 0),
                               (date(2026, 10, 18), 0), (date(2026, 10, 19), 5)]:
            with self.subTest(when=when):
                result = self.api.parse_holidays(None, 8, today=when)
                self.assertEqual(next(x for x in result if x["name"] == "周末")["days_left"], expected)

    def test_invalid_data_and_count(self):
        for payload in [None, {}, {"data": None}, {"data": {}}, {"code": 401, "data": []},
                        {"data": [None, row(None, "bad"), row("错误", "bad"),
                                  row("错误", 123), row("错误", "2027-01-01", 0)]}]:
            with self.subTest(payload=payload):
                self.assertEqual(len(self.api.parse_holidays(payload, 2, today=date(2026, 10, 14))), 2)
        self.assertEqual(self.api.parse_holidays(None, 0), [])
        self.assertEqual(self.api.parse_holidays(None, -1), [])

    def test_timezone_and_leap_month(self):
        # UTC 12 月 31 日 16:30 已经是北京时间元旦。
        when = datetime(2026, 12, 31, 16, 30, tzinfo=timezone.utc).astimezone(BEIJING_TZ)
        with patch("api.date_utils.get_current_datetime", return_value=when):
            self.assertEqual(get_current_date_info()["date_str"], "2027-01-01")
        self.assertTrue(get_lunar_date(datetime(2025, 7, 25, tzinfo=BEIJING_TZ)).startswith("闰六月"))
        next_push = datetime.combine(when.date(), datetime.min.time().replace(hour=8), tzinfo=when.tzinfo)
        self.assertEqual((next_push - when).total_seconds(), 7.5 * 3600)

    def test_template_today_and_fallback(self):
        from jinja2 import Template
        template = Template((Path(__file__).resolve().parents[1] / "daily_news.html").read_text(encoding="utf-8-sig"))
        html = template.render(date_info={"week_cn": "星期五", "date_str": "2027-01-01", "cn_date_str": "冬月廿四"},
                               hitokoto_data={}, moyu_list=self.api.parse_holidays(None, 8, today=date(2027, 1, 1)))
        self.assertIn("今天是", html)
        self.assertIn('元旦<small class="holiday-kind">（节日）</small>', html)

    def test_greeting_uses_real_days_left_field(self):
        import ast
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8-sig")
        plugin = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef))
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "_get_default_greeting")
        scope = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), scope)
        greeting = scope["_get_default_greeting"]
        self.assertIn("春节快乐", greeting(None, 8, [{"name": "春节", "days_left": 0}]))
        self.assertIn("还有3天", greeting(None, 8, [{"name": "周末", "days_left": 3}]))


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_year_params_body_cache_and_null_next_year(self):
        class Response:
            def __init__(self, year):
                self.year = year
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def raise_for_status(self):
                pass
            async def json(self):
                return {"code": 200, "data": None if self.year == 2027 else [row("国庆节", "2026-10-01")]}

        class Session:
            closed = False
            def __init__(self):
                self.calls = []
            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response(kwargs["json"]["year"])

        session = Session()
        api = HolidayAPI("test-token", session=session)
        with patch("api.holiday_api.get_current_datetime", return_value=datetime(2026, 10, 14, tzinfo=BEIJING_TZ)):
            result = await api.get_moyu_list_async()
            self.assertEqual(result[-1]["days_left"], 115)
            await api.get_moyu_list_async()
        self.assertEqual(len(session.calls), 2)
        self.assertEqual({kwargs["json"]["year"] for _, kwargs in session.calls}, {2026, 2027})
        for url, kwargs in session.calls:
            self.assertNotIn("test-token", url)
            self.assertEqual(kwargs["json"]["token"], "test-token")
        with patch("api.holiday_api.get_current_datetime", return_value=datetime(2027, 1, 1, tzinfo=BEIJING_TZ)):
            await api.get_moyu_list_async()
        self.assertEqual(session.calls[-1][1]["json"]["year"], 2028)

    async def test_timeout_fallback_and_safe_logging(self):
        class Session:
            closed = False
            def post(self, *args, **kwargs):
                raise asyncio.TimeoutError("test-token")
        api = HolidayAPI("test-token", session=Session())
        with self.assertLogs("holiday-test", level="WARNING") as captured:
            result = await api.get_moyu_list_async()
        self.assertEqual(len(result), 3)
        self.assertNotIn("test-token", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
