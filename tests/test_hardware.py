# -*- coding: utf-8 -*-
"""hardware 纯函数 + bench 参数校验。不需要 GPU，也不下载模型。"""
import contextlib
import io
import os
import sys

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

from vsum import bench  # noqa: E402
from vsum.pipeline import InputError  # noqa: E402

eq(bench.candidates("cuda"), ["float16", "int8_float16", "int8"], "cuda candidates")
eq(bench.candidates("cpu"), ["int8", "int8_float32"], "cpu candidates")

try:
    bench.run(audio=None, synth=False)
    FAILS.append("bench without audio should raise")
except InputError:
    pass

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
