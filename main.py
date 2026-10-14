import asyncio
import base64
import os
import re
import tempfile
from datetime import datetime, timedelta, time
from urllib.request import pathname2url

import aiohttp
from jinja2 import Template
from playwright.async_api import async_playwright

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools

from .api.bgm_api import BGMAPI
from .api.bilibili_api import BilibiliAPI
from .api.date_utils import get_current_date_info, get_current_datetime
from .api.hitokoto_api import HitokotoAPI
from .api.holiday_api import HolidayAPI
from .api.ithome_rss import ITHomeRSS
from .api.zaobao_api import ZaobaoAPI


@register("astrbot_plugin_zhenxunribao", "Huahuatgc", "小真寻记者为你献上今日报道！", "1.2.0", "https://github.com/Huahuatgc/astrbot_plugin_zhenxunribao")
class ZhenxunReportPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.template_path = os.path.join(plugin_dir, "daily_news.html")
        self.plugin_dir = plugin_dir

        # 创建共享的 aiohttp ClientSession，供所有 API 类复用
        self.http_session = aiohttp.ClientSession()

        api_token = config.get("api_token", "")
        self.bgm_api = BGMAPI(session=self.http_session)
        self.bilibili_api = BilibiliAPI(session=self.http_session)
        self.hitokoto_api = HitokotoAPI(token=api_token, session=self.http_session)
        self.holiday_api = HolidayAPI(token=api_token, session=self.http_session)
        self.ithome_rss = ITHomeRSS(session=self.http_session)
        self.zaobao_api = ZaobaoAPI(token=api_token, session=self.http_session)

        self.push_task = None
        
        # 群号到 unified_msg_origin 的映射，用于定时推送
        self.group_umo_mapping = {}
        self._load_group_mapping()
        
        # 启动定时推送任务（使用延迟启动，等待平台适配器就绪）
        if config.get("enable_scheduled_push", False):
            asyncio.create_task(self._delayed_start_scheduler())
            logger.info("定时推送任务正在初始化...")

        logger.info("真寻日报插件已加载")

    async def _delayed_start_scheduler(self):
        """延迟启动定时推送调度器"""
        try:
            # 等待 15 秒让系统完全初始化
            await asyncio.sleep(15)
            
            # 取消已存在的旧任务（防止重复）
            if self.push_task and not self.push_task.done():
                self.push_task.cancel()
                try:
                    await self.push_task
                except asyncio.CancelledError:
                    pass
            
            # 确保 HTTP session 可用
            if self.http_session is None or self.http_session.closed:
                self.http_session = aiohttp.ClientSession()
                # 重新初始化 API 客户端的 session
                self._reinit_api_sessions()
            
            self.push_task = asyncio.create_task(self._scheduled_push_task())
            logger.info("定时推送任务已启动（延迟初始化）")
        except Exception as e:
            logger.error(f"启动定时推送任务失败: {e}", exc_info=True)

    def _reinit_api_sessions(self):
        """重新初始化 API 客户端的 session"""
        self.bgm_api.set_session(self.http_session)
        self.bilibili_api.set_session(self.http_session)
        self.hitokoto_api.set_session(self.http_session)
        self.holiday_api.set_session(self.http_session)
        self.ithome_rss.set_session(self.http_session)
        self.zaobao_api.set_session(self.http_session)

    @filter.command("日报")
    async def daily_news(self, event: AstrMessageEvent):
        """生成日报"""
        # 输出 unified_msg_origin 并自动保存映射
        umo = event.unified_msg_origin
        logger.info(f"日报命令触发，unified_msg_origin: {umo}")
        
        # 自动学习群组的 unified_msg_origin
        group_id = self._extract_group_id(umo)
        if group_id and group_id not in self.group_umo_mapping:
            self.group_umo_mapping[group_id] = umo
            self._save_group_mapping()
            logger.info(f"已学习群组 {group_id} 的 unified_msg_origin: {umo}")
        
        image_path = None
        try:
            image_path = await self._generate_daily_image()
            yield event.image_result(image_path)
        except Exception as e:
            logger.error(f"生成日报时出错: {e}", exc_info=True)
            yield event.plain_result(f"生成日报时出错: {str(e)}")
        finally:
            # 清理临时图片文件
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.debug(f"已清理临时图片文件: {image_path}")
                except Exception as e:
                    logger.warning(f"清理临时图片文件失败: {e}")

    @filter.command("日报群组ID")
    async def get_group_id(self, event: AstrMessageEvent):
        """获取当前会话的群组ID，用于配置定时推送"""
        umo = event.unified_msg_origin
        logger.info(f"获取群组ID，unified_msg_origin: {umo}")
        yield event.plain_result(
            f"📋 当前会话信息：\n"
            f"unified_msg_origin: {umo}\n\n"
            f"💡 请将此值添加到插件配置的「定时推送目标群组列表」中"
        )

    async def _generate_daily_image(self) -> str:
        logger.info("开始生成日报")

        max_anime_count = self.config.get("max_anime_count", 4)
        max_news_count = self.config.get("max_news_count", 5)
        max_hotword_count = self.config.get("max_hotword_count", 4)
        max_holiday_count = self.config.get("max_holiday_count", 3)

        date_info = get_current_date_info()

        anime_list, bili_hotwords, hitokoto_data, moyu_list, world_news, it_news = (
            await self._fetch_all_data(
                max_anime_count=max_anime_count,
                max_news_count=max_news_count,
                max_hotword_count=max_hotword_count,
                max_holiday_count=max_holiday_count,
            )
        )

        template_data = {
            "date_info": date_info,
            "anime_list": anime_list or [],
            "bili_hotwords": bili_hotwords or [],
            "hitokoto_data": hitokoto_data or {"hitokoto": "暂无", "from": "未知"},
            "moyu_list": moyu_list or [],
            "world_news": world_news or [],
            "it_news": it_news or [],
        }

        logger.info(
            f"模板数据准备完成: 新番={len(template_data['anime_list'])}, "
            f"热点={len(template_data['bili_hotwords'])}, "
            f"节假日={len(template_data['moyu_list'])}, "
            f"世界新闻={len(template_data['world_news'])}, "
            f"IT新闻={len(template_data['it_news'])}"
        )

        try:
            with open(self.template_path, "r", encoding="utf-8") as f:
                html_template_str = f.read()
        except Exception as e:
            logger.error(f"读取模板文件失败: {e}", exc_info=True)
            raise

        template = Template(html_template_str)
        rendered_html = template.render(**template_data)
        rendered_html = await self._embed_resources(rendered_html)

        style_fix = """
html, body {
  width: 578px;
  margin: 0;
  padding: 0;
  overflow-x: hidden;
}
"""
        rendered_html = rendered_html.replace("</style>", style_fix + "</style>", 1)

        image_path = await self._render_html_with_playwright(rendered_html)
        logger.info("日报生成成功")
        return image_path
    
    async def _fetch_all_data(
        self,
        max_anime_count: int,
        max_news_count: int,
        max_hotword_count: int,
        max_holiday_count: int,
    ):
        results = await asyncio.gather(
            self.bgm_api.get_today_anime_async(max_count=max_anime_count),
            self.bilibili_api.get_hotwords_async(max_count=max_hotword_count),
            self.hitokoto_api.get_hitokoto_async(),
            self.holiday_api.get_moyu_list_async(max_count=max_holiday_count),
            self.zaobao_api.get_world_news_async(max_count=max_news_count),
            self.ithome_rss.get_it_news_async(max_count=max_news_count),
            return_exceptions=True,
        )

        anime_list = results[0] if not isinstance(results[0], Exception) else []
        bili_hotwords = results[1] if not isinstance(results[1], Exception) else []
        hitokoto_data = (
            results[2]
            if not isinstance(results[2], Exception)
            else {"hitokoto": "暂无", "from": "未知"}
        )
        moyu_list = results[3] if not isinstance(results[3], Exception) else []
        world_news = results[4] if not isinstance(results[4], Exception) else []
        it_news = results[5] if not isinstance(results[5], Exception) else []

        if isinstance(hitokoto_data, dict):
            from_value = hitokoto_data.get("from", "未知")
            if not from_value or from_value.strip() == "" or from_value.strip() == "网络":
                hitokoto_data["from"] = "佚名"
            else:
                hitokoto_data["from"] = from_value.strip()

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"获取数据时出错 (索引 {i}): {result}")

        return anime_list, bili_hotwords, hitokoto_data, moyu_list, world_news, it_news

    def _file_to_base64(self, file_path: str) -> str | None:
        try:
            if not os.path.exists(file_path):
                logger.warning(f"资源文件不存在: {file_path}")
                return None

            with open(file_path, "rb") as f:
                file_data = f.read()
                base64_data = base64.b64encode(file_data).decode("utf-8")

                ext = os.path.splitext(file_path)[1].lower()
                mime_types = {
                    ".otf": "font/opentype",
                    ".ttf": "font/ttf",
                    ".woff": "font/woff",
                    ".woff2": "font/woff2",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".svg": "image/svg+xml",
                }
                mime_type = mime_types.get(ext, "application/octet-stream")

                return f"data:{mime_type};base64,{base64_data}"
        except Exception as e:
            logger.warning(f"转换文件到base64失败 {file_path}: {e}")
            return None

    async def _embed_resources(self, html_template: str) -> str:
        def replace_font(match):
            filename = match.group(1)
            file_path = os.path.join(self.plugin_dir, "res", "font", filename)
            base64_uri = self._file_to_base64(file_path)
            if base64_uri:
                return f'url("{base64_uri}")'
            return match.group(0)

        html_template = re.sub(
            r'url\(["\']?\./res/font/([^"\')]+)["\']?\)',
            replace_font,
            html_template,
            flags=re.IGNORECASE,
        )

        def replace_image(match):
            filepath = match.group(1)
            if filepath.startswith("icon/") or filepath.startswith("image/"):
                file_path = os.path.join(self.plugin_dir, "res", filepath)
                base64_uri = self._file_to_base64(file_path)
                if base64_uri:
                    logger.debug(f"转换图片为base64: {filepath}")
                    return f'src="{base64_uri}"'
                else:
                    logger.warning(f"图片转换为base64失败: {filepath}")
            return match.group(0)

        html_template = re.sub(
            r'src=["\']\./res/([^"\']+)["\']',
            replace_image,
            html_template,
            flags=re.IGNORECASE,
        )

        return html_template

    async def _render_html_with_playwright(
        self, html_content: str, output_path: str | None = None
    ) -> str:
        """Render HTML to PNG using Playwright.

        提升清晰度的关键：使用 BrowserContext 的 device_scale_factor (DPR)。
        """
        temp_html_path = None
        context = None
        try:
            temp_dir = tempfile.gettempdir()
            temp_html_path = os.path.join(
                temp_dir,
                f"ripan_daily_{os.getpid()}_{hash(html_content) % 100000}.html",
            )
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            if output_path is None:
                output_path = temp_html_path.replace(".html", ".png")

            # DPR (device scale factor): 越大越清晰，但图片更大、渲染更慢
            dpr = int(self.config.get("render_dpr", 4))
            dpr = max(1, min(dpr, 6))

            async with async_playwright() as p:
                logger.info("启动Playwright浏览器...")
                browser = await p.chromium.launch(headless=True)
                try:
                    # 用 context 设置 DPR 提升截图清晰度
                    context = await browser.new_context(
                        viewport={"width": 1156, "height": 1000},
                        device_scale_factor=dpr,
                    )
                    page = await context.new_page()

                    file_url = f"file://{pathname2url(temp_html_path)}"
                    await page.goto(file_url, wait_until="networkidle")
                    await page.wait_for_timeout(2000)

                    wrapper = await page.query_selector(".wrapper")
                    if not wrapper:
                        raise Exception("未找到.wrapper元素")

                    box = await wrapper.bounding_box()
                    if not box:
                        raise Exception("无法获取.wrapper元素的bounding box")

                    wrapper_width = int(box["width"])
                    wrapper_height = int(box["height"])

                    # 动态设置 viewport，避免超长内容截图不完整（留余量）
                    viewport_height = max(int(wrapper_height * 1.2), 1000)
                    viewport_width = 1156
                    await page.set_viewport_size(
                        {"width": viewport_width, "height": viewport_height}
                    )

                    # viewport 调整后重新查询元素
                    wrapper = await page.query_selector(".wrapper")
                    if not wrapper:
                        raise Exception("未找到.wrapper元素(viewport调整后)")

                    logger.info(
                        f"Wrapper宽高: {wrapper_width}x{wrapper_height}, "
                        f"viewport: {viewport_width}x{viewport_height}, DPR={dpr}"
                    )

