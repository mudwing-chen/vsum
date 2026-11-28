#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 与 eval 的回归：退出码、CER、参考文本匹配、manifest 自洽。不跑模型。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import evaluate, manifest as manifest_mod, pipeline, transcribe as tr_mod  # noqa: E402

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

_which = pipeline.toolpath.which
pipeline.toolpath.which = lambda name: None
try:
    check("没 ffprobe 时音频探测返回 None（不阻塞）", pipeline.has_audio_stream("x.mp4") is None)
finally:
    pipeline.toolpath.which = _which

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

# transcribe 是默认命令的显式名字：脚本按 README 写全 `vsum transcribe` 时，
# 退出码和 --json/--quiet 必须和 `vsum <文件>` 完全一样。
def transcribe_fixture(name, decode=None):
    d = os.path.join(tmp, name)
    out = os.path.join(d, "out")
    os.makedirs(out)
    src = os.path.join(d, "demo.mp4")
    with open(src, "wb") as fh:
        fh.write(b"fake-video-bytes")
    wav = os.path.join(out, "audio.wav")
    with open(wav, "wb") as fh:
        fh.write(b"RIFFfake")
    with open(os.path.join(out, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": segs}, fh, ensure_ascii=False)
    man = manifest_mod.build(src, wav, decode or {"model": "tiny", "language": "zh",
                                                  "condition_on_previous_text": True},
                             segs, {}, [], duration=18.0)
    manifest_mod.write(man, os.path.join(out, "manifest.json"))
    return src, out, wav


tr_src, tr_out, tr_wav = transcribe_fixture("tr")
tr_proc = run("transcribe", tr_src, "-o", tr_out, "--model", "tiny")
check("transcribe 子命令成功退出 0", tr_proc.returncode == 0, (tr_proc.returncode, tr_proc.stderr[-300:]))
check("transcribe 不把 result 字典打到 stderr", not tr_proc.stderr.lstrip().startswith("{"),
      tr_proc.stderr[:120])
tr_res = os.path.join(tr_out, "result.json")
check("transcribe 写了 ok 的 result.json",
      os.path.exists(tr_res) and json.load(open(tr_res, encoding="utf-8"))["ok"] is True)

tr_json = run("transcribe", tr_src, "-o", tr_out, "--model", "tiny", "--json")
tr_lines = [ln for ln in tr_json.stdout.splitlines() if ln.strip()]
check("transcribe --json 退出 0", tr_json.returncode == 0, (tr_json.returncode, tr_json.stderr[-200:]))
check("transcribe --json stdout 只有一行", len(tr_lines) == 1, tr_lines[:3])
tr_obj = json.loads(tr_lines[0]) if len(tr_lines) == 1 else {}
check("transcribe --json 可解析且复用成功", tr_obj.get("ok") is True and tr_obj.get("reused") is True,
      tr_obj.get("ok"))
check("--json 的 sha256 覆盖 plain 稿", "transcript.plain.txt" in (tr_obj.get("sha256") or {}),
      sorted((tr_obj.get("sha256") or {})))

tr_quiet = run("transcribe", tr_src, "-o", tr_out, "--model", "tiny", "--quiet")
check("transcribe --quiet 静默且退出 0",
      tr_quiet.returncode == 0 and tr_quiet.stdout.strip() == "",
      (tr_quiet.returncode, tr_quiet.stdout[:80]))

# 复用转写时 audio.wav 被删掉：应该重抽音频继续，而不是导出完才在 manifest 那一步炸
if shutil.which("ffmpeg"):
    rg_src, rg_out, rg_wav = transcribe_fixture("regen")
    real = os.path.join(tmp, "regen", "real.wav")
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ar", "16000", "-ac", "1", real],
                   capture_output=True)
    os.replace(real, rg_src)
    man = manifest_mod.read(os.path.join(rg_out, "manifest.json"))
    man["input"]["sha256"] = manifest_mod.sha256_file(rg_src)
    man["input"]["path"] = rg_src
    man["input"]["duration"] = None      # 上次没探出时长（既无 ffprobe 也无 PyAV）
    manifest_mod.write(man, os.path.join(rg_out, "manifest.json"))
    os.remove(rg_wav)
    rg = run("transcribe", rg_src, "-o", rg_out, "--model", "tiny")
    check("复用缺 audio.wav 时重抽音频并退出 0",
          rg.returncode == 0 and os.path.exists(rg_wav), (rg.returncode, rg.stderr[-200:]))
    check("重抽后 manifest 与 result 都在",
          os.path.exists(os.path.join(rg_out, "manifest.json"))
          and json.load(open(os.path.join(rg_out, "result.json"), encoding="utf-8"))["ok"] is True)
    rg_md = open(os.path.join(rg_out, "transcript.md"), encoding="utf-8").read()
    check("重抽音频后补探时长（不再是 null）", "duration_sec: null" not in rg_md, rg_md[:160])

# 文件类错误必须带文件名，不然「No such file or directory」等于没说
missing = os.path.join(tmp, "no-such-segments.json")
nf = run("export", "--segments", missing, "-o", os.path.join(tmp, "nf-out"))
check("文件类错误带文件名", nf.returncode == 2 and "no-such-segments.json" in nf.stderr,
      (nf.returncode, nf.stderr))

# fix --apply 零命中时不能删 corrections.json：它可能是用户手写的输入
fix_dir = os.path.join(tmp, "fixzero")
os.makedirs(fix_dir)
with open(os.path.join(fix_dir, "transcript.txt"), "w", encoding="utf-8") as fh:
    fh.write("今天天气不错，出去走走。\n")
fix_corr = os.path.join(fix_dir, "corrections.json")
with open(fix_corr, "w", encoding="utf-8") as fh:
    json.dump({"corrections": [{"term": "手写清单", "variants": ["手写"], "confidence": "high"}]},
              fh, ensure_ascii=False, indent=1)
fix_proc = run("fix", fix_dir, "--apply")
check("fix 零命中退出 0", fix_proc.returncode == 0, (fix_proc.returncode, fix_proc.stderr[-200:]))
check("fix 零命中保留手写 corrections.json", os.path.exists(fix_corr), fix_proc.stdout[-120:])
check("保留的 corrections.json 内容没被改写",
      os.path.exists(fix_corr) and json.load(open(fix_corr, encoding="utf-8"))["corrections"][0]["term"] == "手写清单")


# 复用条件必须覆盖所有改变输出的解码参数：--words / 热词 / prompt 换了就不能复用旧稿
wd_decode = tr_mod.decode_args("tiny", "zh")[1]
wd_src, wd_out, _ = transcribe_fixture("reuse-params", decode=wd_decode)
ok_reuse, _, _ = pipeline.segments_reusable(wd_out, wd_src, "tiny", words=None, language="zh", context=True)
check("解码参数一致时复用", ok_reuse is True)
ok_words, _, why_words = pipeline.segments_reusable(wd_out, wd_src, "tiny", words=True,
                                                    language="zh", context=True)
check("带 --words 不复用旧稿", ok_words is False and "解码参数" in why_words, why_words)
os.environ["WHISPER_PROMPT"] = "这次换一个提示词"
try:
    ok_prompt, _, why_prompt = pipeline.segments_reusable(wd_out, wd_src, "tiny", words=None,
                                                          language="zh", context=True)
finally:
    os.environ.pop("WHISPER_PROMPT", None)
check("WHISPER_PROMPT 变了不复用旧稿", ok_prompt is False and "解码参数" in why_prompt, why_prompt)


# ffprobe 是可选依赖：缺了只该是 warn，不能把医生判成失败
from vsum import doctor as doctor_mod  # noqa: E402

doc_rows = {name: status for name, status, _detail, _hint in doctor_mod.collect("tiny")[0]}
check("doctor 把 ffprobe 当可选（warn 而非 MISSING）",
      doc_rows.get("ffprobe") in ("ok", "warn"), doc_rows.get("ffprobe"))

# help 自己是文档化的入口词，拿它探测帮助支持情况不该被判成「没有这个子命令」
hh = run("help", "help")
check("vsum help help 打印总览", hh.returncode == 0 and "退出码" in hh.stdout,
      (hh.returncode, hh.stdout[:80]))

# 空 segments 导出：别把「什么都没导出来」当成功
empty_dir = os.path.join(tmp, "empty-segs")
os.makedirs(empty_dir)
empty_seg = os.path.join(empty_dir, "segments.json")
with open(empty_seg, "w", encoding="utf-8") as fh:
    json.dump({"segments": []}, fh)
empty_out = os.path.join(tmp, "empty-out")
ex_empty = run("export", "--segments", empty_seg, "-o", empty_out)
check("export 空 segments 退出 2", ex_empty.returncode == 2,
      (ex_empty.returncode, ex_empty.stderr[-200:]))
check("export 空 segments 不写产物", not os.path.exists(os.path.join(empty_out, "transcript.md")),
      os.listdir(empty_out) if os.path.isdir(empty_out) else "no dir")

# --clean 是「额外一版」，不能改动逐字稿；清洗稿也不该带术语附录（它不改词表）
clean_dir = os.path.join(tmp, "clean-src")
os.makedirs(clean_dir)
clean_seg = os.path.join(clean_dir, "segments.json")
with open(clean_seg, "w", encoding="utf-8") as fh:
    json.dump({"segments": segs}, fh, ensure_ascii=False)
clean_out = os.path.join(tmp, "clean-out")
ex_clean = run("export", "--segments", clean_seg, "-o", clean_out, "--clean")
check("export --clean 退出 0", ex_clean.returncode == 0, (ex_clean.returncode, ex_clean.stderr[-200:]))
check("--clean 生成 transcript.clean.md",
      os.path.exists(os.path.join(clean_out, "transcript.clean.md")), os.listdir(clean_out))
check("清洗稿不带术语修订附录",
      "## 术语修订" not in open(os.path.join(clean_out, "transcript.clean.md"), encoding="utf-8").read())
check("--clean 不动 transcript.md",
      "[00:00]" in open(os.path.join(clean_out, "transcript.md"), encoding="utf-8").read())


# 版本号写两处（pyproject 与 __init__），发版时最容易只改一处
import re as _re  # noqa: E402

from vsum import __version__ as _pkg_version  # noqa: E402

_pm = _re.search(r'^version = "([^"]+)"',
                 open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read(), _re.M)
check("pyproject 与包版本一致",
      _pm is not None and _pm.group(1) == _pkg_version,
      (_pm.group(1) if _pm else None, _pkg_version))

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK cli")