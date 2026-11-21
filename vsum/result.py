# -*- coding: utf-8 -*-
"""result.json：一次运行的机器可读结论。失败时不写半成品（CLI 走退出码）。"""
import json
import os

SCHEMA = 1


def build(ok, out_dir, source, counts, reused, summary, artifacts, sha256,
          warnings, elapsed_sec, version):
    return {
        "schema": SCHEMA,
        "vsum_version": version,
        "ok": bool(ok),
        "out": os.path.abspath(out_dir),
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
    path = os.path.join(out_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write(chr(10))
    return path
