#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语表与 fix_terms 的回归测试。无第三方依赖：python3 tests/test_terms.py"""
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
from vsum import fix as fix_terms  # noqa: E402
from vsum import mine_variants, phonetics, termrules  # noqa: E402

CORPUS = os.path.join(HERE, "fixtures", "corpus")
PASS = []


def check(name, cond, detail=""):
    if not cond:
        print("FAIL %s %s" % (name, detail))
        sys.exit(1)
    PASS.append(name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def lines_of(path):
    return read(path).splitlines()


RULES = termrules.load()

# 中文没有词边界，单字 variant 会在任意合成词里命中，必须在加载时就挡住
_bad = os.path.join(mkdtemp(), "bad-terms.json")
with open(_bad, "w", encoding="utf-8") as _fh:
    json.dump({"terms": [{"canonical": "云端", "variants": [{"surface": "云"}]}]},
              _fh, ensure_ascii=False)
try:
    termrules.load(_bad)
    check("中文单字 variant 被拒", False, "没有抛错")
except ValueError as exc:
    check("中文单字 variant 被拒", "至少两个字" in str(exc), str(exc))
_good = os.path.join(mkdtemp(), "ok-terms.json")
with open(_good, "w", encoding="utf-8") as _fh:
    json.dump({"terms": [{"canonical": "云端", "variants": [{"surface": "云断"}]}]},
              _fh, ensure_ascii=False)
check("中文两字 variant 照收", termrules.load(_good)[0]["surface"] == "云断")


def strip_paths(path):
    """corrections.json 里的 transcript/fixed 是绝对路径，两次跑目录不同，比较时去掉。"""
    d = json.load(open(path, encoding="utf-8"))
    for f in d["files"]:
        f.pop("transcript", None)
        f.pop("fixed", None)
    return d


def run(*argv):
    return subprocess.run([sys.executable] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


# 1. 词表自身结构
with open(termrules.DEFAULT_TERMS, encoding="utf-8") as fh:
    data = json.load(fh)
surfaces = []
for term in data["terms"]:
    check("canonical 非空", bool(term["canonical"]))
    for v in term["variants"]:
        surfaces.append(v["surface"])
        check("surface 小写: " + v["surface"], v["surface"] == v["surface"].lower())
        check("confidence 合法: " + v["surface"], v.get("confidence", "high") in ("high", "medium"))
check("surface 无重复", len(surfaces) == len(set(surfaces)),
      [s for s in surfaces if surfaces.count(s) > 1])
check("词条数 >= 12", len(data["terms"]) >= 12, len(data["terms"]))

# 2. 6 处已知漏报必须全部命中（回归断言，不是泛化证据）
MISSES = [
    {"surface": "Cloud", "expected": "Claude"},
    {"surface": "GROCK", "expected": "Grok"},
    {"surface": "Deepseq", "expected": "DeepSeek"},
    {"surface": "DeepSec", "expected": "DeepSeek"},
    {"surface": "sunite", "expected": "Sonnet"},
    {"surface": "CloudMD", "expected": "CLAUDE.md"},
]
for miss in MISSES:
    hits = termrules.find_hits("x " + miss["surface"] + " y", RULES)
    check("漏报已补: " + miss["surface"], len(hits) == 1 and hits[0]["canonical"] == miss["expected"],
          hits)

# 3. term-mix：默认只落 high；medium 只报不改
src_mix = os.path.join(CORPUS, "term-mix", "transcript.txt")
fixed, recs = fix_terms.fix_lines(lines_of(src_mix), RULES)
check("term-mix 命中数", len(recs) == 7, len(recs))
check("term-mix 默认只落 high",
      [r["surface"] for r in recs if r["applied"]] ==
      ["cloud code", "grock", "deepseq", "sunite", "cloudmd"],
      [(r["surface"], r["applied"]) for r in recs])
check("term-mix 默认输出", fixed == [
    "[  0.0 ->  3.2] 今天我们聊聊 Claude Code 这个工具",
    "[  3.2 ->  6.4] 它叫 Grok，还有 DeepSeek 和 Sonnet",
    "[  6.4 ->  9.6] 这个 cloudy 天气不错",
    "[  9.6 -> 12.8] 用 CLAUDE.md 记录项目规范",
    "[ 12.8 -> 16.0] 这门课讲克劳得和 open cloud",   # 两条 medium 没人确认过，不许改
], fixed)
check("term-mix medium 未落地",
      [r["applied"] for r in recs if r["confidence"] == "medium"] == [False, False])

fixed_m, recs_m = fix_terms.fix_lines(lines_of(src_mix), RULES, apply_medium=True)
check("term-mix --apply-medium 全落", len(recs_m) == 7 and all(r["applied"] for r in recs_m))
check("term-mix --apply-medium 输出", fixed_m == [
    "[  0.0 ->  3.2] 今天我们聊聊 Claude Code 这个工具",
    "[  3.2 ->  6.4] 它叫 Grok，还有 DeepSeek 和 Sonnet",
    "[  6.4 ->  9.6] 这个 cloudy 天气不错",
    "[  9.6 -> 12.8] 用 CLAUDE.md 记录项目规范",
    "[ 12.8 -> 16.0] 这门课讲克劳德和 OpenClaw",
], fixed_m)

# 4. overlap：长短语优先、不重叠、词边界；同行有 code 也不能把裸 cloud 升成 high
src_ov = os.path.join(CORPUS, "overlap", "transcript.txt")
fixed2, recs2 = fix_terms.fix_lines(lines_of(src_ov), RULES)
check("overlap 命中数", len(recs2) == 3, [(r["surface"], r["line"]) for r in recs2])
check("overlap 默认输出", fixed2 == [
    "[  0.0 ->  2.0] Claude Code 和 cloud 都出现在同一行，cloudy 不该命中",
    "[  2.0 ->  4.0] 这里写代码用 open cloud",
], fixed2)
check("同行有 code 不升级裸 cloud",
      [r["confidence"] for r in recs2 if r["surface"] == "cloud"] == ["medium"])
check("cloudy 不命中", all("cloudy" not in r["surface"] for r in recs2))
check("overlap --apply-medium 才改第二行",
      fix_terms.fix_lines(lines_of(src_ov), RULES, apply_medium=True)[0][1].endswith("用 OpenClaw"))

# 5. clean：零改动，输出逐字节等于原文
src = os.path.join(CORPUS, "clean", "transcript.txt")
fixed3, recs3 = fix_terms.fix_lines(lines_of(src), RULES)
check("clean 零命中", recs3 == [], recs3)
check("clean 原样", "\n".join(fixed3) + "\n" == read(src))

# 5.5 segments.json 只做标注：低置信段不改变改不改，也不改变写入结果
_segdir = mkdtemp()
try:
    with open(os.path.join(_segdir, "transcript.txt"), "w", encoding="utf-8") as fh:
        fh.write("[  0.0 ->  3.0] 用 cloud code 写代码\n[  3.0 ->  6.0] 今天天气不错\n")
    with open(os.path.join(_segdir, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": [
            {"start": 0.0, "end": 3.0, "text": "用 cloud code 写代码", "avg_logprob": -1.2,
             "no_speech_prob": 0.02, "compression_ratio": 1.1},
            {"start": 3.0, "end": 6.0, "text": "今天天气不错", "avg_logprob": -0.2,
             "no_speech_prob": 0.01, "compression_ratio": 1.0},
        ]}, fh)
    _res = fix_terms.process(os.path.join(_segdir, "transcript.txt"), RULES, False, False)
    _c = _res["corrections"][0]
    check("segments 标注 avg_logprob", _c["segment"]["avg_logprob"] == -1.2, _c.get("segment"))
    check("最低 10% 标 low_conf", _c["low_conf"] is True, _c)
    check("low_conf 不改决策", _c["applied"] is True and _res["low_conf"] == 1, (_c["applied"], _res["low_conf"]))
    os.remove(os.path.join(_segdir, "segments.json"))
    _res2 = fix_terms.process(os.path.join(_segdir, "transcript.txt"), RULES, False, False)
    check("没有 segments.json 时不炸", _res2["low_conf"] == 0 and _res2["corrections"][0]["segment"] is None,
          _res2["corrections"][0])
finally:
    shutil.rmtree(_segdir, ignore_errors=True)

# 6. 幂等：修过的文本再跑一遍不许再产生落地改动（medium 仍会报，但 applied=false）
again_lines, again = fix_terms.fix_lines(fixed, RULES)
check("幂等：无新落地改动", [r for r in again if r["applied"]] == [], again)
check("幂等：输出不变", again_lines == fixed)

# 6.4 词边界与重叠：旧版会把标识符切开、会被短命中抢走长短语
def hits_of(text):
    return [(h["surface"], h["canonical"]) for h in termrules.find_hits(text, RULES)]


check("下划线标识符不切", hits_of("cloud_code 这个仓库") == [], hits_of("cloud_code 这个仓库"))
check("数字相邻不切", hits_of("cloud9 部署") == [], hits_of("cloud9 部署"))
check("中文相邻照常命中", hits_of("用 cloud 写代码") == [("cloud", "Claude")], hits_of("用 cloud 写代码"))
check("大小写错写已覆盖", hits_of("打开 Claude.md 看规范") == [("claude.md", "CLAUDE.md")],
      hits_of("打开 Claude.md 看规范"))
# 覆盖与幂等不再二选一：修好之后命中片段等于 canonical，find_hits 直接跳过
_cm, _cm_recs = fix_terms.fix_lines(["[  0.0 ->  1.0] 打开 claude.md 看看"], RULES, apply_medium=True)
check("claude.md 修成 CLAUDE.md", _cm == ["[  0.0 ->  1.0] 打开 CLAUDE.md 看看"], _cm)
check("修过的 CLAUDE.md 不再命中", termrules.find_hits(_cm[0], RULES) == [],
      termrules.find_hits(_cm[0], RULES))
check("点号错写", hits_of("打开 cloud.md 看规范") == [("cloud.md", "CLAUDE.md")],
      hits_of("打开 cloud.md 看规范"))
check("cloud.md 只到 medium",
      [h["confidence"] for h in termrules.find_hits("cloud.md", RULES)] == ["medium"])
check("偏移不随 lower() 漂（İ 案例）",
      termrules.apply_hits("İstanbul cloud 的客户", termrules.find_hits("İstanbul cloud 的客户", RULES))
      == "İstanbul Claude 的客户",
      termrules.apply_hits("İstanbul cloud 的客户", termrules.find_hits("İstanbul cloud 的客户", RULES)))
check("中文音译只做 medium", [(h["canonical"], h["confidence"]) for h in termrules.find_hits("克劳特说这个模型", RULES)]
      == [("克劳德", "medium")], hits_of("克劳特说这个模型"))
check("claudecode 不再免检", [(h["canonical"], h["confidence"]) for h in termrules.find_hits("这个 claudecode 好用", RULES)]
      == [("Claude Code", "medium")], hits_of("这个 claudecode 好用"))
check("等长重叠取靠前的（已知任意选择）", hits_of("open cloud code") == [("open cloud", "OpenClaw")],
      hits_of("open cloud code"))
# 注意：下面这条现有词表里两种排序都会过——它守行为，不证明排序修好了什么（跨 start 的
# 长命中冲突在当前词表里不存在，实测 0 例）。排序仍按「最长优先」写，是为了和文档一致。
check("重叠时长命中不被抢", hits_of("chat gpt 4") == [("chat gpt", "ChatGPT")], hits_of("chat gpt 4"))

# 6.5 音素通道：Soundex 能找回编辑距离够不到的漏报（只做候选，不改文本）
for miss in MISSES:
    check("音素骨架一致: " + miss["surface"],
          phonetics.soundex(miss["surface"]) == phonetics.soundex(miss["expected"]),
          (phonetics.soundex(miss["surface"]), phonetics.soundex(miss["expected"])))
check("非 ASCII 不猜", phonetics.soundex("克劳得") == "", phonetics.soundex("克劳得"))
check("cloud/cloudy 同骨架（所以只能做候选）",
      phonetics.soundex("cloud") == phonetics.soundex("cloudy") == "C430")
one = {"Sonnet": ["sonnet"]}
check("phonetic 通道兜底", mine_variants.mine({"sunite": 3}, one) == [
    {"surface": "sunite", "count": 3, "nearest": "Sonnet", "distance": None, "channel": "phonetic"}],
    mine_variants.mine({"sunite": 3}, one))
check("phonetic 通道可关", mine_variants.mine({"sunite": 3}, one, edit_only=True) == [])
check("edit 通道优先", [c["channel"] for c in mine_variants.mine({"clawd": 3}, {"Claude": ["claude"]})] == ["edit"],
      mine_variants.mine({"clawd": 3}, {"Claude": ["claude"]}))
PRODUCTS = mine_variants.build_products()
check("词表写法不重复报候选", mine_variants.mine({"grock": 3}, PRODUCTS) == [],
      mine_variants.mine({"grock": 3}, PRODUCTS))
# 6.6 拒绝机制：骨架相同也不等于同一个词（Garg20 的 NE rejection 的最小近似）
check("骨架歧义不猜", mine_variants.mine({"sunite": 3}, {"Sonnet": ["sonnet"], "Sonnett": ["sonnett"]}) == [],
      mine_variants.mine({"sunite": 3}, {"Sonnet": ["sonnet"], "Sonnett": ["sonnett"]}))
check("长度差过半否掉", mine_variants.mine({"transformer": 3}, one) == [],
      mine_variants.mine({"transformer": 3}, one))
check("词表里已有的写法不报警", mine_variants.mine({"sunite": 3}, mine_variants.build_products()) == [],
      mine_variants.mine({"sunite": 3}, mine_variants.build_products()))
check("候选池含 terms.json 全部 canonical",
      all(term["canonical"] in PRODUCTS for term in data["terms"]),
      [term["canonical"] for term in data["terms"] if term["canonical"] not in PRODUCTS])

# 7. CLI：dry-run 不写文件；--apply 写 fixed + corrections.json；两次结果字节相同
tmp = mkdtemp()
try:
    a = os.path.join(tmp, "a")
    shutil.copytree(CORPUS, a)
    before = sorted(os.listdir(os.path.join(a, "term-mix")))
    dry = run("-m", "vsum", "fix", a)
    check("dry-run 退出 0", dry.returncode == 0, dry.stderr)
    check("dry-run 不写盘", sorted(os.listdir(os.path.join(a, "term-mix"))) == before)
    check("dry-run 报 dry-run", "dry-run" in dry.stdout, dry.stdout)

    ap = run("-m", "vsum", "fix", a, "--apply")
    check("apply 退出 0", ap.returncode == 0, ap.stderr)
    fixed_txt = read(os.path.join(a, "term-mix", "transcript.fixed.txt"))
    check("apply 写 fixed", "Claude Code" in fixed_txt)
    check("apply 不放行 medium",
          "克劳德" not in fixed_txt and "OpenClaw" not in fixed_txt, fixed_txt)
    check("原文件未动", read(os.path.join(a, "term-mix", "transcript.txt")).startswith("[  0.0 ->  3.2] 今天我们聊聊 cloud code"))
    corr = json.load(open(os.path.join(a, "corrections.json"), encoding="utf-8"))
    flat = [c for f in corr["files"] for c in f["corrections"]]
    check("corrections total", corr["total"] == 10, corr["total"])
    check("corrections applied", corr["applied"] == 6, corr["applied"])
    check("medium 标成未落地",
          [c["applied"] for c in flat if c["confidence"] == "medium"] == [False, False, False, False], flat)

    b = os.path.join(tmp, "b")
    shutil.copytree(CORPUS, b)
    run("-m", "vsum", "fix", b, "--apply")
    check("确定性：修好的文本", read(os.path.join(b, "term-mix", "transcript.fixed.txt")) == fixed_txt)
    check("确定性：修订内容（除路径）", strip_paths(os.path.join(b, "corrections.json")) == strip_paths(os.path.join(a, "corrections.json")))

    c = os.path.join(tmp, "c")
    shutil.copytree(CORPUS, c)
    cmed = run("-m", "vsum", "fix", c, "--apply", "--apply-medium")
    check("--apply-medium 退出 0", cmed.returncode == 0, cmed.stderr)
    all_txt = read(os.path.join(c, "term-mix", "transcript.fixed.txt"))
    check("--apply-medium 落 medium", "克劳德和 OpenClaw" in all_txt, all_txt)
    corr_c = json.load(open(os.path.join(c, "corrections.json"), encoding="utf-8"))
    check("--apply-medium applied 全落", corr_c["applied"] == 10, corr_c["applied"])

    # 8. 时间戳前缀逐行保留
    orig = lines_of(os.path.join(a, "term-mix", "transcript.txt"))
    new = lines_of(os.path.join(a, "term-mix", "transcript.fixed.txt"))
    check("行数不变", len(orig) == len(new))
    check("时间戳保留", all(o.split("]")[0] == n.split("]")[0] for o, n in zip(orig, new)))
finally:
    shutil.rmtree(tmp)

print("OK %d checks" % len(PASS))