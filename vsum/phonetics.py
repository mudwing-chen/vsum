# -*- coding: utf-8 -*-
"""Soundex：把英文词压成辅音骨架，用来找回编辑距离够不到的变体。

只用来生成候选、给人审，绝不直接改文本。
"""
CODES = {
    "b": "1", "f": "1", "p": "1", "v": "1",
    "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
    "d": "3", "t": "3",
    "l": "4",
    "m": "5", "n": "5",
    "r": "6",
}


def soundex(word):
    """ASCII 字母按 Soundex 编码；混了非 ASCII 就返回空串，不做猜测。"""
    if any(ord(c) > 127 for c in word):
        return ""
    letters = [c for c in word.lower() if "a" <= c <= "z"]
    if not letters:
        return ""
    first = letters[0].upper()
    prev = CODES.get(letters[0], "")
    out = []
    for ch in letters[1:]:
        code = CODES.get(ch, "")
        if code and code != prev:
            out.append(code)
        prev = code
    return (first + "".join(out) + "000")[:4]
