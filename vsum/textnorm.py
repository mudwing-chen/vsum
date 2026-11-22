# -*- coding: utf-8 -*-
"""繁转简：Whisper 没有正字法约束，实测 13:47 的稿子里 2.5% 的字落成繁体。

映射来自 Unicode Unihan_Variants.txt 的单字符 kSimplifiedVariant（UCD，
https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip）。只收源和目标都在
U+4E00–U+9FFF 的对子，并剔除在简体文本里也合法的字，避免逐字替换改错：
乾坤的「乾」、狼藉的「藉」、於是的「於」。Unihan 本身很保守，没有给
乾/藉/著/覆/瞭 建简化映射。

为什么不用 zhconv：它体积小、效果更好，但许可证是 GPLv2+，本项目是 MIT。
"""
import json
import os

BLOCK = frozenset("於麼麽儘祇牠喫夥佔瞭乾藉著覆")
_MAP = None


def _load():
    global _MAP
    if _MAP is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "t2s.json")
        with open(path, encoding="utf-8") as fh:
            _MAP = {k: v for k, v in json.load(fh).items() if k not in BLOCK}
    return _MAP


def to_simplified(text):
    """逐字转简体。确定性、不改长度、不动标点与拉丁词。"""
    if not text:
        return text
    table = _load()
    return "".join(table.get(ch, ch) for ch in text)


def traditional_chars(text):
    """返回 text 里会被转换的字数（用来在 manifest 里记一笔）。"""
    table = _load()
    return sum(1 for ch in text or "" if ch in table)
