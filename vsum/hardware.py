# -*- coding: utf-8 -*-
"""硬件探测与 compute_type 选择。

规则是**启发式**，不是实测数据：Pascal（cc 6.x）消费级卡 fp16 吞吐只有 1/64，
所以走 int8_float32；cc>=7 且显存够才用 float16。真实速度以 `vsum bench` 为准。
环境变量 WHISPER_DEVICE / WHISPER_COMPUTE 仍然优先，但算出的 compute_type 要过设备
校验：CPU 上不会被 GPU 判出的 float16 带崩。
"""
import os
import shutil
import subprocess

# 显存(GB) 下限 -> 建议模型上限；仅用于 doctor 提示，不强制
MODEL_CAP = ((4, "small"), (8, "medium"), (1 << 30, "large-v3"))

# 各后端实际能加载的 compute_type；不在表里的值会在 resolve 里被纠正。
# CPU 后端不认 float16/int8_float16，cuda 后端则都认（该表里只剩拼错的值会被拦）；
# default/auto 交给 ctranslate2 自己按后端挑，两边都安全。
CPU_COMPUTE = ("int8", "int8_float32", "float32", "default", "auto")
CUDA_COMPUTE = ("float16", "int8_float16", "int8_bfloat16", "int8_float32", "bfloat16",
                "float32", "int8", "int16", "default", "auto")


def parse_smi(text):
    """解析 nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader。"""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        name, mem, cc = parts
        try:
            mib = float(mem.split()[0])
            cap = float(cc)
        except (ValueError, IndexError):
            continue
        out.append({"name": name, "vram_gb": round(mib / 1024.0, 1), "cc": cap})
    return out


def model_cap(vram_gb):
    for limit, name in MODEL_CAP:
        if vram_gb < limit:
            return name
    return "large-v3"


def choose(gpus):
    """只按最显存大的那张卡选；返回 device/compute_type/model_cap/reason。"""
    if not gpus:
        return {"device": "cpu", "compute_type": "int8", "model_cap": "medium",
                "name": None, "cc": None, "vram_gb": None,
                "reason": "没有 NVIDIA GPU：CPU int8，模型上限 medium"}
    gpu = max(gpus, key=lambda g: g["vram_gb"])
    cap = model_cap(gpu["vram_gb"])
    if gpu["cc"] < 7:
        compute = "int8_float32"
        cap = "medium"  # Pascal 上 large 基本划不来
        why = "cc %.1f < 7：Pascal 级 fp16 很慢，用 int8_float32" % gpu["cc"]
    elif gpu["vram_gb"] >= 8:
        compute = "float16"
        why = "cc %.1f + %.1f GB：float16" % (gpu["cc"], gpu["vram_gb"])
    else:
        compute = "int8_float16"
        why = "cc %.1f 但显存 %.1f GB < 8：int8_float16" % (gpu["cc"], gpu["vram_gb"])
    return {"device": "cuda", "compute_type": compute, "model_cap": cap,
            "name": gpu["name"], "cc": gpu["cc"], "vram_gb": gpu["vram_gb"],
            "reason": why}


def probe():
    """有 nvidia-smi 才探测；失败返回 []，不抛。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,compute_cap",
                              "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10)
        return parse_smi(out.stdout)
    except Exception:
        return []


def _cuda_device_count():
    """ctranslate2 报的 CUDA 设备数；导入失败或没装当 0。

    `import ctranslate2` 会去加载 CUDA/cuDNN 运行库，本机实测冷启动 25~77 秒。
    所以只有当 nvidia-smi 什么都没探到、但机器上确实有 CUDA 运行库（nvcuda.dll）时
    才走这条路——那是 WSL/容器里「没有 nvidia-smi 但有卡」的兜底。
    有 nvidia-smi 且报了卡的情况不需要问 ctranslate2，省的这几秒曾让 `vsum doctor`
    看起来像挂死。
    """
    if not _cuda_runtime_present():
        return 0
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def _cuda_runtime_present():
    if os.environ.get("CUDA_PATH"):
        return True
    for root in (os.environ.get("SystemRoot", r"C:\Windows"), r"C:\Windows"):
        if os.path.exists(os.path.join(root, "System32", "nvcuda.dll")):
            return True
    return False


def resolve(gpus=None, cuda_count=None):
    """优先级：WHISPER_DEVICE / WHISPER_COMPUTE > 自动探测 > CPU 兜底。

    两个变量可以各自覆盖，但 compute_type 必须和**最终** device 相容：只设
    WHISPER_DEVICE=cpu 时若把 GPU 判出来的 float16 原样带过去，CPU 后端会拒绝加载，
    现场只看到「模型加载失败（small / cpu / float16）」而看不出根因。顺序因此是
    先定设备、再校验 compute；被纠正的值和原因都写进 reason（`vsum doctor` 会显示）。
    """
    env_device = os.environ.get("WHISPER_DEVICE", "").strip()
    env_compute = os.environ.get("WHISPER_COMPUTE", "").strip()
    if gpus is None:
        gpus = probe()
    if cuda_count is None:
        # nvidia-smi 已经报了卡就不用再问 ctranslate2：结论一样，代价是几十秒。
        cuda_count = len(gpus) if gpus else _cuda_device_count()
    auto = choose(gpus) if cuda_count else choose([])
    cfg = dict(auto)
    cfg["source"] = "auto"
    if not (env_device or env_compute):
        return cfg

    notes = []
    device = env_device or auto["device"]
    if env_device:
        notes.append("设备按 WHISPER_DEVICE=%s" % env_device +
                     ("" if env_device == auto["device"] else "（自动探测为 %s）" % auto["device"]))
    want = env_compute or auto["compute_type"]
    valid = CPU_COMPUTE if device == "cpu" else CUDA_COMPUTE
    if want in valid:
        compute = want
        if env_compute:
            notes.append("compute 按 WHISPER_COMPUTE=%s" % env_compute)
    else:
        compute = "int8" if device == "cpu" else (
            auto["compute_type"] if auto["compute_type"] in CUDA_COMPUTE else "float16")
        notes.append("%s%s 在 %s 上不可用，改用 %s" %
                     ("WHISPER_COMPUTE=" if env_compute else "自动判定的 ", want, device, compute))
    cfg["device"] = device
    cfg["compute_type"] = compute
    cfg["source"] = "env"
    cfg["reason"] = "；".join(notes + [auto["reason"]])
    return cfg
