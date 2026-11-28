# -*- coding: utf-8 -*-
"""vsum 命令行入口。

三个约定：

- 不带参数、``vsum --help``、``vsum help`` 打印同一张分组总览；
- 每个子命令自己的参数由 argparse 提供，``vsum help <子命令>`` 直达；
- 命令总览和 SUB 分发必须一一对应，对不上就是 bug（tests/test_cli.py 会查）。
"""
import argparse
import contextlib
import io
import json
import os
import sys
import unicodedata

from . import (__version__, audit, batch as batch_mod, bench as bench_mod, browser as browser_mod, doctor, evaluate, export as export_mod, fetch as fetch_mod, fix, holdout, lookup, mine_variants, ocr as ocr_mod, pipeline, transcribe)

# 命令总览：(子命令, 分组, 用法片段, 一句话说明)
# 这张表就是用户看到的第一屏，也是 tests/test_cli.py 校验 SUB 覆盖度的依据。
# transcribe 是默认命令的显式名字：不带命令名时走同一条路径。
COMMANDS = [
    ("transcribe", "转写", "<文件|链接> -o DIR", "转写 + 导出 + 摘要（默认命令，可省掉命令名）"),
    ("export", "转写", "--segments FILE -o DIR", "从 segments.json 重新导出四件套"),
    ("fetch", "抓取", "URL -o DIR", "只抓取：抖音 / B站 / YouTube → 本地视频"),
    ("login", "抓取", "douyin", "扫码登录，登录态存进独立 profile"),
    ("batch", "抓取", "LIST -o DIR", "一串链接或清单文件；默认串行，--jobs 可并行"),
    ("find", "查询与剪辑", "DIR 关键词...", "在逐字稿里搜，命中项带 [mm:ss]"),
    ("clip", "查询与剪辑", "DIR mm:ss -o FILE", "按时间点剪出那一段（前后各留 1.5 秒）"),
    ("ocr", "核对与修正", "DIR", "抽帧 OCR，用画面上的拼写核对专名"),
    ("fix", "核对与修正", "DIR", "按词表修正（默认只预演，--apply 才写盘）"),
    ("audit", "核对与修正", "DIR", "词表命中的体检报告"),
    ("mine", "核对与修正", "DIR", "挖词表外的变体候选"),
    ("doctor", "诊断与评测", "", "逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / OCR / yt-dlp"),
    ("bench", "诊断与评测", "--audio FILE", "在本机音频上量 RTF，比较 compute_type"),
    ("eval", "诊断与评测", "--manifest FILE", "按 manifest 算 CER 与术语召回"),
    ("holdout", "诊断与评测", "--corpus DIR", "固定语料上的 CER 与回归比较"),
]


