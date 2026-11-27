# -*- coding: UTF-8 -*-
"""AUTOlive WebUI 入口。

源码模式：runtime312\\python.exe webui.py（开发链 start_autolive.bat，不动）。
frozen 模式：Nuitka standalone 的 AUTOlive.exe——main() 前执行引导块
（cwd 归位 / NiceGUI 存储 / PATH 前插 / 文件日志 / 端口预检 / 首启零配置）。
"""
import sys
import os
import shutil
import socket
from pathlib import Path

WEBUI_PORT = 8086


def _tcp_free(host: str, port: int) -> bool:
    """TCP bind 试探：端口可绑定视为空闲（TIME_WAIT 等亦放行）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _fatal_port(base: Path) -> None:
    """端口被占三段式报错：说明 + 处置 + 提示，弹窗后退出。"""
    msg = (
        f"AUTOlive 启动失败：端口 {WEBUI_PORT} 已被占用。\n\n"
        f"处置方法（任选其一）：\n"
        f"1. 关闭已运行的 AUTOlive 窗口后重试；\n"
        f"2. 在任务管理器结束占用端口的进程；\n"
        f"3. 检查是否已有另一个 AUTOlive 实例在运行。\n\n"
        f"诊断：以管理员运行  netstat -ano | findstr :{WEBUI_PORT}"
    )
    print(msg, file=sys.stderr)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "AUTOlive", 0x10)  # MB_ICONERROR
    except Exception:
        pass
    sys.exit(1)


def _ensure_junctions(base: Path) -> None:
    """首启幂等重建目录 junction（发行改造阶段 E 配套，2026-11-22）。

    zip 无法携带 junction——发行包不含链接，此处首启重建：
    - edtalk/ckpts → models/edtalk（EDTalk 脚本相对路径约定）
    - apps/gpt-sovits/GPT_SoVITS/pretrained_models → models/gpt_sovits
    - apps/gpt-sovits/runtime → runtime312（GSV 解释器，runtime312 已补齐
      GSV 全部依赖；注册中心另有 runtime312 终级回退，双保险）
    失败仅告警不阻断（注册中心绝对路径解析为第二保障）。"""
    import _winapi

    links = [
        (base / "edtalk" / "ckpts", base / "models" / "edtalk"),
        (base / "apps" / "gpt-sovits" / "GPT_SoVITS" / "pretrained_models",
         base / "models" / "gpt_sovits"),
        (base / "apps" / "gpt-sovits" / "runtime", base / "runtime312"),
    ]
    for link, target in links:
        if link.exists() or link.is_symlink():
            continue  # 已存在（junction/真实目录）→ 幂等跳过
        if not target.exists():
            print(f"[bootstrap] junction 目标缺失，跳过: {target}", file=sys.stderr)
            continue
        try:
            _winapi.CreateJunction(str(target), str(link))
            print(f"[bootstrap] 已重建 junction: {link.name} -> {target.name}")
        except OSError as e:
            print(f"[bootstrap] junction 创建失败（不阻断）: {link} - {e}",
                  file=sys.stderr)


def _resolve_webui_port(cfg_path: Path) -> int:
    """轻解析 config.json 的 webui.port（2026-11-27 审查修复 #10）：json 直读，
    不走 Settings 完整校验链（bootstrap 期依赖未就绪）；异常/越界 fallback
    8086。示例配置 port 为字符串（"8086"），str→int 容错。"""
    try:
        import json
        node = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        port = int(str((node.get("webui") or {}).get("port") or 8086))
        return port if 0 < port < 65536 else 8086
    except Exception:
        return 8086


def bootstrap_frozen():
    """frozen（Nuitka standalone）引导块——源码模式直接返回。

    全部动作在 import frontend.main 之前完成（避免 NiceGUI/前端初始化
    读到漂移的 cwd 或未就绪的 config）。"""
    if not ("__compiled__" in globals() or getattr(sys, "frozen", False)):
        return
    base = Path(sys.executable).parent.resolve()

    # 1) cwd 归位（快捷方式/双击启动的工作目录漂移）
    if Path.cwd() != base:
        os.chdir(base)

    # 2) NiceGUI 存储目录（ui storage 显式锚定，防用户目录漂移）
    os.environ.setdefault("NICEGUI_STORAGE_PATH", str(base / ".nicegui"))

    # 3) PATH 前插 runtime312 子目录（对齐 start_autolive.bat 21-22 行）
    rt = base / "runtime312"
    for sub in ("", "Scripts", "Library\\bin", "ffmpeg\\bin"):
        p = rt / sub
        if p.is_dir():
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")

    # 4) 文件日志 sink（双击启动崩溃可见；源码链原有控制台日志不受影响）
    log_dir = base / "log"
    log_dir.mkdir(exist_ok=True)
    try:
        from loguru import logger as _loguru
        _loguru.add(str(log_dir / "webui.log"),
                    rotation="10 MB", retention=10, encoding="utf-8",
                    enqueue=True, backtrace=True, diagnose=False)
    except Exception:
        pass

    # 5) 首启零配置：无 config.json 时以 example 复制
    cfg, example = base / "config.json", base / "config.example.json"
    if not cfg.exists() and example.exists():
        shutil.copyfile(example, cfg)

    # 6) 端口预检（2026-11-27 审查修复 #10）：读 config.json 的 webui.port
    #    再预检——原硬编码 8086 与用户改配置后的实际监听端口脱节（预检白过/
    #    误报）。置于零配置复制之后，首启也能读到 example 携带的端口。
    global WEBUI_PORT
    WEBUI_PORT = _resolve_webui_port(cfg)
    if not _tcp_free("127.0.0.1", WEBUI_PORT):
        _fatal_port(base)

    # 7) 首启 junction 重建（zip 不携带链接，见 _ensure_junctions）
    _ensure_junctions(base)

    # 8) NiceGUI script mode 重执行入口重定向（2026-11-23 T1 冒烟修复）：
    #    ui.run 未传 root 时 NiceGUI 每次页面连接 runpy.run_path(sys.argv[0])
    #    重建全局 UI——frozen 下 sys.argv[0]=exe（二进制）必炸 null bytes。
    #    dist 内附带无 bootstrap 的纯入口源文件，重定向后与源码模式同构。
    entry = base / "autolive_entry.py"
    if entry.is_file():
        sys.argv[0] = str(entry)


bootstrap_frozen()

# 确保项目根目录在 Python 路径中（源码模式；frozen 下包已内嵌）
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 导入并运行模块化应用
from frontend.main import AutoliveApp
from utils.my_log import logger

def main():
    """主函数"""
    try:
        app = AutoliveApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
