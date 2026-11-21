# -*- coding: utf-8 -*-
"""manifest.json：可复现与可评测的地基。

记录输入/音频的 sha256、完整解码参数、词表 sha256、产物清单与警告。
同一 manifest 的 decode 参数重跑，段数与文本应一致（时间戳 ±0.1s）。
"""
import hashlib
import json
import os

SCHEMA = 1


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def build(input_path, audio_path, decode, segments, outputs, warnings,
          terms_path=None, mode="verbatim", duration=None, version="0.1.0", source_url=None,
          summary=None):
    terms = {"pack": None, "path": None, "sha256": None}
    if terms_path:
        terms = {"pack": os.path.basename(terms_path), "path": terms_path,
                 "sha256": sha256_file(terms_path)}
    return {
        "schema": SCHEMA,
        "vsum_version": version,
        "mode": mode,
        "input": {"path": input_path, "sha256": sha256_file(input_path),
                  "duration": duration, "url": source_url},
        "audio": {"path": audio_path, "sha256": sha256_file(audio_path), "sample_rate": 16000},
        "decode": decode,
        "terms": terms,
        "counts": {"segments": len(segments), "chars": sum(len(s["text"]) for s in segments)},
        "summary": summary or {"mode": "none", "bullets": 0, "dropped": 0},
        "warnings": warnings,
        "outputs": {k: os.path.basename(v) for k, v in outputs.items()},
    }


def write(man, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