def _width(text):
    """终端显示宽度：CJK 与全角符号占两格，按 len() 对齐会歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def overview():
    """不带参数 / --help / help 打印的那一屏。"""
    lines = [
        "vsum %s — 视频/音频 → 可逐秒引用的逐字稿与带出处的摘要" % __version__,
        "",
        "用法",
        "  vsum <文件|链接> -o <目录>        转写本地文件或抖音/B站/YouTube 链接",
        "  vsum <子命令> [参数]              见下表；省掉命令名就是转写",
        "  vsum help <子命令>                看这个子命令的全部参数",
        "",
    ]
    rows = [("%s %s" % (name, synopsis)).strip() for name, _, synopsis, _ in COMMANDS]
    pad = max(_width(r) for r in rows) + 2
    group = None
    for (name, grp, synopsis, desc), row in zip(COMMANDS, rows):
        if grp != group:
            if group is not None:
                lines.append("")
            lines.append(grp)
            group = grp
        lines.append("  %s%s%s" % (row, " " * (pad - _width(row)), desc))
    lines += [
        "",
        "退出码：0 成功 · 1 find 无命中 · 2 用法或输入错误 · 3 缺依赖 · 4 转写/抓取/剪片失败 · "
        "5 评测不达标或 batch 有失败项 · 130 中断",
        "环境变量、平台差异与已知限制见 README：https://github.com/mudwing-chen/vsum",
    ]
    return "\n".join(lines)


def _export_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum export", description="从 segments.json 重新导出四件套")
    ap.add_argument("--segments", required=True,
                    help="segments.json 路径，也可以直接给 vsum 的输出目录")
    ap.add_argument("-o", "--out", required=True, help="产物目录，不存在会建")
    ap.add_argument("--clean", action="store_true",
                    help="顺带生成清洗版 transcript.clean.md（transcript.md 本身不变）")
    args = ap.parse_args(argv)
    seg_path = args.segments
    if os.path.isdir(seg_path):
        seg_path = os.path.join(seg_path, "segments.json")
    if not os.path.exists(seg_path):
        raise pipeline.InputError("segments 文件不存在：%s" % seg_path)
    with open(seg_path, encoding="utf-8") as fh:
        segments = json.load(fh).get("segments", [])
    if not segments:
        # 空转写导出成四件套空文件、退出码 0，脚本会把它当成功；find/clip/pipeline
        # 都按错误处理，这里没理由例外。
        raise pipeline.InputError("segments 文件里没有可用段落：%s" % seg_path)
    os.makedirs(args.out, exist_ok=True)
    clean_md = None
    if args.clean:
        from . import clean as clean_mod

        clean_md = clean_mod.clean_md(segments)
    outputs, warnings = export_mod.export_all(segments, args.out, clean_md, source=seg_path)
    for name, path in outputs.items():
        print("%-9s %s" % (name, os.path.basename(path)))
    if warnings["long_cues"]:
        print("long cues: %s" % ",".join(str(i) for i in warnings["long_cues"]))
    if warnings["invalid_cues"]:
        print("invalid cues: %s" % ",".join("%d(%s)" % (x["index"], x["reason"]) for x in warnings["invalid_cues"]))
    return 0


def _doctor_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum doctor", description="检查依赖")
    ap.add_argument("--model", default="small", help="顺带检查这个模型能不能加载（默认 small）")
    args = ap.parse_args(argv)
    return doctor.run(args.model)


def _decode_opts(args):
    """把 CLI 的批/VAD/chunk 参数收成一个 dict；pipeline 与 batch 共用。"""
    size = (args.batch_size or "1").strip()
    if size != "auto" and not size.isdigit():
        raise pipeline.InputError("--batch-size 只能是整数或 auto：%s" % size)
    return {"batch_size": size, "threshold": args.vad_threshold,
            "min_speech_duration_ms": args.vad_min_speech_ms,
            "min_silence_duration_ms": args.vad_min_silence_ms,
            "speech_pad_ms": args.vad_pad_ms, "no_vad": args.no_vad,
            "chunk_length": args.chunk_length}


def _parse_main(argv, prog="vsum"):
    ap = argparse.ArgumentParser(prog=prog, description="逐字稿工具：vsum <file> -o DIR")
    ap.add_argument("input", help="本地音视频文件，或抖音/B站/YouTube 链接")
    ap.add_argument("-o", "--out", required=True, help="产物目录，不存在会建")
    ap.add_argument("--model", default="small",
                    help="tiny|base|small|medium|large-v3|auto，或本地模型目录（默认 small；"
                         "auto 按显存取上限，CPU 固定 small）")
    ap.add_argument("--lang", default="zh", help="语言：zh / en / auto（默认 zh）")
    ap.add_argument("--pack", default=None,
                    help="术语包 JSON；默认用包内 data/terms.json（示例词表）")
    ap.add_argument("--clean", action="store_true",
                    help="额外生成一版清洗稿 transcript.clean.md（去口水词与重复），"
                         "transcript.md 保持逐字原样")
    ap.add_argument("--words", action="store_true",
                    help="记录词级时间戳与概率（更慢，manifest 里会记 word_timestamps）")
    ap.add_argument("--no-context", action="store_true",
                    help="不用上文（condition_on_previous_text=False）：更不易复读，"
                         "但中文会漂成繁体、丢标点，不推荐")
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"],
                    help="抓链接时用哪个浏览器；默认 Chrome，没装就 Edge，Safari 需显式指定")
    ap.add_argument("--headless", action="store_true", help="无头模式（可能被风控，默认可见窗口）")
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--fetch-only", action="store_true", help="只抓取，不转写")
    ap.add_argument("--summary", default="extractive", choices=["extractive", "llm", "none"],
                    help="摘要模式：extractive（默认，每条必须能在原文找到）/ llm / none")
    ap.add_argument("--llm-url", default=None,
                    help="OpenAI-compatible base，如 http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default=None, help="LLM 模型名，如 qwen2.5:7b")
    ap.add_argument("--llm-key", default=None,
                    help="云端 LLM 的 API key（发 Authorization: Bearer）；"
                         "也可用环境变量 VSUM_LLM_KEY / OPENAI_API_KEY")
    ap.add_argument("--force", action="store_true", help="不复用旧转写，强制重跑模型")
    ap.add_argument("--batch-size", default="1",
                    help="批推理大小：整数或 auto（按显存给 16/8/4）；CPU 上自动为 1。"
                         "默认 1（串行，产物与旧版一致）")
    ap.add_argument("--vad-threshold", type=float, default=None,
                    help="VAD 语音阈值，默认 0.5；风声/音乐重的素材调高")
    ap.add_argument("--vad-min-speech-ms", type=int, default=None, help="VAD 最短语音（毫秒）")
    ap.add_argument("--vad-min-silence-ms", type=int, default=None, help="VAD 最短静音（毫秒）")
    ap.add_argument("--vad-pad-ms", type=int, default=None, help="VAD 语音两侧留白（毫秒）")
    ap.add_argument("--no-vad", action="store_true", help="关闭 VAD（短音频/纯人声）")
    ap.add_argument("--chunk-length", type=int, default=None,
                    help="解码 chunk 秒数（串行与批推理都生效；默认随模型）")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 result.json")
    ap.add_argument("--quiet", action="store_true", help="压制人类日志，只留错误")
    ap.add_argument("--version", action="version", version="vsum " + __version__)
    return ap.parse_args(argv)


def _llm_key(args):
    """--llm-key 优先，其次环境变量；本地 Ollama 不设也能用。"""
    return (args.llm_key or os.environ.get("VSUM_LLM_KEY")
            or os.environ.get("OPENAI_API_KEY") or None)


def _run_main(argv, prog="vsum"):
    """默认命令的完整流程：解析 → 跑 → 按 --json/--quiet 决定人类日志去哪。

    `vsum transcribe` 走同一个函数：子命令只是默认命令的显式名字，退出码、
    --json / --quiet 必须完全一致，否则「按文档写全名字」的脚本就解析不了。
    """
    args = _parse_main(argv, prog)

    def go():
        return pipeline.run(args.input, args.out, args.model, args.lang, args.pack,
                            args.clean, True if args.words else None,
                            browser=args.browser, fetch_only=args.fetch_only,
                            headless=True if args.headless else None, profile=args.profile,
                            summary_mode=args.summary, llm_url=args.llm_url,
                            llm_model=args.llm_model, llm_key=_llm_key(args),
                            force=args.force,
                            context=not args.no_context,
                            decode_opts=_decode_opts(args))

    if args.json:
        # stdout 只留那一行 result.json，进度日志全部让给 stderr
        with contextlib.redirect_stdout(sys.stderr):
            res = go()
        print(json.dumps(res, ensure_ascii=False))
    elif args.quiet:
        with contextlib.redirect_stdout(io.StringIO()):
            go()
    else:
        go()
    return 0


def _transcribe_cmd(argv):
    """默认命令的显式名字：用法和 vsum <文件> -o DIR 完全一致。"""
    return _run_main(argv, prog="vsum transcribe")


def _fetch_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum fetch", description="只抓取：抖音 / B站 / YouTube → 本地视频")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", required=True, help="产物目录，不存在会建")
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"],
                    help="用哪个浏览器；默认 Chrome，没装就 Edge")
    ap.add_argument("--headless", action="store_true", help="无头模式（可能被风控）")
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不起浏览器")
    ap.add_argument("--force", action="store_true",
                    help="不复用上次下好的视频，强制重新抓取")
    args = ap.parse_args(argv)
    info = fetch_mod.run(args.url, args.out, browser=args.browser, dry_run=args.dry_run,
                         headless=True if args.headless else None, profile=args.profile,
                         force=args.force)
    if not args.dry_run:
        print(info.get("video") or info.get("outDir") or args.out)
    return 0


def _login_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum login", description="打开抖音扫码登录，登录态存进独立 profile")
    ap.add_argument("site", choices=["douyin"])
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--timeout", type=int, default=300, help="等扫码的秒数，默认 300")
    args = ap.parse_args(argv)
    driver, kind = browser_mod.login_douyin(profile=args.profile, timeout=args.timeout)
    try:
        print("登录成功（%s，profile %s）" % (kind, browser_mod.profile_dir(args.profile)))
    finally:
        # login_douyin 自己不在成功路径 quit；这里关一次，保证 cookie 落盘。
        browser_mod.close(driver)
    return 0


def _batch_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum batch", description="一串链接或清单文件；默认串行，--jobs 可并行")
    ap.add_argument("list", help="urls.txt（一行一条）或 index.json（{\"items\":[{\"href\"}]}）")
    ap.add_argument("-o", "--out", required=True, help="产物根目录，每条一个子目录")
    ap.add_argument("--model", default="small", help="默认 small；auto 按显存取上限")
    ap.add_argument("--lang", default="zh", help="语言：zh / en / auto（默认 zh）")
    ap.add_argument("--pack", default=None, help="术语包 JSON，默认包内 data/terms.json")
    ap.add_argument("--no-context", action="store_true", help="关掉上文条件（不推荐，见 README）")
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"])
    ap.add_argument("--headless", action="store_true", help="无头模式（可能被风控）")
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--force", action="store_true", help="不跳过已完成的目录")
    ap.add_argument("--jobs", type=int, default=1,
                    help="并行条数（子进程），默认 1；多卡时再调高")
    ap.add_argument("--devices", default=None,
                    help="并行时用的卡，逗号分隔，如 cuda:0,cuda:1")
    ap.add_argument("--batch-size", default="1",
                    help="批推理大小：整数或 auto；默认 1")
    ap.add_argument("--vad-threshold", type=float, default=None, help="VAD 语音阈值，默认 0.5")
    ap.add_argument("--vad-min-speech-ms", type=int, default=None, help="VAD 最短语音（毫秒）")
    ap.add_argument("--vad-min-silence-ms", type=int, default=None, help="VAD 最短静音（毫秒）")
    ap.add_argument("--vad-pad-ms", type=int, default=None, help="VAD 语音两侧留白（毫秒）")
    ap.add_argument("--no-vad", action="store_true", help="关闭 VAD")
    ap.add_argument("--chunk-length", type=int, default=None,
                    help="解码 chunk 秒数（串行与批推理都生效；默认随模型）")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 batch.json")
    ap.add_argument("--quiet", action="store_true", help="压制人类日志")
    args = ap.parse_args(argv)
    if not os.path.exists(args.list):
        raise pipeline.InputError("清单文件不存在：%s" % args.list)
    if args.jobs < 1:
        raise pipeline.InputError("--jobs 必须 >= 1")
    call = lambda: batch_mod.run(args.list, args.out, args.model, args.lang, args.pack,
                                 browser=args.browser,
                                 headless=True if args.headless else None,
                                 profile=args.profile, force=args.force,
                                 context=not args.no_context, jobs=args.jobs,
                                 devices=args.devices, decode_opts=_decode_opts(args))
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            code = call()
        with open(os.path.join(args.out, "batch.json"), encoding="utf-8") as fh:
            print(json.dumps(json.load(fh), ensure_ascii=False))
        return code
    if args.quiet:
        with contextlib.redirect_stdout(io.StringIO()):
            return call()
    return call()


def _bench_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum bench", description="在本机音频上量 RTF，比较 compute_type")
    ap.add_argument("--audio", default=None, help="已有音频/视频；不给就配 --synth（仅 macOS）")
    ap.add_argument("--synth", action="store_true", help="macOS：用 say 合成一段测试音频")
    ap.add_argument("--model", default="tiny", help="默认 tiny（只量速度，不比准确率）")
    ap.add_argument("--compute", default=None, help="逗号分隔；默认按设备选")
    ap.add_argument("--lang", default="zh", help="语言：zh / en / auto（默认 zh）")
    ap.add_argument("-o", "--out", default=None, help="写 bench.json 的目录")
    args = ap.parse_args(argv)
    bench_mod.run(args.audio, args.synth, args.model, args.out, args.compute, args.lang)
    return 0


SUB = {
    "transcribe": _transcribe_cmd,
    "batch": _batch_cmd,
    "bench": _bench_cmd,
    "doctor": _doctor_cmd,
    "fetch": _fetch_cmd,
    "find": lookup.find_main,
    "clip": lookup.clip_main,
    "ocr": ocr_mod.main,
    "login": _login_cmd,
    "eval": evaluate.main,
    "holdout": holdout.main,
    "export": _export_cmd,
    "audit": audit.main,
    "fix": fix.main,
    "mine": mine_variants.main,
}


def _help_for(name):
    """vsum help <子命令>：把 argparse 的 --help 原样放出来。"""
    if name in ("help", "overview"):
        # help 本身是文档化的入口词，脚本会拿 `vsum help help` 探测支持情况
        print(overview())
        return 0
    if name not in SUB:
        print("error: 没有这个子命令：%s" % name, file=sys.stderr)
        print("可用：%s" % " ".join(sorted(SUB)), file=sys.stderr)
        return 2
    try:
        SUB[name](["--help"])
    except SystemExit as exc:            # argparse 打印完就退出
        return exc.code if isinstance(exc.code, int) else 0
    return 0


def _utf8_on_pipe():
    """输出被重定向或接管道时统一 UTF-8。

    Windows 控制台默认 cp936/cp1252，重定向时 Python 也按本地编码写，中文会变成
    \\uXXXX 转义，机读和日志都不可用；终端里保持本地编码不动。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _utf8_on_pipe()
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        # 总览：什么都不给、-h/--help、help 都打印同一屏
        if not argv or argv[0] in ("-h", "--help", "-?", "help"):
            if argv and argv[0] == "help" and len(argv) > 1:
                return _help_for(argv[1])
            print(overview())
            return 0
        # 子命令只在「确实不是同名文件」时生效：别把 vsum ocr.mp4 里的文件名当命令吃了
        if argv[0] in SUB and not os.path.exists(argv[0]):
            name = argv[0]
            try:
                rc = SUB[name](argv[1:])
            except SystemExit as exc:    # 子命令的 argparse --help / 用法错误
                return exc.code if isinstance(exc.code, int) else 0
            # 子命令要么返回退出码，要么返回 None（例如 ocr）：非 int 一律算成功。
            # 以前写的是 `rc or 0`，transcribe 返回 result 字典时 sys.exit(dict)
            # 会把字典打到 stderr 并以 1 退出——按 README 写全 `vsum transcribe` 的
            # 脚本就全挂了。
            return rc if isinstance(rc, int) else 0
        return _run_main(argv)
    except pipeline.InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        # 只打 strerror 的话用户看到的是「error: No such file or directory」，
        # 完全不知道是哪个文件；文件名才是能动手的那一半信息。
        detail = "%s：%s" % (exc.filename, exc.strerror) if exc.filename else (exc.strerror or exc)
        print("error: %s" % detail, file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print("error: JSON 解析失败：%s" % exc, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print("error: 没有写权限：%s" % (exc.filename or exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except transcribe.DependencyError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3
    except browser_mod.BrowserError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3
    except fetch_mod.FetchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 4
    except transcribe.TranscribeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 4
    except bench_mod.BenchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
