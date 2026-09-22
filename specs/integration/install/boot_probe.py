# -*- coding: utf-8 -*-
"""启动期全量缺口探测：缺失模块自动打桩并记录（一次性暴露全部 ModuleNotFoundError），
非缺失类异常（API 不兼容等）原样抛出。"""
import sys, types, os
import importlib.abc, importlib.machinery

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))  # .../specs/integration/install → AI-Vtuber 根
sys.path.insert(0, ROOT)

def is_local_top(top):
    p = os.path.join(ROOT, top)
    return os.path.isfile(p + ".py") or (os.path.isdir(p) and any(
        x.endswith(".py") for x in os.listdir(p)))

stubbed = []
class StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if is_local_top(top):
            return None  # 本地模块绝不打桩（真实错误必须暴露）
        if fullname in sys.modules:
            return None
        # 先让所有其他查找器（含 six 的虚拟模块导入器等）尝试；全部失败才打桩
        for f in list(sys.meta_path):
            if f is self:
                continue
            try:
                spec = f.find_spec(fullname, path, target)
            except Exception:
                continue
            if spec is not None:
                return None
        try:
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        except Exception:
            spec = None
        if spec is not None:
            return None
        stubbed.append(fullname)
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
    def create_module(self, spec):
        m = types.ModuleType(spec.name)
        m.__version__ = "0.0.0-stub"
        m.__path__ = []
        return m
    def exec_module(self, module):
        pass

sys.meta_path.append(StubFinder())

fails = []
print("=== 1) webui.py ===")
try:
    import webui
    print("OK")
except Exception as e:
    import traceback; traceback.print_exc()
    fails.append(("webui", e))
print()
print("=== 2) webui-bak.py ===")
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("webui_bak", os.path.join(ROOT, "webui-bak.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    print("OK")
except Exception as e:
    import traceback; traceback.print_exc()
    fails.append(("webui-bak", e))

print()
print("=" * 60)
print("自动打桩记录（= 在 runtime312 中缺失的模块，含二级路径）:")
for s in sorted(set(stubbed)):
    print("  MISSING:", s)
if not stubbed:
    print("  （无）")
