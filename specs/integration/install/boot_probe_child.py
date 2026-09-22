# -*- coding: utf-8 -*-
"""单轮引导探测子进程：只桩 STUB_MODULES 里列出的模块；输出 BOOT_OK 标记。"""
import os
import sys
import types
import importlib.abc

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

STUBS = [s for s in os.environ.get("STUB_MODULES", "").split(",") if s]

class NamedStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in STUBS:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec):
        m = types.ModuleType(spec.name)
        m.__version__ = "0.0.0-stub"
        m.__path__ = []
        return m

    def exec_module(self, module):
        pass

if STUBS:
    import importlib.machinery
    sys.meta_path.append(NamedStubFinder())

print(f"[child] 桩集合: {STUBS}", file=sys.stderr)

try:
    import webui  # noqa
    print("BOOT_OK: webui")
except Exception:
    import traceback
    traceback.print_exc()

# users.pth 遮蔽断言（永久项，/autoplan 2026-09-22 Eng T2）：
# runtime312 的 site-packages/users.pth 注入 6 个 GPT-SoVITS 路径，
# 关键本地模块必须解析到本项目，否则存在模块遮蔽。
try:
    import utils
    _utils_dir = os.path.dirname(os.path.abspath(utils.__file__))
    assert _utils_dir == os.path.join(ROOT, "utils"), f"utils 被遮蔽: {utils.__file__}"
    print("SHADOW_OK: utils 解析至本项目")
except AssertionError as e:
    print(f"SHADOW_FAIL: {e}")
except Exception:
    import traceback
    traceback.print_exc()

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "webui_bak", os.path.join(ROOT, "webui-bak.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    print("BOOT_OK: webui-bak")
except Exception:
    import traceback
    traceback.print_exc()
