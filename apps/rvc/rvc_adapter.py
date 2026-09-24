# -*- coding: utf-8 -*-
"""
RVC 实时变声服务薄入口（P2-1，E-3：apps/ 只放薄入口）

由 RVC 自带 runtime 的 python 执行（子进程）：
    runtime/python.exe apps/rvc/rvc_adapter.py --autolive-home <AUTOlive根>

职责仅 2 行：把 AUTOlive 树挂上 sys.path（PYTHONPATH 反向注入）→ 调主实现 main()。
全部逻辑在 utils/rvc_realtime_service.py（E-3：主实现入 AUTOlive 树，升级重拷零风险）。
"""
import argparse
import os
import sys


def bootstrap(autolive_home: str):
    home = os.path.abspath(autolive_home)
    if home not in sys.path:
        sys.path.insert(0, home)
    os.environ.setdefault("AUTOLIVE_HOME", home)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--autolive-home", default=os.environ.get(
        "AUTOLIVE_HOME",
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")),
    ))
    ap.add_argument("--rvc-root", default=os.environ.get("RVC_ROOT"))
    ap.add_argument("--http-port", default=os.environ.get("RVC_VC_HTTP_PORT"))
    args = ap.parse_args()
    bootstrap(args.autolive_home)
    if args.rvc_root:
        os.environ["RVC_ROOT"] = args.rvc_root
    if args.http_port:
        os.environ["RVC_VC_HTTP_PORT"] = args.http_port

    # 按 file 路径直载主实现，绕过 utils/__init__.py 巨石导入链
    # （RVC py3.9 无 loguru 等 AUTOlive 依赖；主实现自包含零包依赖——TODOS 惰性化收口前的桥接）
    import importlib.util
    _svc = os.path.join(args.autolive_home, "utils", "rvc_realtime_service.py")
    _spec = importlib.util.spec_from_file_location("rvc_realtime_service", _svc)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _mod.main()
