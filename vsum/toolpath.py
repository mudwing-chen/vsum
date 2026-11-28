# -*- coding: utf-8 -*-
"""定位外部可执行文件。

`shutil.which()` 在 Windows 上会把当前目录前置到 PATH：在「别人给的、或下载来的」
项目目录里运行 vsum 时，同目录投放的 ffmpeg.exe / nvidia-smi.exe 会被优先执行。
统一走这里，PATH 里的空项与 "." 一律剔除，只认 PATH 上的绝对目录。
"""

import os
import shutil


def search_path():
    raw = (os.environ.get("PATH") or "").split(os.pathsep)
    keep = [p for p in raw if p.strip() and p.strip() not in (".", "..")]
    return os.pathsep.join(keep)


def which(cmd):
    """返回可执行文件的路径，找不到返回 None。带分隔符的参数按路径处理。"""
    if os.path.sep in cmd or (os.path.altsep and os.path.altsep in cmd):
        return os.path.abspath(cmd) if os.path.isfile(cmd) else None
    return shutil.which(cmd, path=search_path())
