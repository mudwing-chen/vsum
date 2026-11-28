# -*- coding: utf-8 -*-
"""hardware 纯函数 + bench 参数校验。不需要 GPU，也不下载模型。"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vsum import hardware  # noqa: E402

FAILS = []


def eq(got, want, name):
    if got != want:
        FAILS.append("%s: got %r want %r" % (name, got, want))


eq(hardware.parse_smi(""), [], "empty")
gpus = hardware.parse_smi("Tesla P100-PCIE-16GB, 16384 MiB, 6.0\n"
                          "NVIDIA GeForce RTX 3060, 12288 MiB, 8.6\n")
eq(len(gpus), 2, "two gpus")
eq(gpus[0]["name"], "Tesla P100-PCIE-16GB", "name")
eq(gpus[0]["vram_gb"], 16.0, "vram gb")
eq(gpus[1]["cc"], 8.6, "cc")
eq(hardware.parse_smi("bad line\n"), [], "bad line")

c = hardware.choose([])
eq((c["device"], c["compute_type"], c["model_cap"]), ("cpu", "int8", "medium"), "cpu")

c = hardware.choose([{"name": "GTX 1080", "vram_gb": 8.0, "cc": 6.1}])
eq((c["device"], c["compute_type"], c["model_cap"]), ("cuda", "int8_float32", "medium"),
   "pascal caps model")

c = hardware.choose([{"name": "RTX 3060", "vram_gb": 12.0, "cc": 8.6}])
eq(c["compute_type"], "float16", "ampere fp16")
eq(c["model_cap"], "large-v3", "ampere cap")

c = hardware.choose([{"name": "RTX 2060", "vram_gb": 6.0, "cc": 7.5}])
eq(c["compute_type"], "int8_float16", "small vram")

c = hardware.choose([{"name": "MX450", "vram_gb": 2.0, "cc": 7.5}])
eq((c["compute_type"], c["model_cap"]), ("int8_float16", "small"), "tiny vram")

# 两张卡时取显存大的
c = hardware.choose([{"name": "A", "vram_gb": 4.0, "cc": 7.5},
                     {"name": "B", "vram_gb": 24.0, "cc": 8.9}])
eq(c["name"], "B", "largest gpu")

os.environ.pop("WHISPER_DEVICE", None)
os.environ.pop("WHISPER_COMPUTE", None)
c = hardware.resolve(gpus=[], cuda_count=0)
eq((c["device"], c["source"]), ("cpu", "auto"), "resolve cpu")
os.environ["WHISPER_COMPUTE"] = "int8_float32"
c = hardware.resolve(gpus=[], cuda_count=0)
eq((c["compute_type"], c["source"]), ("int8_float32", "env"), "env override")
os.environ.pop("WHISPER_COMPUTE", None)

# 只覆盖设备时，compute 必须跟着设备走：GPU 判出的 float16 不能带到 CPU 上
GPU = [{"name": "RTX 3060", "vram_gb": 12.0, "cc": 8.6}]
eq(set(hardware.resolve(gpus=GPU, cuda_count=1)),
   {"device", "compute_type", "model_cap", "name", "cc", "vram_gb", "reason", "source"},
   "返回字段不变")
os.environ["WHISPER_DEVICE"] = "cpu"
c = hardware.resolve(gpus=GPU, cuda_count=1)
eq((c["device"], c["compute_type"]), ("cpu", "int8"), "cpu 不接受 GPU 的 float16")
eq(c["compute_type"] in hardware.CPU_COMPUTE, True, "cpu compute 在 CPU 白名单里")
eq(c["source"], "env", "cpu 覆盖记为 env")
if "WHISPER_DEVICE" not in c["reason"] or "float16" not in c["reason"]:
    FAILS.append("reason 要说明覆盖: %r" % c["reason"])

# 明确要了 CPU 能用的值就照办
os.environ["WHISPER_COMPUTE"] = "int8_float32"
c = hardware.resolve(gpus=GPU, cuda_count=1)
eq(c["compute_type"], "int8_float32", "显式 CPU 值胜出")
os.environ["WHISPER_COMPUTE"] = "float32"
c = hardware.resolve(gpus=[], cuda_count=0)
eq(c["compute_type"], "float32", "显式 float32 允许")
os.environ.pop("WHISPER_COMPUTE", None)

# 明确要了 float16 但设备是 CPU：纠正为 int8，并把原因写出来
os.environ["WHISPER_COMPUTE"] = "float16"
c = hardware.resolve(gpus=[], cuda_count=0)
eq(c["compute_type"], "int8", "cpu 上的 float16 被纠正")
if "float16" not in c["reason"] or "int8" not in c["reason"]:
    FAILS.append("纠正原因不完整: %r" % c["reason"])
os.environ["WHISPER_COMPUTE"] = "int8_float16"
c = hardware.resolve(gpus=[], cuda_count=0)
eq(c["compute_type"], "int8", "cpu 上的 int8_float16 被纠正")
os.environ.pop("WHISPER_COMPUTE", None)

# cuda 上显式给 float16 应原样保留（不误伤）
os.environ.pop("WHISPER_DEVICE", None)
os.environ["WHISPER_COMPUTE"] = "float16"
c = hardware.resolve(gpus=GPU, cuda_count=1)
eq((c["device"], c["compute_type"]), ("cuda", "float16"), "cuda 保留 float16")
os.environ.pop("WHISPER_COMPUTE", None)
os.environ.pop("WHISPER_DEVICE", None)

# M-8：PATH 里的 "." 不能把当前目录下的同名 exe 顶上来（Windows 上 shutil.which 会）
from vsum.toolpath import which  # noqa: E402

probe_name = "vsum-exe-probe.exe" if os.name == "nt" else "vsum-exe-probe"
probe_dir = tempfile.mkdtemp(prefix="vsum-path-")
probe_exe = os.path.join(probe_dir, probe_name)
with open(probe_exe, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(probe_exe, 0o755)

old_path = os.environ.get("PATH")
old_cwd = os.getcwd()
try:
    os.chdir(probe_dir)
    # 对照：只给 "." 时 shutil.which 真的会找到当前目录那一份
    os.environ["PATH"] = "."
    eq(shutil.which(probe_name) is not None, True, "对照组：shutil.which 认 '.'")
    eq(which(probe_name), None, "toolpath.which 剔除 '.'")
    # 前置 "." + 绝对目录：相对项被丢掉，命中的必须是目录里的绝对路径
    os.environ["PATH"] = "." + os.pathsep + probe_dir
    got = which(probe_name)
    eq(got, probe_exe, "绝对目录仍然可用")
    eq(os.path.isabs(got or ""), True, "不返回相对路径")
    # 带分隔符的参数按路径处理，不做 PATH 搜索
    eq(which(os.path.join(probe_dir, "nope")), None, "路径参数找不到就是 None")
    eq(which(probe_exe), probe_exe, "路径参数存在就返回绝对路径")
finally:
    os.chdir(old_cwd)
    if old_path is None:
        os.environ.pop("PATH", None)
    else:
        os.environ["PATH"] = old_path
    shutil.rmtree(probe_dir, ignore_errors=True)

from vsum import bench  # noqa: E402
from vsum.pipeline import InputError  # noqa: E402

eq(bench.candidates("cuda"), ["float16", "int8_float16", "int8"], "cuda candidates")
eq(bench.candidates("cpu"), ["int8", "int8_float32"], "cpu candidates")
# Pascal 上 float16 是模拟的，bench 必须以实际会用的 compute_type 先跑
eq(bench.candidates("cuda", "int8_float32"),
   ["int8_float32", "float16", "int8_float16", "int8"], "preferred first (cuda)")
eq(bench.candidates("cpu", "int8"), ["int8", "int8_float32"], "preferred first (cpu)")

try:
    bench.run(audio=None, synth=False)
    FAILS.append("bench without audio should raise")
except InputError:
    pass

# L-1：--synth 的临时文件名不可预测（CWE-377），也不再共用固定名字
said = []


def fake_say(argv, **kwargs):
    said.append(argv)
    with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as fh:
        fh.write("FAKE")
    return subprocess.CompletedProcess(argv, 0)

orig_run, orig_which, orig_platform = bench.subprocess.run, bench.which, sys.platform
try:
    bench.subprocess.run = fake_say
    bench.which = lambda cmd: "/usr/bin/say"
    sys.platform = "darwin"
    p1 = bench.synth_audio()
    p2 = bench.synth_audio()
finally:
    bench.subprocess.run, bench.which = orig_run, orig_which
    sys.platform = orig_platform
eq(os.path.exists(p1), True, "synth 产出文件")
eq(p1 != p2, True, "两次合成名字不同（不可预测）")
eq(os.path.basename(p1).startswith("vsum-bench-"), True, "mkstemp 临时名前缀")
for p in (p1, p2):
    if os.path.exists(p):
        os.remove(p)

# I-14：doctor 推荐的 `vsum bench --model auto` 必须先把 auto 解析成具体型号
seen = {}


def fake_to_wav(src, workdir):
    os.makedirs(workdir, exist_ok=True)
    return os.path.join(workdir, "bench-16k.wav")


def fake_run_one(audio, model_name, device, compute, language="zh"):
    seen["model"] = model_name
    return {"compute_type": compute, "ok": False, "error": "fake"}


audio_dir = tempfile.mkdtemp(prefix="vsum-bench-audio-")
audio_path = os.path.join(audio_dir, "clip.wav")
with open(audio_path, "wb") as fh:
    fh.write(b"RIFF")
orig_to_wav, orig_run_one = bench.to_wav, bench.run_one
try:
    bench.to_wav, bench.run_one = fake_to_wav, fake_run_one
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = bench.run(audio=audio_path, model_name="auto", computes="int8")
finally:
    bench.to_wav, bench.run_one = orig_to_wav, orig_run_one
    shutil.rmtree(audio_dir, ignore_errors=True)
out = buf.getvalue()
eq(seen.get("model") != "auto", True, "auto 不直接交给 WhisperModel")
eq(res["model"], seen.get("model"), "结果里的 model 是解析后的")
eq(res["model_requested"], "auto", "保留了用户请求")
eq("auto → " in out, True, "输出里印出 auto → 型号：%r" % out[:80])

from vsum import doctor  # noqa: E402

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = doctor.run(model="tiny")
out = buf.getvalue()
eq(any(l.startswith("browser") for l in out.splitlines()), True, "doctor browser row")
eq(isinstance(code, int), True, "doctor return code is int")
eq(doctor.ffmpeg_hint("win32").startswith("winget"), True, "Windows 不给 apt 建议")
eq(doctor.ffmpeg_hint("darwin").startswith("brew"), True, "macOS 给 brew")
eq(doctor.ffmpeg_hint("linux").startswith("apt"), True, "Linux 给 apt")

if FAILS:
    print("FAIL")
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
print("OK hardware")
