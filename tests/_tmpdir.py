# -*- coding: utf-8 -*-
"""测试用临时目录：不用 tempfile.mkdtemp（它建 0700 目录，Windows 上某些沙箱里
建完自己都写不进去），改用普通 makedirs + 收尾清理。"""
import atexit
import os
import shutil
import tempfile
import uuid

_MADE = []


def mkdtemp(prefix="vsum-"):
    path = os.path.join(tempfile.gettempdir(), prefix + uuid.uuid4().hex[:10])
    os.makedirs(path, exist_ok=True)
    _MADE.append(path)
    return path


@atexit.register
def _cleanup():
    for p in _MADE:
        shutil.rmtree(p, ignore_errors=True)
