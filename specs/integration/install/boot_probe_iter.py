# -*- coding: utf-8 -*-
"""迭代式启动阻断探测：每轮干净子进程只打"已证实阻断"的桩。
桩集合的最终值 = 缺失且无 try 保护（或保护后仍阻断）的模块 = 需补装/适配的启动阻断清单。
用法：runtime312 python.exe boot_probe_iter.py
"""
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PROBE = os.path.join(HERE, "boot_probe_child.py")

stubs = []          # 已证实阻断的模块
seen_fails = set()
history = []

for i in range(20):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["STUB_MODULES"] = ",".join(stubs)
    r = subprocess.run(
        [sys.executable, PROBE],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, env=env, timeout=300,
    )
    out = r.stdout + r.stderr
    history.append(out)

    # 找出未打桩的 ModuleNotFoundError（即新的阻断点）
    new_missing = None
    for line in out.splitlines():
        if "ModuleNotFoundError" in line and "No module named" in line:
            mod = line.split("No module named")[-1].strip().strip("'\"")
            if mod not in stubs:
                new_missing = mod
                break

    ok_marker = out.count("BOOT_OK:")
    if ok_marker == 2:  # 两个入口都通过
        print(f"[迭代 {i}] 两个入口全部通过。最终阻断清单: {stubs}")
        break
    if new_missing and new_missing not in seen_fails:
        seen_fails.add(new_missing)
        stubs.append(new_missing)
        print(f"[迭代 {i}] 新阻断点: {new_missing} → 加入桩集合 {stubs}")
    else:
        # 无新缺失但未全通过：非缺失类错误（API 不兼容等），输出细节后终止
        print(f"[迭代 {i}] 无新缺失模块但引导未全通过（非缺失类错误）。")
        print("桩集合:", stubs)
        print("---- 最后一次运行输出尾部 ----")
        print("\n".join(out.splitlines()[-30:]))
        break
else:
    print("迭代达上限，桩集合:", stubs)

print()
print("=" * 60)
print("最终启动阻断模块清单（缺失且阻断引导）:")
for s in stubs:
    print("  -", s)
if not stubs:
    print("  （无）")
