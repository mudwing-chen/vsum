# -*- coding: utf-8 -*-
"""vsum find / clip 的纯函数与命令行为。不依赖 ffmpeg（clip 的成功路径另算）。"""
import io
import json
import os
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

# find --context 带上下文
buf = io.StringIO()
with redirect_stdout(buf):
    lookup.find_main([out, "价格", "--context", "1"])
text = buf.getvalue()
check("--context 带出相邻段", "它每个月 20 美元" in text or "最后说说价格" in text, text)

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

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK lookup")
