# -*- coding: utf-8 -*-
"""result.json：一次运行的机器可读结论。失败时不写半成品（CLI 走退出码）。"""
import os

from . import atomic

SCHEMA = 1


def build(ok, out_dir, source, counts, reused, summary, artifacts, sha256,
          warnings, elapsed_sec, version):
    return {
        "schema": SCHEMA,
        "vsum_version": version,
        "ok": bool(ok),
        # 只记目录名：result.json 会被随产物一起发出去，绝对路径会顺带泄露用户名
        "out": os.path.basename(os.path.abspath(out_dir)),
        "source": source,
        "counts": counts,
        "reused": bool(reused),
        "summary": summary,
        "artifacts": artifacts,
        "sha256": sha256,
        "warnings": list(warnings),
        "elapsed_sec": round(float(elapsed_sec), 1),
    }


def write(result, out_dir):
    # 原子写：读到半截 result.json 的脚本会把坏 JSON 当「成功」或直接崩
    return atomic.write_json(os.path.join(out_dir, "result.json"), result)
