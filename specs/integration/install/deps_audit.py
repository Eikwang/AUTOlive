# -*- coding: utf-8 -*-
"""AUTOlive webui.py / webui-bak.py 全量依赖审计（runtime312 适配前置调查）

用法：D:\\AI\\EDTalk\\runtime312\\python.exe deps_audit.py
逻辑：AST 解析两个入口 → 递归跟随项目内本地模块（含父包 __init__ 链）→ 收集第三方 import
      → 在当前解释器（runtime312）用 find_spec 实测可用性 → 输出缺失清单（含使用模块定位）
口径：guarded=try 包裹的可选导入；lazy=函数体内延迟导入；required=模块顶层无条件导入
"""
import ast
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))  # .../specs/integration/install → AUTOlive 根
ENTRIES = ["webui.py", "webui-bak.py"]

imports = {}        # top_name -> {file: [(lineno, kind)]}
local_files = set()
pending = [os.path.join(ROOT, e) for e in ENTRIES]
seen = set()

# ---------- 路径解析 ----------

def file_or_pkg(*parts):
    """返回 (file, pkg_init) 候选：mod.py 优先，其次 mod/__init__.py"""
    p = os.path.join(ROOT, *parts)
    f, i = p + ".py", os.path.join(p, "__init__.py")
    return f, i

def dir_is_code(*parts):
    """目录含 .py 才算本地代码包（排除 data/ 等同名数据目录）"""
    p = os.path.join(ROOT, *parts)
    return os.path.isdir(p) and any(x.endswith(".py") for x in os.listdir(p))

def is_local_top(top):
    return os.path.isfile(os.path.join(ROOT, top + ".py")) or dir_is_code(top)

def enqueue_module(mod):
    """import a.b.c / from a.b.c import x → 入队执行链（父包 __init__ + 模块本体）"""
    parts = mod.split(".")
    for i in range(1, len(parts)):
        _, ini = file_or_pkg(*parts[:i])
        if os.path.isfile(ini):
            pending.append(ini)
    f, ini = file_or_pkg(*parts)
    if os.path.isfile(f):
        pending.append(f)
    elif os.path.isfile(ini):
        pending.append(ini)

def enqueue_relative(fname, level, mod):
    """相对导入：基于当前文件目录上溯 level-1 层"""
    cur_dir = os.path.dirname(os.path.join(ROOT, fname))
    for _ in range(level - 1):
        cur_dir = os.path.dirname(cur_dir)
    if mod:
        enqueue_module_abs_dir(cur_dir, mod)
    else:
        # from . import x：展开 x 为子模块入队
        base = cur_dir
        return base, None
    return None, None

def enqueue_module_abs_dir(base_dir, mod):
    parts = mod.split(".")
    for i in range(1, len(parts)):
        _, ini = file_or_pkg(*parts[:i])
        cand = os.path.join(os.path.dirname(ini))  # noqa
    p = os.path.join(base_dir, *parts)
    f, ini = p + ".py", os.path.join(p, "__init__.py")
    if os.path.isfile(f):
        pending.append(f)
    elif os.path.isfile(ini):
        pending.append(ini)

# ---------- 解析 ----------

