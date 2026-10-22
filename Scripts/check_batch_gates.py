# -*- coding: utf-8 -*-
"""
批次验证门模板（/autoplan main-20261021-153719 卫生门禁交付，TODOS P2）

未来批次每批次后复跑的门禁集合，固化 20261021 清理批次验证纪律：
  门1  pytest 全套（失败必须为 0）
  门2  pyautogui 两态白名单（--pyautogui-expected game|clean）
  门4  活模块 import 探测 + 已删模块反向断言（--forbid-import module1,module2）
  门3  git status 留给人工（预期外文件需人工判断，不自动化）

用法（仓库根）：
    python Scripts/check_batch_gates.py --pyautogui-expected clean --forbid-import game

退出码：0=全过，1=有门失败（失败明细见输出）。
"""

import argparse
import subprocess
import sys

# 门2 白名单：pyautogui 运行时引用的合法位点（保持与清理批次验证门 2 同步）
PYAUTOGUI_WHITELIST = {
    "utils/handlers/feature_handler.py",   # T5 按键触发惰性导入（活功能）
    "tests/test_t5_key_mapping.py",        # 缺依赖降级测试探测（门禁 A 管辖）
    "Scripts/check_batch_gates.py",        # 本门禁脚本自身（白名单定义元引用）
}


# 门5 红线扫描范围（义务 native C4/Eng S1，2026-10-22 字幕打印审查批次）：
# captions 相关文件集——不扫全库 requests/httpx 合法调用，防门禁误报。
CAPTIONS_REDLINE_FILES = [
    "utils/web_captions/captions_manager.py",
    "utils/web_captions/__init__.py",
    "utils/audio/playback_manager.py",
]
CAPTIONS_REDLINE_PATTERNS = ["requests.", "httpx.", "urllib.request", "aiohttp."]

# 门6 音频播放审查批次退役对象（/autoplan main-20261022-143149 批次 A3/A4/A5，
# 义务 R2-A3/R4）：定义级/活跃使用级模式，命中=退役对象回流。
# （注释性提及如"随 A3 退役"不在扫描模式内，不算命中。）
AUDIO_RETIRED_CODE_PATTERNS = [
    "def audio_speed_change",                 # A2：pydub 变速路径退役
    "def merge_audio_files",                  # A3：文案合并死代码退役
    "def send_audio_play_info_to_callback",   # A3：info_to_callback 死链回路退役
    "mixer_copywriting.music",                # A3：文案 mixer 使用
    "self.mixer_copywriting",                 # A3：文案 mixer 属性
    "my_global.wait_play_audio_num",          # A3：死全局消费
    "interval_num_min",                       # A4/A3：重复次数参数（键与消费）
    # A5：退役优先级节读取（豁免：audio_core 迁移读取器为 A5 交付物本身）
    ("audio_player\", \"priority_mapping", {"utils/audio/audio_core.py"}),
]
AUDIO_RETIRED_CONFIG_KEYS = [
    ("play_audio", "info_to_callback"),
    ("play_audio", "interval_num_min"),
    ("play_audio", "interval_num_max"),
    ("audio_random_speed", "copywriting"),
    ("audio_player", "priority_mapping"),
]
AUDIO_CONFIG_FILES = ["config.example.json", "config/tts/tts.json"]
AUDIO_NEW_KEYS_REQUIRED = [("play_audio", "play_interval_min"), ("play_audio", "play_interval_max")]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def gate1_pytest():
    print("=== 门1 pytest 全套 ===")
    r = run([sys.executable, "-m", "pytest", "-q", "--tb=line"])
    tail = "\n".join(r.stdout.strip().splitlines()[-3:])
    print(tail)
    ok = r.returncode == 0 and " failed" not in tail
    print(f"门1 {'PASS' if ok else 'FAIL'}")
    return ok


def gate2_pyautogui(expected_state):
    """两态白名单：game=删除 game/ 前的中间态（多 game/ 三文件命中）；clean=game/ 已删。"""
    print(f"=== 门2 pyautogui 白名单（期望态：{expected_state}）===")
    r = run(["git", "grep", "-n", "pyautogui", "--", "*.py"])
    hits = {}
    for line in r.stdout.splitlines():
        path = line.split(":", 1)[0]
        hits[path] = hits.get(path, 0) + 1
    allowed = {p: None for p in PYAUTOGUI_WHITELIST}
    if expected_state == "game":
        for f in ("game/game.py", "game/game1.py", "game/game2.py"):
            allowed[f] = None  # 中间态：game/ 命中数不限
    offenders = {p: n for p, n in hits.items() if p not in allowed}
    missing_whitelist = [p for p in PYAUTOGUI_WHITELIST if p not in hits]
    for p, n in sorted(hits.items()):
        print(f"  {p}: {n}")
    ok = not offenders
    if offenders:
        print(f"白名单外命中：{offenders}")
    if missing_whitelist:
        print(f"注意：白名单位点零命中（活性检查）——{missing_whitelist}")
    print(f"门2 {'PASS' if ok else 'FAIL'}")
    return ok


