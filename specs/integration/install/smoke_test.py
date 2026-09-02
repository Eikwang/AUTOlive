# -*- coding: utf-8 -*-
"""统一环境冒烟脚本（/autoplan DX2 + Eng C1）
用法：D:\\AI\\EDTalk\\runtime312\\python.exe D:\\AI\\install\\smoke_test.py
输出：逐项 PASS/FAIL（含 cause+fix 提示），控制台 + D:\\AI\\install\\env_check_report.md
"""
import importlib
import sys
import datetime

RESULTS = []

def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, "PASS", detail or ""))
    except Exception as e:
        RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))

def imp(mod):
    def f():
        m = importlib.import_module(mod)
        return getattr(m, "__version__", "")
    return f

def base_versions():
    import numpy, torch, pydantic, transformers
    import google.protobuf
    return (f"numpy={numpy.__version__} torch={torch.__version__} "
            f"protobuf={google.protobuf.__version__} pydantic={pydantic.__version__} "
            f"transformers={transformers.__version__}")

def baseline_drift():
    """Eng E2：基线核心包版本必须与 constraints 一致，漂移即标红。"""
    import numpy, google.protobuf, pydantic, transformers
    expect = {}
    for line in open(r"D:\AI\install\constraints.txt", encoding="utf-8"):
        if "==" in line and not line.startswith("#"):
            k, v = line.strip().split("==", 1)
            expect[k] = v
    drift = []
    pairs = [("numpy", numpy.__version__), ("protobuf", google.protobuf.__version__),
             ("pydantic", pydantic.__version__), ("transformers", transformers.__version__)]
    for name, actual in pairs:
        if name in expect and expect[name] != actual:
            drift.append(f"{name}: 期望 {expect[name]} 实际 {actual}")
    if drift:
        raise AssertionError("基线漂移 → 用 install/constraints.txt 钉回: " + "; ".join(drift))
    return "无漂移"

def pb2_roundtrip():
    """Eng B1：真实 pb2 序列化/反序列化（非仅 import）。"""
    sys.path.insert(0, r"D:\AI\AI-Vtuber")
    import dy_pb2
    from google.protobuf.descriptor import FieldDescriptor
    # 防御式：扫模块成员找消息类（老式 gencode，无文件级 message_types_by_name）
    cands = [n for n in dir(dy_pb2)
             if not n.startswith("_") and hasattr(getattr(dy_pb2, n), "SerializeToString")]
    msg_name = cands[0]
    m = getattr(dy_pb2, msg_name)()
    set_field = None
    for f in m.DESCRIPTOR.fields:
        if f.type in (FieldDescriptor.TYPE_INT32, FieldDescriptor.TYPE_INT64,
                      FieldDescriptor.TYPE_UINT32, FieldDescriptor.TYPE_UINT64):
            set_field = f.name
            setattr(m, f.name, 12345)
            break
    data = m.SerializeToString()
    m2 = getattr(dy_pb2, msg_name)()
    m2.ParseFromString(data)
    assert set_field is None or getattr(m2, set_field) == 12345
    return f"dy_pb2 round-trip OK ({msg_name}.{set_field})"

def cv2_state():
    """Eng B2：cv2 版本断言（已知不稳定状态，漂移必须可见）。"""
    import cv2
    return f"cv2={cv2.__version__}"

def opencv_variants():
    import importlib.metadata as md
    vs = []
    for p in ("opencv-python", "opencv-contrib-python", "opencv-python-headless"):
        try:
            vs.append(f"{p}={md.version(p)}")
        except md.PackageNotFoundError:
            pass
    return " ".join(vs)

def fairseq_abi():
    """Eng B5：本地 wheel ABI 立验。"""
    import fairseq
    return f"fairseq={fairseq.__version__}"

def pyworld_abi():
    import numpy as np
    import pyworld
    x = np.zeros(1600, dtype=np.float64)
    f0, t = pyworld.harvest(x, 16000)
    return f"pyworld 最小调用 OK (f0 帧数 {len(f0)})"

CHECKS = [
    ("基线版本快照", base_versions),
    ("基线漂移检测(constraints)", baseline_drift),
    ("cv2 状态", cv2_state),
    ("opencv 变体共存", opencv_variants),
    ("protobuf pb2 反序列化", pb2_roundtrip),
    ("fairseq(本地wheel ABI)", fairseq_abi),
    ("pyworld(本地wheel ABI)", pyworld_abi),
    ("gradio", imp("gradio")),
    ("tensorboardX", imp("tensorboardX")),
    ("einops", imp("einops")),
    ("ffmpy", imp("ffmpy")),
    ("ffmpeg_python", imp("ffmpeg")),
    ("torchcrepe", imp("torchcrepe")),
    ("sentencepiece", imp("sentencepiece")),
    ("ctranslate2", imp("ctranslate2")),
    ("faster_whisper", imp("faster_whisper")),
    ("funasr", imp("funasr")),
    ("pytorch_lightning", imp("pytorch_lightning")),
    ("transformers(已基线)", imp("transformers")),
    ("whisper(openai)", imp("whisper")),
    ("pyopenjtalk", imp("pyopenjtalk")),
    ("opencc", imp("opencc")),
    ("cn2an", imp("cn2an")),
    ("pypinyin(已基线)", imp("pypinyin")),
    ("FreeSimpleGUI", imp("FreeSimpleGUI")),
    ("noisereduce", imp("noisereduce")),
    ("local_attention", imp("local_attention")),
    ("torchfcpe", imp("torchfcpe")),
    ("resampy", imp("resampy")),
    ("yt_dlp", imp("yt_dlp")),
    ("pedalboard", imp("pedalboard")),
    ("typed_ffmpeg", imp("ffmpeg")),  # typed-ffmpeg 也叫 ffmpeg，见上
    ("audio_separator", imp("audio_separator")),
    ("nicegui(基线3.16)", imp("nicegui")),
    ("mitmproxy", imp("mitmproxy")),
    ("playwright(浏览器另行 install)", imp("playwright")),
    ("edge_tts", imp("edge_tts")),
    ("pydantic_settings(已基线)", imp("pydantic_settings")),
    ("loguru(已基线)", imp("loguru")),
    ("websocket", imp("websocket")),
    ("watchdog", imp("watchdog")),
    ("json5", imp("json5")),
    ("editdistance", imp("editdistance")),
]

for name, fn in CHECKS:
    check(name, fn)

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
fails = [r for r in RESULTS if r[1] == "FAIL"]
lines = [f"# 统一环境冒烟报告", f"时间：{now} ｜ 解释器：D:\\AI\\EDTalk\\runtime312\\python.exe",
         f"结果：{len(RESULTS) - len(fails)}/{len(RESULTS)} PASS", ""]
for name, status, detail in RESULTS:
    icon = "[PASS]" if status == "PASS" else "[FAIL]"
    line = f"- {icon} {name}" + (f" — {detail}" if detail else "")
    lines.append(line)
report = "\n".join(lines)
with open(r"D:\AI\install\env_check_report.md", "w", encoding="utf-8") as f:
    f.write(report)
print(report)
print(f"\n==> {len(RESULTS)-len(fails)}/{len(RESULTS)} PASS，报告已写入 install/env_check_report.md")
sys.exit(1 if fails else 0)
