# 真寻日报 (astrbot_plugin_zhenxunribao)

✨ 基于 AstrBot 的一个插件 ✨

小真寻记者为你献上今天报道！

> **小真寻也很可爱呀，也会很喜欢你！**

## 📖 介绍

这是一个从 [nonebot-plugin-zxreport](https://github.com/HibiKier/nonebot-plugin-zxreport) 移植到 AstrBot 的真寻日报插件。插件会每日为你汇总最新的资讯内容，包含今日新番、B站热点、世界新闻、IT资讯、摸鱼日历和今日一言等内容。

## 💿 安装

### 通过 AstrBot 插件市场安装（推荐）

1. 在 AstrBot WebUI 中打开插件市场
2. 搜索 `astrbot_plugin_zhenxunribao` 或 `真寻日报`
3. 点击安装

### 手动安装

1. 克隆仓库到 AstrBot 插件目录：
```bash
cd AstrBot/data/plugins
git clone https://github.com/luminacry/astrbot_plugin_zhenxunribao
```

2. 安装依赖：
```bash
cd astrbot_plugin_zhenxunribao
pip install -r requirements.txt
playwright install chromium
```

3. 在 AstrBot WebUI 的插件管理中启用插件

## ⚙️ 配置

在 AstrBot WebUI 的插件配置页面进行配置：

| 配置 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_token` | str | `""` | ALAPI Token，用于节假日、今日一言、早报API。在 [https://admin.alapi.cn/user/login](https://admin.alapi.cn/user/login) 登录后获取token |
| `max_anime_count` | int | `4` | 今日新番最大显示数量，建议设置为4-8之间 |
| `max_news_count` | int | `5` | 新闻最大显示数量，建议设置为5-10之间 |
| `max_hotword_count` | int | `4` | B站热点最大显示数量，建议设置为4-8之间 |
| `max_holiday_count` | int | `3` | 摸鱼日历最大显示数量，建议设置为3-5之间 |
| `render_dpr` | int | `5` | 渲染清晰度（DPR），越大越清晰但图片更大更慢，建议 3-6 |
| `enable_scheduled_push` | bool | `false` | 是否启用定时推送，启用后会在指定时间自动推送日报到配置的群组 |
| `scheduled_push_time` | str | `"08:00"` | 定时推送时间，HH:MM格式（北京时间），例如：`08:00` 表示北京时间每天早上8点 |
| `scheduled_push_groups` | list | `[]` | 定时推送目标群组列表，直接填写群号即可，如：`["957880653", "123456789"]` |
| `enable_ai_greeting` | bool | `false` | 是否启用 AI 生成个性化问候语，启用后会调用 AstrBot 当前配置的大模型生成推送问候语 |

## 🎁 使用

### 手动生成日报

在QQ群或其他支持的平台中发送指令：
```
/日报
```

机器人将自动生成并发送当日日报图片。

### 获取群组ID（可选）

如果直接填写群号无法推送，可以在群内发送：
```
/日报群组ID
```

机器人会返回当前会话的完整标识，将其添加到配置中即可。

### 定时推送

1. 在插件配置中启用 `enable_scheduled_push`
2. 设置 `scheduled_push_time`（推送时间，默认 08:00）
3. 在 `scheduled_push_groups` 中填写目标群号，如：`["957880653"]`
4. 保存配置并重载插件，定时任务将自动启动

### 摸鱼日历数据说明

- 优先查询 ALAPI 当年及次年的放假安排，同名连续假期只显示最近一天；正在放假时显示“今天是”。
- 次年安排尚未公布、Token 未配置或接口失败时，使用本地计算的元旦、春节、清明节、劳动节、端午节、中秋节、国庆节日期。标有“（节日）”的条目是节日当天，不代表官方放假起始日或调休安排；清明本地公式适用 2000–2099 年。
- 周末按普通周六、周日计算，周六和周日均显示“今天是周末”，不判断周末调休补班。
- `max_holiday_count` 限制整个摸鱼日历的条目数量（含周末），所有数据按剩余天数排序；设为 `0` 时不显示条目。
- 日报日期、倒计时、定时推送均使用北京时间（UTC+8），不受服务器所在时区影响。
- 农历节日复用原有 `zhdate` 依赖，Token 仍用于其他内容。接口成功响应缓存一小时。

## 📋 依赖

- `aiohttp>=3.8.0` - 异步HTTP请求库
- `jinja2>=3.0.0` - HTML模板渲染引擎
- `playwright>=1.40.0` - 浏览器自动化，用于HTML转图片
- `zhdate>=0.1` - 农历日期计算支持

安装 Playwright 浏览器：
```bash
playwright install chromium
```

## 🖋 字体说明

本插件渲染日报图片时使用了 **HarmonyOS Sans** 字体文件以提升跨系统一致性与清晰度。


## ⚠️ 注意事项

1. **API Token 配置**：部分功能（节假日、今日一言、早报）需要配置 ALAPI Token，否则相关数据可能无法获取
2. **Playwright 安装**：首次使用需要安装 Playwright 的 Chromium 浏览器，执行 `playwright install chromium`
3. **网络环境**：插件需要访问多个外部API，请确保网络连接正常
4. **群组ID获取**：配置定时推送时，可以通过在目标群内发送 `/日报` 后查看日志获取正确的群组ID格式

## 🛠️ 技术实现

- 使用 **Jinja2** 渲染HTML模板
- 使用 **Playwright** 进行HTML到图片的转换，支持2倍分辨率高清渲染
- 使用 **aiohttp** 异步获取多个数据源
- 资源文件通过 Base64 编码嵌入HTML，确保图片和字体正常显示

## 📝 功能特性

- 📺 **今日新番** - 显示今日更新的动画番剧信息
- 🔥 **B站热点** - 汇总B站当前热门内容
- 🌍 **世界新闻** - 获取最新的国际新闻资讯
- 💻 **IT资讯** - IT之家最新科技资讯
- 🐟 **摸鱼日历** - 显示节假日和重要日期提醒
- 💬 **今日一言** - 每日一句精美文案

## 📝 更新日志

- 最新版本：`1.2.0`
- 详细变更见 `CHANGELOG.md`

## 📄 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。

## ❤ 致谢

- [nonebot-plugin-zxreport](https://github.com/HibiKier/nonebot-plugin-zxreport) - 原始项目，由 [HibiKier](https://github.com/HibiKier) 开发
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 优秀的机器人框架
- [ALAPI](https://www.alapi.cn/) - 提供API服务
- [Bangumi](https://bgm.tv/) - 番剧数据来源
- [IT之家](https://www.ithome.com/) - IT资讯来源

## 📮 反馈与建议

如有问题或建议，欢迎提交 Issue 或 Pull Request！

仓库地址：[https://github.com/Huahuatgc/astrbot_plugin_zhenxunribao](https://github.com/Huahuatgc/astrbot_plugin_zhenxunribao)


