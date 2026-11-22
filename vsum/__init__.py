# -*- coding: utf-8 -*-
"""vsum：视频/音频 → 可逐秒引用的逐字稿与带出处的摘要。"""

import sys


def _tolerant_stdio():
    """非 UTF-8 控制台（Windows 默认 cp936/cp1252）打印中文会抛 UnicodeEncodeError。

    这里只把错误处理器换成 backslashreplace，不改编码：cp936 下中文照常显示，
    遇到编不出的字符输出 \\uXXXX，而不是让整个进程崩掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


_tolerant_stdio()


__version__ = "0.5.0"

