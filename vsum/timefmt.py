# -*- coding: utf-8 -*-
"""时间戳格式：全包只有这一份实现。

不足一小时是 `MM:SS`，满一小时是 `H:MM:SS`（H 不补零，M/S 两位）。解析侧要接受
「可选的小时段」——长视频的行会带小时，正则若只认 `\d{2}:\d{2}` 就会静默匹配不上，
回查门形同失效（见 summary.BULLET）。

舍入统一到最近的一秒（SRT/VTT 舍到最近毫秒再截断显示，规则等价），不再截断：
59.9996s 显示为 01:00，而不是 00:59。
"""


def mmss(sec):
    """秒 -> mm:ss，满一小时给 h:mm:ss。负数/None 按 0；四舍五入到秒。"""
    total = int(round(max(0.0, float(sec or 0.0))))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)
