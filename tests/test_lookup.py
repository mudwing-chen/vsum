# -*- coding: utf-8 -*-
"""vsum find / clip 的纯函数与命令行为。不依赖 ffmpeg（clip 的成功路径另算）。"""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from contextlib import redirect_stderr

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from vsum import lookup  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


SEGS = [
    {"start": 0.0, "end": 3.0, "text": "今天我们聊聊 Claude Code 这个工具", "avg_logprob": -0.2},
    {"start": 3.0, "end": 6.0, "text": "它每个月 20 美元，比 copilot 贵", "avg_logprob": -0.5},
    {"start": 216.0, "end": 219.0, "text": "最后说说价格，Pro 版 20 美元", "avg_logprob": -0.3},
]

# parse_time
check("mm:ss", lookup.parse_time("03:41") == 221.0, lookup.parse_time("03:41"))
check("带小数", lookup.parse_time("3:41.5") == 221.5)
check("纯秒数", lookup.parse_time("90") == 90.0)
check("hh:mm:ss", lookup.parse_time("1:02:03") == 3723.0)
try:
    lookup.parse_time("1:2:3:4")
    check("非法时间被拒", False)
except ValueError:
    check("非法时间被拒", True)

# search
hits = lookup.search(SEGS, ["价格"])
check("单词命中", len(hits) == 1 and hits[0]["start"] == 216.0, hits)
hits = lookup.search(SEGS, ["claude"])          # 大小写不敏感
check("大小写不敏感", len(hits) == 1, hits)
hits = lookup.search(SEGS, ["美元", "20"])       # AND
check("多词 AND", len(hits) == 2, [h["text"] for h in hits])
hits = lookup.search(SEGS, ["美元", "不存在"])
check("AND 不满足则为空", hits == [])
check("空查询返回空", lookup.search(SEGS, ["  "]) == [])
check("limit 生效", len(lookup.search(SEGS, ["美元"], limit=1)) == 1)

# load_segments
work = mkdtemp()
out = os.path.join(work, "out")
os.makedirs(out)
with open(os.path.join(out, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": SEGS}, fh, ensure_ascii=False)

segs, base = lookup.load_segments(out)
check("从目录读", len(segs) == 3 and os.path.abspath(base) == os.path.abspath(out), base)
segs2, _ = lookup.load_segments(os.path.join(out, "segments.json"))
check("从文件读", len(segs2) == 3)
try:
    lookup.load_segments(os.path.join(work, "nope"))
    check("缺 segments.json 报错", False)
except FileNotFoundError:
    check("缺 segments.json 报错", True)

# find 命令：正常输出 + JSON + 未命中退出码
buf = io.StringIO()
with redirect_stdout(buf):
    rc = lookup.find_main([out, "价格"])
text = buf.getvalue()
check("find 退出 0", rc == 0, rc)
check("find 打印时间戳", "[03:36]" in text, text)
check("find 打印原句", "Pro 版 20 美元" in text, text)
check("find 给出 clip 提示", "vsum clip" in text, text)

buf, err = io.StringIO(), io.StringIO()
with redirect_stdout(buf), redirect_stderr(err):
    rc = lookup.find_main([out, "价格", "--json"])
payload = json.loads(buf.getvalue())
check("--json 一行可解析", payload["hits"][0]["start"] == 216.0, payload)
check("--json 退出 0", rc == 0)

buf, err = io.StringIO(), io.StringIO()
with redirect_stdout(buf), redirect_stderr(err):
    rc = lookup.find_main([out, "不存在的词"])
check("未命中退出 1", rc == 1, rc)

# find --context 带上下文：命中行以 ">" 开头，上下文行以空格开头
buf = io.StringIO()
with redirect_stdout(buf):
    lookup.find_main([out, "价格", "--context", "1"])
text = buf.getvalue()
marked = [l for l in text.splitlines() if l.startswith(">")]
neighbors = [l for l in text.splitlines() if l.startswith(" ")]
check("--context 带出相邻段",
      len(marked) == 1 and len(neighbors) >= 1 and "20 美元" in "\n".join(neighbors),
      text)


# 别人给的产物目录：manifest.input.path 可以指向任意文件，不能照单全收
import hashlib  # noqa: E402

pts = os.path.join(work, "pts")
os.makedirs(pts)
secret = os.path.join(work, "secret.txt")
with open(secret, "w", encoding="utf-8") as fh:
    fh.write("x" * 100)
with open(os.path.join(pts, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": SEGS}, fh, ensure_ascii=False)
with open(os.path.join(pts, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"input": {"path": secret, "sha256": "0" * 64}}, fh)
buf, err = io.StringIO(), io.StringIO()
try:
    with redirect_stdout(buf), redirect_stderr(err):
        lookup.clip_main([pts, "00:03", "-o", os.path.join(work, "x.mp4")])
    check("manifest 越界路径要报错", False)
except ValueError as exc:
    check("manifest 越界路径要报错", "sha256" in str(exc), exc)

with open(os.path.join(pts, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"input": {"path": secret,
                         "sha256": hashlib.sha256(b"x" * 100).hexdigest()}}, fh)
check("sha256 对得上就认",
      lookup._source_media(pts)[0] == secret, lookup._source_media(pts))

# clip：没有原视频时必须明确报错，而不是剪出个空文件
buf, err = io.StringIO(), io.StringIO()
with redirect_stdout(buf), redirect_stderr(err):
    rc = lookup.clip_main([out, "03:36", "-o", os.path.join(work, "clip.mp4")])
check("clip 无源文件退出 4", rc == 4, (rc, err.getvalue()))
check("clip 无源文件不产文件", not os.path.exists(os.path.join(work, "clip.mp4")))

# clip：时间点落在空隙里，距离太远则拒绝
buf, err = io.StringIO(), io.StringIO()
with redirect_stdout(buf), redirect_stderr(err):
    rc = lookup.clip_main([out, "30:00", "-o", os.path.join(work, "clip2.mp4")])
check("clip 时间点太远退出 2", rc == 2, (rc, err.getvalue()))

# clip 成功路径：有 ffmpeg 就真剪一段出来（只剪音频，不需要真视频）
from vsum.toolpath import which as _which  # noqa: E402

if _which("ffmpeg"):
    cdir = os.path.join(work, "clip")
    os.makedirs(cdir, exist_ok=True)
    with open(os.path.join(cdir, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": SEGS}, fh, ensure_ascii=False)
    _wav = os.path.join(cdir, "audio.wav")
    subprocess.run([_which("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=6", "-ar", "16000", "-ac", "1", _wav],
                   capture_output=True)
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = lookup.clip_main([cdir, "00:02", "-o", os.path.join(work, "cut.wav")])
    cut = os.path.join(work, "cut.wav")
    check("clip 成功路径退出 0", rc == 0, (rc, err.getvalue()[-200:]))
    check("clip 真的剪出文件", os.path.exists(cut) and os.path.getsize(cut) > 1000,
          os.path.getsize(cut) if os.path.exists(cut) else "missing")

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK lookup")
