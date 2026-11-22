# -*- coding: utf-8 -*-
"""vsum 命令行入口。"""
import argparse
import contextlib
import io
import json
import os
import sys

from . import (__version__, audit, batch as batch_mod, bench as bench_mod, browser as browser_mod, doctor, evaluate, export as export_mod, fetch as fetch_mod, fix, holdout, mine_variants, pipeline, transcribe)


def _export_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum export", description="从 segments.json 重新导出四件套")
    ap.add_argument("--segments", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.segments):
        raise pipeline.InputError("segments 文件不存在：%s" % args.segments)
    with open(args.segments, encoding="utf-8") as fh:
        segments = json.load(fh).get("segments", [])
    os.makedirs(args.out, exist_ok=True)
    clean_md = None
    if args.clean:
        from . import clean as clean_mod

        clean_md = clean_mod.clean_md(segments)
    outputs, warnings = export_mod.export_all(segments, args.out, clean_md, source=args.segments)
    for name, path in outputs.items():
        print("%-9s %s" % (name, os.path.basename(path)))
    if warnings["long_cues"]:
        print("long cues: %s" % ",".join(str(i) for i in warnings["long_cues"]))
    if warnings["invalid_cues"]:
        print("invalid cues: %s" % ",".join("%d(%s)" % (x["index"], x["reason"]) for x in warnings["invalid_cues"]))
    return 0


def _doctor_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum doctor", description="检查依赖")
    ap.add_argument("--model", default="small")
    args = ap.parse_args(argv)
    return doctor.run(args.model)


def _main_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum", description="逐字稿工具：vsum <file> -o DIR")
    ap.add_argument("input", help="本地音视频文件，或抖音/B站/YouTube 链接")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--pack", default=None, help="术语包（JSON）")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--words", action="store_true")
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"],
                    help="抓链接时用哪个浏览器；默认 Chrome，没装就 Edge，Safari 需显式指定")
    ap.add_argument("--headless", action="store_true", help="无头模式（可能被风控，默认可见窗口）")
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--fetch-only", action="store_true", help="只抓取，不转写")
    ap.add_argument("--summary", default="extractive", choices=["extractive", "llm", "none"])
    ap.add_argument("--llm-url", default=None, help="OpenAI-compatible base，如 http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--force", action="store_true", help="不复用旧转写")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 result.json")
    ap.add_argument("--quiet", action="store_true", help="压制人类日志")
    ap.add_argument("--version", action="version", version="vsum " + __version__)
    args = ap.parse_args(argv)
    return pipeline.run(args.input, args.out, args.model, args.lang, args.pack,
                        args.clean, True if args.words else None,
                        browser=args.browser, fetch_only=args.fetch_only,
                        headless=True if args.headless else None, profile=args.profile,
                        summary_mode=args.summary, llm_url=args.llm_url,
                        llm_model=args.llm_model, force=args.force)


def _fetch_cmd(argv):
    ap = argparse.ArgumentParser(prog="vsum fetch", description="只抓取：抖音 / B站 / YouTube → 本地视频")
    ap.add_argument("url")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"])
    ap.add_argument("--headless", action="store_true", help="无头模式（可能被风控）")
    ap.add_argument("--profile", default=None, help="浏览器独立 profile 目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不起浏览器")
    args = ap.parse_args(argv)
    info = fetch_mod.run(args.url, args.out, browser=args.browser, dry_run=args.dry_run,
                         headless=True if args.headless else None, profile=args.profile)
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
    ap = argparse.ArgumentParser(prog="vsum batch", description="一串链接串行跑，失败不中断")
    ap.add_argument("list", help="urls.txt（一行一条）或 index.json（{\"items\":[{\"href\"}]}）")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--pack", default=None)
    ap.add_argument("--browser", default=None, choices=["auto", "safari", "chrome", "edge"])
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--force", action="store_true", help="不跳过已完成的目录")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 batch.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.list):
        raise pipeline.InputError("清单文件不存在：%s" % args.list)
    call = lambda: batch_mod.run(args.list, args.out, args.model, args.lang, args.pack,
                                 browser=args.browser,
                                 headless=True if args.headless else None,
                                 profile=args.profile, force=args.force)
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
    ap.add_argument("--audio", default=None, help="已有音频/视频；不给就配 --synth")
    ap.add_argument("--synth", action="store_true", help="macOS：用 say 合成一段")
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--compute", default=None, help="逗号分隔；默认按设备选")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("-o", "--out", default=None, help="写 bench.json 的目录")
    args = ap.parse_args(argv)
    bench_mod.run(args.audio, args.synth, args.model, args.out, args.compute, args.lang)
    return 0


SUB = {
    "batch": _batch_cmd,
    "bench": _bench_cmd,
    "doctor": _doctor_cmd,
    "fetch": _fetch_cmd,
    "login": _login_cmd,
    "eval": evaluate.main,
    "holdout": holdout.main,
    "export": _export_cmd,
    "audit": audit.main,
    "fix": fix.main,
    "mine": mine_variants.main,
}


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
        if argv and argv[0] in SUB:
            return SUB[argv[0]](argv[1:])
        want_json = "--json" in argv
        quiet = "--quiet" in argv
        if want_json:
            with contextlib.redirect_stdout(sys.stderr):
                res = _main_cmd(argv)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if quiet:
            with contextlib.redirect_stdout(io.StringIO()):
                _main_cmd(argv)
            return 0
        _main_cmd(argv)
        return 0
    except pipeline.InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        print("error: %s" % (exc.strerror or exc), file=sys.stderr)
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