def parse_file(path):
    fname = os.path.relpath(path, ROOT).replace("\\", "/")
    local_files.add(fname)
    with open(path, "rb") as f:
        src = f.read()
    tree = ast.parse(src, filename=fname)

    def in_try(node):
        cur = getattr(node, "_aparent", None)
        while cur is not None:
            if isinstance(cur, ast.Try):
                return True
            cur = getattr(cur, "_aparent", None)
        return False

    def in_func(node):
        cur = getattr(node, "_aparent", None)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return True
            cur = getattr(cur, "_aparent", None)
        return False

    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._aparent = node
        if isinstance(node, ast.Import):
            for a in node.names:
                mod = a.name
                top = mod.split(".")[0]
                if top in sys.stdlib_module_names:
                    continue
                if is_local_top(top):
                    enqueue_module(mod)
                else:
                    kind = "guarded" if in_try(node) else ("lazy" if in_func(node) else "required")
                    imports.setdefault(top, {}).setdefault(fname, []).append((node.lineno, kind))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # 相对导入 → 项目内，解析入队
                cur_dir = os.path.dirname(os.path.join(ROOT, fname))
                for _ in range(node.level - 1):
                    cur_dir = os.path.dirname(cur_dir)
                if node.module:
                    p = os.path.join(cur_dir, *node.module.split("."))
                    f, ini = p + ".py", os.path.join(p, "__init__.py")
                    if os.path.isfile(f):
                        pending.append(f)
                    elif os.path.isfile(ini):
                        pending.append(ini)
                    elif dir_is_code_abs(cur_dir, node.module.split(".")[0]):
                        # 命名空间包 from .sub import x：子项由调用处显式 import 决定，
                        # 这里把 from .pkg.mod import x 的 mod 已覆盖；from .pkg import x 的 x
                        # 若是子模块，尝试展开别名
                        for a in node.names:
                            if a.name == "*":
                                continue
                            sf = os.path.join(p, a.name.replace(".", "/") + ".py")
                            if os.path.isfile(sf):
                                pending.append(sf)
                else:
                    p = cur_dir
                    for a in node.names:
                        if a.name == "*":
                            continue
                        sf = os.path.join(p, a.name.replace(".", "/") + ".py")
                        si = os.path.join(p, a.name.replace(".", "/"), "__init__.py")
                        if os.path.isfile(sf):
                            pending.append(sf)
                        elif os.path.isfile(si):
                            pending.append(si)
                continue
            mod = node.module or ""
            top = mod.split(".")[0] if mod else ""
            if not top:
                continue
            if top in sys.stdlib_module_names:
                continue
            if is_local_top(top):
                enqueue_module(mod)
                # from pkg import name：name 也可能是 pkg 下的子模块
                for a in node.names:
                    if a.name == "*":
                        continue
                    sf = os.path.join(ROOT, *mod.split("."), a.name.replace(".", "/") + ".py")
                    si = os.path.join(ROOT, *mod.split("."), a.name.replace(".", "/"), "__init__.py")
                    if os.path.isfile(sf):
                        pending.append(sf)
                    elif os.path.isfile(si):
                        pending.append(si)
            else:
                kind = "guarded" if in_try(node) else ("lazy" if in_func(node) else "required")
                imports.setdefault(top, {}).setdefault(fname, []).append((node.lineno, kind))

def dir_is_code_abs(base, top):
    p = os.path.join(base, top)
    return os.path.isdir(p) and any(x.endswith(".py") for x in os.listdir(p))

while pending:
    p = os.path.abspath(pending.pop())
    if p in seen or not os.path.isfile(p):
        continue
    seen.add(p)
    try:
        parse_file(p)
    except SyntaxError as e:
        print(f"[SYNTAX WARN] {os.path.relpath(p, ROOT)}: {e}", file=sys.stderr)

# ---------- 在当前解释器验证 ----------
import importlib.util
import importlib.metadata as im

missing = {}
found = 0
for top, users in sorted(imports.items()):
    try:
        spec = importlib.util.find_spec(top)
        ok = spec is not None
        find_err = ""
    except Exception as e:
        ok = False
        find_err = f" ({type(e).__name__}: {e})"
    if ok:
        found += 1
        continue
    dist = None
    try:
        pd = im.packages_distributions()
        d = pd.get(top)
        dist = d[0] if d else None
    except Exception:
        pass
    entry = missing.setdefault(top, {"kinds": set(), "users": {}, "dist": dist or top})
    if find_err:
        entry["find_err"] = find_err
    for f, locs in users.items():
        entry["users"][f] = [(l, k) for l, k in locs]
        for l, k in locs:
            entry["kinds"].add(k)

total = len(imports)
print(f"扫描文件数: {len(local_files)}  |  第三方顶层导入: {total}  |  当前解释器可用: {found}  |  缺失: {len(missing)}")
print(f"解释器: {sys.executable}")
print()

if missing:
    order = {"required": 0, "lazy": 1, "guarded": 2}

    def worst(kinds):
        return min(order[k] for k in kinds)

    print("=" * 70)
    print("缺失依赖清单（不安装，仅报告；required 最严重优先）")
    print("=" * 70)
    for top, info in sorted(missing.items(), key=lambda kv: worst(kv[1]["kinds"])):
        kinds = "/".join(sorted(info["kinds"], key=lambda k: order[k]))
        print(f"\n[{kinds}] {top}  (pip 名: {info['dist']}){info.get('find_err','')}")
        for f, locs in sorted(info["users"].items()):
            lines = ",".join(str(l) for l, k in locs)
            print(f"    {f}: 行 {lines}")
else:
    print("无缺失。")

# 附：已确认可用的导入清单（供计划引用）
print()
print("-" * 70)
print("附：可用第三方导入（按文件聚合，前 200 条）")
by_file = {}
for top, users in imports.items():
    for f in users:
        by_file.setdefault(f, []).append(top)
for f in sorted(by_file):
    print(f"  {f}: {', '.join(sorted(set(by_file[f])))}")