# 使用 clip 精确裁剪，避免 body absolute 定位导致的大片空白
                    clip = {
                        "x": int(box["x"]),
                        "y": int(box["y"]),
                        "width": int(box["width"]),
                        "height": int(box["height"]),
                    }
                    await page.screenshot(
                        path=output_path,
                        type="png",
                        clip=clip,
                    )

                    logger.info(f"截图完成: {output_path}")
                    return output_path
                finally:
                    try:
                        if context:
                            await context.close()
                    finally:
                        await browser.close()

        except Exception as e:
            logger.error(f"Playwright渲染失败: {e}", exc_info=True)
            raise
        finally:
            if temp_html_path and os.path.exists(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except Exception as e:
                    logger.warning(f"删除临时HTML文件失败: {e}")

    async def _scheduled_push_task(self):
        while True:
            try:
                push_time_str = self.config.get("scheduled_push_time", "08:00")
                push_groups = self.config.get("scheduled_push_groups", [])

                if not push_groups:
                    logger.warning("定时推送已启用，但未配置目标群组，跳过本次推送")
                    await asyncio.sleep(3600)
                    continue

                try:
                    hour, minute = map(int, push_time_str.split(":"))
                    push_time = time(hour, minute)
                except (ValueError, AttributeError):
                    logger.error(
                        f"定时推送时间格式错误: {push_time_str}，使用默认时间08:00"
                    )
                    push_time = time(8, 0)

                now = get_current_datetime()
                next_push = datetime.combine(now.date(), push_time, tzinfo=now.tzinfo)

                if next_push <= now:
                    next_push += timedelta(days=1)

                wait_seconds = (next_push - now).total_seconds()

                logger.info(
                    f"定时推送任务已启动，下次推送时间: {next_push.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await asyncio.sleep(wait_seconds)

                logger.info("开始执行定时推送")
                await self._push_daily_to_groups(push_groups)

            except asyncio.CancelledError:
                logger.info("定时推送任务已取消")
                break
            except Exception as e:
                logger.error(f"定时推送任务出错: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _push_daily_to_groups(self, group_list: list):
        """向指定群组推送日报 - 直接使用 OneBot API"""
        image_path = None
        try:
            logger.info(f"开始生成日报图片，目标群组数量: {len(group_list)}")
            image_path = await self._generate_daily_image()
            
            # 验证图片文件存在
            if not image_path or not os.path.exists(image_path):
                logger.error(f"日报图片生成失败或文件不存在: {image_path}")
                return
            
            logger.info(f"日报图片生成成功: {image_path}")
            
            # 将图片转为 base64
            with open(image_path, 'rb') as f:
                image_data = f.read()
            image_b64 = base64.b64encode(image_data).decode()

            success_count = 0
            
            for group_id in group_list:
                try:
                    # 提取纯群号
                    clean_group_id = self._extract_group_id(group_id)
                    logger.debug(f"正在向群组 {clean_group_id} 发送日报...")
                    
                    # 使用底层 API 直接发送
                    result = await self._send_group_msg_via_api(clean_group_id, image_b64)
                    if result:
                        logger.info(f"成功推送日报到群组: {clean_group_id}")
                        success_count += 1
                    else:
                        # 回退：尝试使用已学习的映射
                        umo = self.group_umo_mapping.get(clean_group_id)
                        if umo:
                            logger.debug(f"尝试使用映射发送: {umo}")
                            message_chain = MessageChain().file_image(image_path)
                            fallback_result = await self.context.send_message(umo, message_chain)
                            if fallback_result:
                                logger.info(f"成功推送日报到群组(映射方式): {clean_group_id}")
                                success_count += 1
                            else:
                                logger.warning(f"推送失败，群组: {clean_group_id}")
                        else:
                            logger.warning(f"推送失败，群组: {clean_group_id}")
                    
                except Exception as e:
                    logger.error(f"推送到群组 {group_id} 时出错: {e}", exc_info=True)

            logger.info(f"定时推送完成，成功: {success_count}/{len(group_list)}")

        except Exception as e:
            logger.error(f"定时推送日报失败: {e}", exc_info=True)
            # 清理临时文件
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.debug(f"已清理临时图片文件: {image_path}")
                except Exception as e:
                    logger.warning(f"清理临时图片文件失败: {e}")

    def _load_group_mapping(self):
        """从文件加载群号到 unified_msg_origin 的映射"""
        try:
            import json
            # 使用标准数据目录，避免写入插件源码目录
            data_dir = StarTools.get_data_dir("astrbot_plugin_zhenxunribao")
            mapping_file = os.path.join(data_dir, "group_mapping.json")
            if os.path.exists(mapping_file):
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    self.group_umo_mapping = json.load(f)
                logger.info(f"已加载 {len(self.group_umo_mapping)} 个群组映射")
        except Exception as e:
            logger.warning(f"加载群组映射失败: {e}")
            self.group_umo_mapping = {}

    def _save_group_mapping(self):
        """保存群号到 unified_msg_origin 的映射到文件"""
        try:
            import json
            # 使用标准数据目录，避免写入插件源码目录
            data_dir = StarTools.get_data_dir("astrbot_plugin_zhenxunribao")
            mapping_file = os.path.join(data_dir, "group_mapping.json")
            with open(mapping_file, 'w', encoding='utf-8') as f:
                json.dump(self.group_umo_mapping, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存 {len(self.group_umo_mapping)} 个群组映射")
        except Exception as e:
            logger.warning(f"保存群组映射失败: {e}")

    def _extract_group_id(self, group_id_str: str) -> str:
        """从配置中提取纯群号，支持多种格式"""
        group_id_str = str(group_id_str).strip()
        
        # 如果是纯数字，直接返回
        if group_id_str.isdigit():
            return group_id_str
        
        # 尝试从 unified_msg_origin 格式中提取群号
        # 格式如: aiocqhttp:GroupMessage:123456789 或 default:GroupMessage:xxx_123456789
        if ':' in group_id_str:
            parts = group_id_str.split(':')
            if len(parts) >= 3:
                last_part = parts[-1]
                # 处理可能的 botid_groupid 格式
                if '_' in last_part:
                    return last_part.split('_')[-1]
                return last_part
        
        return group_id_str

    async def _generate_greeting_text(self) -> str:
        """使用 AI 生成个性化的推送文本"""
        try:
            # 获取当前时间和节日信息
            now = get_current_datetime()
            hour = now.hour
            date_info = get_current_date_info()
            
            # 获取节假日信息
            moyu_list = []
            try:
                holiday_data = await self.holiday_api.get_moyu_list_async(max_count=1)
                if holiday_data and len(holiday_data) > 0:
                    moyu_list = holiday_data
            except:
                pass
            
            # 检查是否启用 AI 生成问候语
            if not self.config.get("enable_ai_greeting", False):
                return self._get_default_greeting(hour, moyu_list)
            
            # 构建 prompt
            prompt_parts = [
                f"现在是{date_info['date_str']} {date_info['week_cn']}",
                f"时间是{hour}点",
            ]
            
            if moyu_list:
                holiday_names = [h.get('name', '') for h in moyu_list if h.get('name')]
                if holiday_names:
                    prompt_parts.append(f"即将到来的节日：{', '.join(holiday_names[:2])}")
            
            if date_info.get('cn_date_str') and date_info.get('cn_date_str') != '农历未知':
                prompt_parts.append(f"农历{date_info['cn_date_str']}")
            
            prompt = (
                f"{', '.join(prompt_parts)}。"
                f"请生成一句简短（15字以内）、温馨且富有创意的日报推送问候语。"
                f"要求：1. 结合时间或节日 2. 亲切自然 3. 带上真寻的口吻 4. 只返回问候语文本，不要其他内容"
            )
            
            # 尝试获取 LLM 提供商
            try:
                # 获取默认的聊天提供商
                umo_for_provider = None
                # 尝试从已学习的群映射里取一个会话ID，以便获取当前会话默认聊天模型
                if self.group_umo_mapping:
                    umo_for_provider = next(iter(self.group_umo_mapping.values()))
                provider_id = await self.context.get_current_chat_provider_id(umo=umo_for_provider) if umo_for_provider else None
                if not provider_id:
                    # 如果没有，尝试获取所有提供商中的第一个
                    providers = self.context.provider_manager.get_all_providers()
                    if providers:
                        provider_id = list(providers.keys())[0]
                
                if provider_id:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=prompt,
                    )
                    
                    if llm_resp and hasattr(llm_resp, 'completion_text'):
                        greeting = llm_resp.completion_text.strip()
                        # 清理可能的引号
                        greeting = greeting.strip('"').strip("'").strip()
                        if greeting and len(greeting) <= 50:
                            logger.info(f"AI 生成问候语: {greeting}")
                            return f"📰 {greeting}\n"
            except Exception as e:
                logger.debug(f"AI 生成问候语失败: {e}")
            
            # 回退到默认问候语
            return self._get_default_greeting(hour, moyu_list)
            
        except Exception as e:
            logger.warning(f"生成问候语出错: {e}")
            return "📰 真寻日报来啦~\n"

    def _get_default_greeting(self, hour: int, moyu_list: list) -> str:
        """获取默认问候语（无 AI 时使用）"""
        # 根据时间段选择问候语
        greetings = {
            "morning": ["早安！新的一天开始啦~", "早上好！今日份日报送达~", "早安！美好的一天从日报开始~"],
            "noon": ["中午好！午间日报来啦~", "中午好~来看看今天的资讯吧~", "午安！休息时刻看看日报~"],
            "afternoon": ["下午好！日报新鲜出炉~", "下午茶时间，看看日报吧~", "下午好！今日资讯已备好~"],
            "evening": ["晚上好！晚间日报送达~", "晚上好~睡前看看今日资讯吧~", "晚安前的日报时间~"],
        }
        
        # 判断时间段
        if 5 <= hour < 11:
            period_greetings = greetings["morning"]
        elif 11 <= hour < 14:
            period_greetings = greetings["noon"]
        elif 14 <= hour < 18:
            period_greetings = greetings["afternoon"]
        else:
            period_greetings = greetings["evening"]
        
        # 如果有节日信息，添加节日问候
        if moyu_list and len(moyu_list) > 0:
            holiday = moyu_list[0]
            if holiday.get('name'):
                days_left = holiday.get('days_left')
                if days_left == 0:
                    return f"📰 {holiday['name']}快乐！日报送上~\n"
                elif isinstance(days_left, int) and 0 < days_left <= 3:
                    return f"📰 距离{holiday['name']}还有{days_left}天！日报来啦~\n"
        
        # 随机选择一个问候语
        import random
        return f"📰 {random.choice(period_greetings)}\n"

    async def _send_group_msg_via_api(self, group_id: str, image_b64: str) -> bool:
        """使用 OneBot API 直接发送群消息"""
        try:
            # 生成个性化问候语
            greeting_text = await self._generate_greeting_text()
            
            # 通过 platform_manager 获取所有平台实例
            if not hasattr(self.context, 'platform_manager'):
                logger.warning("context 没有 platform_manager 属性")
                return False
            
            platforms = self.context.platform_manager.get_insts()
            if not platforms:
                logger.warning("没有可用的平台实例")
                return False
            
            logger.debug(f"发现 {len(platforms)} 个平台实例")
            
            # 遍历所有平台尝试发送
            for platform in platforms:
                try:
                    # 获取 bot 客户端
                    bot_client = None
                    if hasattr(platform, 'get_client'):
                        bot_client = platform.get_client()
                    elif hasattr(platform, 'client'):
                        bot_client = platform.client
                    elif hasattr(platform, 'bot'):
                        bot_client = platform.bot
                    
                    if not bot_client:
                        continue
                    
                    # 获取 call_action 方法
                    call_action = None
                    if hasattr(bot_client, 'call_action'):
                        call_action = bot_client.call_action
                    elif hasattr(bot_client, 'api') and hasattr(bot_client.api, 'call_action'):
                        call_action = bot_client.api.call_action
                    
                    if not call_action:
                        continue
                    
                    # 调用 OneBot API 发送群消息
                    await call_action(
                        "send_group_msg",
                        group_id=int(group_id),
                        message=[
                            {"type": "text", "data": {"text": greeting_text}},
                            {"type": "image", "data": {"file": f"base64://{image_b64}"}}
                        ]
                    )
                    logger.info(f"通过 OneBot API 成功发送到群 {group_id}")
                    return True
                    
                except Exception as e:
                    error_msg = str(e)
                    if "retcode=1200" in error_msg:
                        logger.debug(f"平台不在群 {group_id} 中，继续尝试其他平台")
                    else:
                        logger.debug(f"平台发送失败: {e}")
                    continue
            
            logger.warning(f"所有平台都无法发送到群 {group_id}")
            return False
            
        except Exception as e:
            logger.error(f"发送群消息失败: {e}")
            return False

    async def terminate(self):
        logger.info("真寻日报插件正在卸载...")
        # 取消定时推送任务
        if self.push_task and not self.push_task.done():
            self.push_task.cancel()
            try:
                await self.push_task
            except asyncio.CancelledError:
                pass
            logger.info("定时推送任务已取消")
        # 关闭共享的 HTTP session
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.info("HTTP session 已关闭")