def gate4_imports(forbid):
    print("=== 门4 活模块 import 探测 ===")
    ok = True
    r = run([sys.executable, "-c", "import main; import webui; print('IMPORT PASS')"])
    ok &= r.returncode == 0 and "IMPORT PASS" in r.stdout
    print(f"  import main; import webui：{'PASS' if ok else 'FAIL ' + (r.stderr.strip().splitlines() or [''])[-1]}")
    for mod in forbid:
        r = run([sys.executable, "-c", f"import {mod}"])
        reverted = r.returncode != 0
        print(f"  反向断言 import {mod}：{'PASS（不可导入，符合已删预期）' if reverted else 'FAIL（已删模块仍可导入！）'}")
        ok &= reverted
    print(f"门4 {'PASS' if ok else 'FAIL'}")
    return ok


def gate5_captions_redline():
    """C4 红线：字幕链路不得回退外接 API 形态（整合形态验收基准——进程内模块、
    主系统内部 API 挂载、播放链路直调）。扫描范围限定 captions 文件集（Eng S1），
    注释行豁免；命中=字幕链路出现外接 HTTP 调用模式。"""
    print("=== 门5 字幕链路外接 API 红线（captions 文件集，义务 C4）===")
    ok = True
    for path in CAPTIONS_REDLINE_FILES:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            print(f"  {path}: MISSING（文件缺失，人工核对路径）")
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for pat in CAPTIONS_REDLINE_PATTERNS:
                if pat in line:
                    print(f"  FAIL {path}:{i} 命中 {pat!r}：{stripped[:80]}")
                    ok = False
    print(f"门5 {'PASS' if ok else 'FAIL'}")
    return ok


def gate6_audio_retired():
    """门6（2026-10-22 音频播放审查批次 A3/A4/A5）：退役对象反向断言——
    代码定义/活跃使用模式 git grep 归零 + 配置文件退役键缺席/新键在位。"""
    print("=== 门6 音频批次退役对象反向断言（A3/A4/A5）===")
    ok = True
    for pat in AUDIO_RETIRED_CODE_PATTERNS:
        allowed_files, pattern = set(), pat
        if isinstance(pat, tuple):
            pattern, allowed_files = pat
        # 排除本脚本自身（元引用，先例：0a5c8197 门2 自引白名单）
        r = run(["git", "grep", "-n", pattern, "--", "*.py", ":!Scripts/check_batch_gates.py"])
        offenders = [l for l in r.stdout.splitlines()
                     if l.split(":", 1)[0] not in allowed_files]
        if offenders:
            print(f"  FAIL 代码模式回流 {pattern!r}：{offenders[0][:100]}")
            ok = False
        else:
            print(f"  {pattern!r}: 0 命中（白名单外）")
    import json
    for cfg_file in AUDIO_CONFIG_FILES:
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  FAIL {cfg_file} 读取失败：{e}")
            ok = False
            continue
        for section, key in AUDIO_RETIRED_CONFIG_KEYS:
            node = cfg.get(section)
            if isinstance(node, dict) and key in node:
                print(f"  FAIL {cfg_file} 退役键仍在：{section}.{key}")
                ok = False
        for section, key in AUDIO_NEW_KEYS_REQUIRED:
            node = cfg.get(section) or {}
            if cfg_file == AUDIO_CONFIG_FILES[0] and key not in node:
                print(f"  FAIL {cfg_file} 新键缺席：{section}.{key}")
                ok = False
    print(f"门6 {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="批次验证门模板（门1/门2/门4；门3 git status 人工核对）")
    ap.add_argument("--pyautogui-expected", choices=["game", "clean"], default="clean",
                    help="门2 期望态：game=game/ 未删中间态；clean=game/ 已删（默认）")
    ap.add_argument("--forbid-import", default="",
                    help="门4 反向断言的已删模块清单（逗号分隔，如 game）")
    ap.add_argument("--skip-pytest", action="store_true", help="跳过门1（快查门2/门4 用）")
    args = ap.parse_args()

    results = []
    if not args.skip_pytest:
        results.append(("门1 pytest", gate1_pytest()))
    results.append(("门2 pyautogui", gate2_pyautogui(args.pyautogui_expected)))
    forbid = [m.strip() for m in args.forbid_import.split(",") if m.strip()]
    results.append(("门4 import", gate4_imports(forbid)))
    results.append(("门5 captions 红线", gate5_captions_redline()))
    results.append(("门6 音频退役对象", gate6_audio_retired()))

    print("\n=== 汇总 ===")
    for name, ok in results:
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    main()
