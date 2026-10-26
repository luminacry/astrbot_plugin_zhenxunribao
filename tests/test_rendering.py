"""Exercise the framework rendering contract without running AstrBot."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from jinja2 import Template


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def load_renderer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in plugin.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "_render_daily_html")
    namespace = {"logger": logging.getLogger("render-test")}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["_render_daily_html"]


class RenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_path_png_and_fixed_width(self):
        renderer = load_renderer()
        plugin = SimpleNamespace(config={}, html_render=AsyncMock(return_value="daily.png"))
        result = await renderer(plugin, "<html>daily</html>")
        self.assertEqual(result, "daily.png")
        args, kwargs = plugin.html_render.call_args
        self.assertFalse(kwargs["return_url"])
        self.assertEqual(kwargs["options"]["type"], "png")
        self.assertEqual(kwargs["options"]["viewport_width"], 578)
        self.assertTrue(kwargs["options"]["full_page"])
        self.assertEqual(Template(args[0]).render(**args[1]), "<html>daily</html>")

    async def test_scale_levels_and_legacy_values(self):
        renderer = load_renderer()
        for value, expected in [(1, "normal"), (1.3, "high"), (1.8, "ultra"), (5, "ultra")]:
            with self.subTest(value=value):
                plugin = SimpleNamespace(config={"render_dpr": value}, html_render=AsyncMock(return_value="daily.png"))
                await renderer(plugin, "<html></html>")
                self.assertEqual(plugin.html_render.call_args.kwargs["options"]["device_scale_factor_level"], expected)

    async def test_news_template_markers_remain_literal(self):
        renderer = load_renderer()
        plugin = SimpleNamespace(config={}, html_render=AsyncMock(return_value="daily.png"))
        html = "<p>{{ user.name }} {% if example %}literal{% endif %} <b>news</b></p>"
        await renderer(plugin, html)
        args, _ = plugin.html_render.call_args
        self.assertEqual(Template(args[0]).render(**args[1]), html)

    async def test_service_failure_propagates(self):
        renderer = load_renderer()
        plugin = SimpleNamespace(config={}, html_render=AsyncMock(side_effect=RuntimeError("service unavailable")))
        with self.assertLogs("render-test", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "service unavailable"):
                await renderer(plugin, "<html></html>")


if __name__ == "__main__":
    unittest.main()
