# -*- coding: utf-8 -*-
"""时间戳格式：全包只有这一份实现。

分钟位不封顶到两位——超过 99 分钟的视频会产出 `100:00` 这种三位分钟。
解析侧一律按「冒号前是分钟，可能不止两位」处理（见 summary.BULLET 的 `\\d{2,}`），
否则长视频的行会静默匹配不上，回查门形同失效。
"""


def mmss(sec):
    """秒 -> mm:ss。负数/None 按 0 处理。"""
    total = int(max(0.0, float(sec or 0.0)))
    m, s = divmod(total, 60)
    return "%02d:%02d" % (m, s)
