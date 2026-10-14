"""
日期工具模块
用于获取当前日期、星期、农历等信息
"""
from datetime import datetime, timedelta, timezone
from typing import Dict

# 星期映射
BEIJING_TZ = timezone(timedelta(hours=8))


def get_current_datetime() -> datetime:
    """日报日期、节日倒计时和定时推送统一使用北京时间。"""
    return datetime.now(BEIJING_TZ)


WEEKDAYS_CN = {
    0: '星期一',
    1: '星期二',
    2: '星期三',
    3: '星期四',
    4: '星期五',
    5: '星期六',
    6: '星期日'
}


def get_current_date_info() -> Dict[str, str]:
    """
    获取当前日期信息
    
    Returns:
        {
            'week_cn': '星期一',
            'date_str': '2024-01-15',
            'cn_date_str': '腊月初五'
        }
    """
    now = get_current_datetime()
    
    # 星期
    week_cn = WEEKDAYS_CN[now.weekday()]
    
    # 日期字符串
    date_str = now.strftime('%Y-%m-%d')
    
    # 农历日期
    cn_date_str = get_lunar_date(now)
    
    return {
        'week_cn': week_cn,
        'date_str': date_str,
        'cn_date_str': cn_date_str
    }


def get_lunar_date(date_obj: datetime) -> str:
    """
    获取农历日期
    优先使用 zhdate 库，如果未安装则返回"农历未知"
    
    Args:
        date_obj: 日期对象
        
    Returns:
        农历日期字符串，如 '腊月初五'，失败时返回 '农历未知'
    """
    try:
        from zhdate import ZhDate
        # zhdate 内部使用无时区 datetime 运算。
        if date_obj.tzinfo is not None:
            date_obj = date_obj.astimezone(BEIJING_TZ).replace(tzinfo=None)
        lunar = ZhDate.from_datetime(date_obj)
        lunar_months = ['', '正', '二', '三', '四', '五', '六', '七', '八', '九', '十', '冬', '腊']
        month_name = lunar_months[lunar.lunar_month] if lunar.lunar_month < len(lunar_months) else str(lunar.lunar_month)
        if lunar.leap_month:
            month_name = "闰" + month_name
        if lunar.lunar_day == 1:
            day_name = '初一'
        elif lunar.lunar_day <= 10:
            day_names = ['', '初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十']
            day_name = day_names[lunar.lunar_day]
        elif lunar.lunar_day < 20:
            day_name = f"十{['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][lunar.lunar_day - 10]}"
        elif lunar.lunar_day == 20:
            day_name = '二十'
        elif lunar.lunar_day < 30:
            day_name = f"廿{['', '一', '二', '三', '四', '五', '六', '七', '八', '九'][lunar.lunar_day - 20]}"
        else:
            day_name = '三十'
        return f"{month_name}月{day_name}"
    except ImportError:
        return "农历未知"
    except Exception:
        return "农历未知"



