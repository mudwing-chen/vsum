# -*- coding: utf-8 -*-
"""--clean 的编辑规范。纯规则、不调模型，所以可复现、能进 CI。

逐字稿（txt/srt/vtt/md 正文）永远不做这些改动；只有 transcript.clean.md 用。
"""
import re

from . import export as _export

FILLER = re.compile(r"(?:嗯+|呃+|啊+|哦+|就是说|那个(?=[，。！？、\s]))")
DUP_PUNCT = re.compile(r"([，。！？、；：])\1+")
CJK_LATIN = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])")
LATIN_CJK = re.compile(r"([A-Za-z0-9])([\u4e00-\u9fff])")


def clean_text(text):
    t = FILLER.sub("", text)
    t = re.sub(r"^[，。！？、；：,.;:！？\s]+", "", t)
    t = DUP_PUNCT.sub(r"\1", t)
    t = CJK_LATIN.sub(r"\1 \2", t)
    t = LATIN_CJK.sub(r"\1 \2", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t and t[-1] not in "。！？!?…":
        t += "。"
    return t


def clean_md(segments):
    cleaned = [{"start": s["start"], "end": s["end"], "text": clean_text(s["text"])} for s in segments]
    # 清洗稿不带 YAML 头（它和逐字稿共用一套元信息，重复写只会两边打架），
    # 也不带「术语修订」附录：这份稿子不改词表，附录只会写「无」。
    return _export.to_md(cleaned, show_corrections=False)
