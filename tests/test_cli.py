#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 与 eval 的回归：退出码、CER、参考文本匹配、manifest 自洽。不跑模型。"""
import json
import os
import subprocess
import sys
import tempfile

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import evaluate, manifest as manifest_mod  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


def run(*argv):
    return subprocess.run([sys.executable, "-m", "vsum"] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


check("CER 全等为 0", evaluate.cer("abc", "abc") == 0.0, evaluate.cer("abc", "abc"))
check("CER 一换一", abs(evaluate.cer("abc", "abd") - 1 / 3) < 1e-9, evaluate.cer("abc", "abd"))
check("CER 标点空白不计", evaluate.cer("用 cloud 写代码。", "用cloud写代码") == 0.0,
      evaluate.cer("用 cloud 写代码。", "用cloud写代码"))
check("CER 数字不转写", evaluate.cer("三千五", "3500") > 0.5, evaluate.cer("三千五", "3500"))
check("Wilson 0/15", evaluate.wilson(0, 15) == (0.0, 1.0) or evaluate.wilson(0, 15)[0] == 0.0)

tmp = mkdtemp()
segs_path = os.path.join(HERE, "fixtures", "segments", "demo.json")
segs = json.load(open(segs_path, encoding="utf-8"))["segments"]
with open(os.path.join(tmp, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": segs}, fh, ensure_ascii=False)
with open(os.path.join(tmp, "transcript.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join("[%6.1f -> %6.1f] %s" % (s["start"], s["end"], s["text"]) for s in segs))

terms = os.path.join(ROOT, "vsum", "data", "terms.json")
man = {
    "schema": 1, "vsum_version": "0.1.0", "mode": "verbatim",
    "input": {"path": "demo.mp4", "sha256": "0" * 64, "duration": 19.0},
    "audio": {"path": "audio.wav", "sha256": "0" * 64, "sample_rate": 16000},
    "decode": {"model": "medium", "device": "cpu", "compute_type": "int8", "language": "zh",
               "beam_size": 5, "vad_filter": True, "condition_on_previous_text": False,
               "temperature": [0.0], "initial_prompt": "x", "hotwords": False, "word_timestamps": False},
    "terms": {"pack": None, "path": terms, "sha256": manifest_mod.sha256_file(terms)},
    "counts": {"segments": len(segs), "chars": sum(len(s["text"]) for s in segs)},
    "warnings": {"long_cues": [3], "empty_segments": 0},
    "outputs": {"txt": "transcript.txt", "srt": "transcript.srt", "vtt": "transcript.vtt",
                "md": "transcript.md", "plain": "transcript.plain.txt", "segments": "segments.json"},
}
man_path = os.path.join(tmp, "manifest.json")
manifest_mod.write(man, man_path)

ref_dir = os.path.join(HERE, "fixtures", "subtitles")
rc = evaluate.run(man_path, ref_dir)
check("eval 同文 CER 0 退出 0", rc == 0, rc)
rc_gate = evaluate.run(man_path, ref_dir, min_cer=0.0)
check("min-cer 门槛", rc_gate == 0, rc_gate)
rc_bad = evaluate.run(man_path, ref_dir, min_cer=-0.01)
check("min-cer 不达标退出 5", rc_bad == 5, rc_bad)

proc = run("eval", "--manifest", man_path, "--ref", ref_dir)
check("eval 子命令退出 0", proc.returncode == 0, proc.stderr)
check("eval 打印 CER", "CER" in proc.stdout, proc.stdout)
check("eval 打印术语召回", "term recall" in proc.stdout, proc.stdout)

bad = run(os.path.join(tmp, "nope.mp4"), "-o", os.path.join(tmp, "out"))
check("缺文件退出 2", bad.returncode == 2, (bad.returncode, bad.stderr))
url = run("https://example.com/nope", "-o", os.path.join(tmp, "out2"))
check("不认识的链接退出 4", url.returncode == 4, (url.returncode, url.stderr))

check("export 缺 segments 退出 2", run("export", "--segments", os.path.join(tmp, "nope.json"),
                                         "-o", os.path.join(tmp, "o3")).returncode == 2)
check("eval 缺 manifest 退出 2", run("eval", "--manifest", os.path.join(tmp, "nope.json")).returncode == 2)
check("audit 目录不存在退出 2", run("audit", os.path.join(tmp, "nope-dir")).returncode == 2)
check("fix 路径不存在退出 2", run("fix", os.path.join(tmp, "nope-path")).returncode == 2)

doc = run("doctor")
check("doctor 不崩", doc.returncode in (0, 3), (doc.returncode, doc.stderr))
check("doctor 有清单", "ffmpeg" in doc.stdout, doc.stdout[:80])

# 没有音频流的输入要在抽音频前就报「没有东西可转写」，而不是抛 ffmpeg 的英文原文
from vsum import pipeline  # noqa: E402

_which = pipeline.shutil.which
pipeline.shutil.which = lambda name: None
try:
    check("没 ffprobe 时音频探测返回 None（不阻塞）", pipeline.has_audio_stream("x.mp4") is None)
finally:
    pipeline.shutil.which = _which

# Windows 控制台默认 cp936/cp1252：命令不能因为打印中文崩掉，管道输出要统一 UTF-8
loop_dir = os.path.join(HERE, "fixtures", "loop")
cp = subprocess.run([sys.executable, "-m", "vsum", "fix", loop_dir], cwd=ROOT,
                    capture_output=True,
                    env=dict(os.environ, PYTHONIOENCODING="cp1252"))
cp_out = cp.stdout.decode("utf-8", "replace")
check("cp1252 环境不崩", cp.returncode == 0, (cp.returncode, cp_out[-80:]))
check("管道输出 UTF-8 中文可读", "命中" in cp_out, cp_out[-80:])

# 帮助入口：总览必须和真实分发表一一对应，否则用户看到的命令会名存实亡
from vsum import cli as cli_mod  # noqa: E402

listed = set(c[0] for c in cli_mod.COMMANDS)
check("命令总览与 SUB 一一对应", listed == set(cli_mod.SUB), sorted(listed ^ set(cli_mod.SUB)))

help_out = run("--help")
check("--help 退出 0", help_out.returncode == 0, help_out.stderr)
check("--help 列出查询与核对命令",
      all(n in help_out.stdout for n in ("find", "clip", "ocr", "fix", "doctor")), help_out.stdout[:150])
check("--help 带退出码说明", "退出码" in help_out.stdout)

noargs = run()
check("无参数打印帮助而不是用法错误", noargs.returncode == 0 and "用法" in noargs.stdout,
      (noargs.returncode, noargs.stderr[:80]))

one = run("help", "find")
check("vsum help find 退出 0 且给出参数", one.returncode == 0 and "--context" in one.stdout, one.stdout[:80])
check("vsum help 不存在的命令退出 2", run("help", "nosuch").returncode == 2)

# export 直接收输出目录，省得记 segments.json 在哪
reexp = os.path.join(tmp, "reexport")
exp = run("export", "--segments", tmp, "-o", reexp)
check("export 收目录", exp.returncode == 0 and os.path.exists(os.path.join(reexp, "transcript.md")),
      (exp.returncode, exp.stderr[:80]))

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK cli")