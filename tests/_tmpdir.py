# -*- coding: utf-8 -*-
"""测试用临时目录。

两个坑：
1. 不用 tempfile.mkdtemp：它建 0700 目录，Windows 上某些沙箱里建完自己都写不进去。
2. 系统 TEMP 可能根本不可用（实测这台机器 TEMP 指向的盘掉线后，tempfile 会退化到
   当前目录，于是仓库根上冒出一堆 tmpXXXX/）。所以这里先试系统 temp，写不进去就
   退到仓库内的 .tmp/。
"""
import atexit
import os
import shutil
import tempfile
import uuid

_MADE = []


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inside_repo(path):
    try:
        return os.path.commonpath([os.path.abspath(path), REPO]) == REPO
    except ValueError:
        return False


def _base():
    cand = os.path.join(tempfile.gettempdir(), "vsum-tests")
    # 系统 TEMP 掉线时 gettempdir() 会退化成当前目录 —— 那就是仓库里，别在那儿建
    if not _inside_repo(cand) and _writable(cand):
        return cand
    local = os.path.join(REPO, ".tmp")
    os.makedirs(local, exist_ok=True)
    return local


def mkdtemp(prefix="vsum-"):
    path = os.path.join(_base(), prefix + uuid.uuid4().hex[:10])
    os.makedirs(path, exist_ok=True)
    _MADE.append(path)
    return path


@atexit.register
def _cleanup():
    for p in _MADE:
        shutil.rmtree(p, ignore_errors=True)
