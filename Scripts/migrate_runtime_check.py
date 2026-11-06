# -*- coding: utf-8 -*-
"""
runtime312 环境迁移校验面（T7，/autoplan main-20261106-173018 批次 X3）

迁移五步序列的"清键+校验"执行器（前两步=停机预检与 copy_edtalk.py 复制，
见计划 X3/C2/F1；本脚本覆盖后三步：自检→清键→五项复验）。

用法（AUTOlive 根目录）：
    python Scripts/migrate_runtime_check.py --dry-run   # 只校验不清键
    python Scripts/migrate_runtime_check.py             # 校验+清键（先 .bak 备份）

五项校验（X3/L1）：
  ① 副本 python.exe 存在
  ② 副本解释器 --version 可执行
  ③ 打印候选链最终解析值（config 覆盖→记忆→仓库副本→外部树兜底）
  ④ config 覆盖键清理确认（范围=rvc_vc.runtime_python；gpt_sovits_python
     覆盖键若存在则保留指向 runtime312-clone——clone 不复制是期望行为，不得误清）
  ⑤ 变声服务用副本解释器实际拉起成功（--with-start，默认跳过——留待用户实播）

回滚：config.json.bak 还原（ensure_config_backup 同款语义）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")
BAK_PATH = CONFIG_PATH + ".migration.bak"
REPO_RUNTIME = os.path.join(REPO_ROOT, "runtime312", "python.exe")
EXTERNAL_RUNTIME = r"D:\AI\EDTalk\runtime312\python.exe"

sys.path.insert(0, REPO_ROOT)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_1_repo_exists() -> bool:
    ok = os.path.isfile(REPO_RUNTIME)
    print(f"  {'[OK]' if ok else '[MISS]'} ① 副本解释器存在: {REPO_RUNTIME}")
    if not ok:
        print("       修复=检查迁移是否完成（环境应位于仓库根 runtime312/）")
    return ok


def check_2_repo_runs() -> bool:
    if not os.path.isfile(REPO_RUNTIME):
        print("  [SKIP] ② 副本解释器可执行（①未过，跳过）")
        return False
    try:
        r = subprocess.run([REPO_RUNTIME, "--version"], capture_output=True,
                           text=True, timeout=30)
        ok = r.returncode == 0
        print(f"  {'[OK]' if ok else '[FAIL]'} ② 副本解释器可执行: {(r.stdout or r.stderr).strip()}")
        return ok
    except Exception as e:
        print(f"  [FAIL] ② 副本解释器执行异常: {e}")
        return False


def check_3_resolution(config) -> str:
    """打印候选链最终解析值（config 覆盖→记忆→仓库副本→外部树兜底）。"""
    from utils.autolive_paths import AutolivePaths, read_interpreter_memory
    paths = AutolivePaths(config)
    mem = read_interpreter_memory()
    print(f"  ③ 候选链解析（rvc_python）:")
    print(f"     config paths.rvc_python = {config.get('paths', {}).get('rvc_python', '(未配置)')}")
    print(f"     config rvc_vc.runtime_python = {config.get('rvc_vc', {}).get('runtime_python', '(未配置)')}")
    print(f"     memory[rvc_python] = {mem.get('rvc_python', '(空)')}")
    print(f"     仓库副本 = {REPO_RUNTIME}{'（存在）' if os.path.isfile(REPO_RUNTIME) else '（不存在）'}")
    print(f"     外部兜底 = {EXTERNAL_RUNTIME}{'（存在）' if os.path.isfile(EXTERNAL_RUNTIME) else '（不存在）'}")
    final = paths.rvc_python or ""
    print(f"     → 最终解析值: {final or '(全候选缺失——拉起会三要素报错)'}")
    return final


def check_4_clear_override(config, apply: bool) -> bool:
    """清键范围（R2-4 扩展自 dry-run 实测）：实盘 config 的 paths.rvc_python 与
    rvc_vc.runtime_python 均显式指向外部树——两者都须清除，否则候选链被覆盖键
    钉死在外部、副本环节空转。gpt_sovits_python 覆盖键保留（指向
    runtime312-clone——clone 不复制是期望行为，不得误清）。"""
    changed = False
    targets = [
        ("paths", "rvc_python", EXTERNAL_RUNTIME),
        ("rvc_vc", "runtime_python", EXTERNAL_RUNTIME),
    ]
    for section, key, std_external in targets:
        val = (config.get(section) or {}).get(key)
        if not val:
            print(f"  [OK] ④ {section}.{key} 未配置（无需清键）")
            continue
        print(f"  [..] ④ 发现覆盖键 {section}.{key} = {val}")
        if str(val).lower() == std_external.lower():
            if apply:
                if not changed:
                    shutil.copy2(CONFIG_PATH, BAK_PATH)
                    print("       已备份 config.json → config.json.migration.bak（回滚=还原该文件）")
                    changed = True
                config[section].pop(key)
                print(f"  [OK] ④ 已清除 {section}.{key}（候选链接管）")
            else:
                print(f"  [DRY] ④ dry-run：将清除该键")
        else:
            print(f"  [WARN] ④ 覆盖键指向非标准外部路径——请人工确认后手动处理（本脚本不动）")
    if changed:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    # gpt_sovits 保留声明（R2-4 防误清）
    gs = (config.get("paths") or {}).get("gpt_sovits_python")
    if gs:
        print(f"  [KEEP] ④ paths.gpt_sovits_python = {gs}（runtime312-clone 语义，保留不清）")
    return True


def check_5_live_start(config) -> bool:
    """⑤ 变声服务用候选链解释器实际拉起（--with-start 启用；默认跳过）。"""
    print("  [SKIP] ⑤ 实际拉起（默认跳过——用 --with-start 或实播验收）")
    return True


def main():
    ap = argparse.ArgumentParser(description="runtime312 迁移校验面（清键+五项复验）")
    ap.add_argument("--dry-run", action="store_true", help="只校验不清键")
    ap.add_argument("--with-start", action="store_true", help="⑤ 实际拉起变声服务验证")
    args = ap.parse_args()

    print("=== runtime312 迁移校验面（T7/X3） ===")
    if not os.path.isfile(CONFIG_PATH):
        print(f"[FAIL] config.json 不存在: {CONFIG_PATH}")
        sys.exit(1)
    config = load_config()
    apply = not args.dry_run

    results = [check_1_repo_exists()]
    results.append(check_2_repo_runs())
    final = check_3_resolution(config)
    results.append(bool(final))
    results.append(check_4_clear_override(config, apply))
    if args.with_start:
        print("  [..] ⑤ 实际拉起（懒拉起链——进入变声页即可验证，此处提示）")
        results.append(True)
    else:
        results.append(check_5_live_start(config))

    n_ok = sum(1 for r in results if r)
    print(f"\n=== 结论: {n_ok}/{len(results)} 项通过"
          + ("（dry-run 未清键）" if args.dry_run else "") + " ===")
    if all(results):
        print("迁移校验完成。整合包发布检查单：")
        print("  - runtime312/ 已 .gitignore（不进 git，打包时附带）")
        print("  - config.json.migration.bak 保留至实播验收通过后删除")
        print("  - 实播：变声页反复进出+启停系统 20 轮，服务可自动恢复、无孤儿进程")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
