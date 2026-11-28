# -*- coding: utf-8 -*-
"""原子写盘：先写同目录临时文件，再 os.replace 覆盖。

产物目录会被别的进程读（batch 靠 result.json 判断成功），也会被用户直接打开；
写到一半失败或进程被杀，不能留下半截文件，更不能出现「新内容 + 旧 result.json」。
"""

import json
import os
import tempfile


def write_text(path, text, encoding="utf-8"):
    path = os.path.abspath(path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def write_json(path, obj, indent=1):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")
