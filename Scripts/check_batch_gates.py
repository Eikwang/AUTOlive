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

    print("\n=== 汇总 ===")
    for name, ok in results:
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if all(ok for _, ok in results) else 1)


if __name__ == "__main__":
    main()
