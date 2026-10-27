# -*- coding: UTF-8 -*-
"""
桌面字幕显示窗口（独立子进程脚本，移植自 D:\\AI\\captions_printer\\desktop_caption_window.py）

整合修订（对齐内置字幕打印机 captions_manager 的显示契约）：
- 连接目标改为主系统内置 socket.io（http://<api_ip>:<api_port>，
  socketio_path=SOCKETIO_PATH 即 /captions_ws/socket.io——engine.io 客户端
  拼 URL 丢弃 connect url 内路径，必须经 socketio_path 提供挂载前缀；
  源项目硬编码 localhost:5502 已废弃）；
- 配置来源：连接建立时服务端推送 config_update（web_captions_printer 节全量，
  含 enable），本地直接读 config.json 同键作初始兜底（不依赖源项目 /get_config）；
- 显示时序：服务端节流线程已完成 start_delay/keep_time 节流，客户端收到即显示、
  keep_time 后清除（源项目客户端二次排队+二次延时为双重节拍，不再移植）；
- 断线自动重连（python-socketio 内建 reconnection）；状态栏仅承载未就绪/异常态
  （连接中/断开/未开启），连接成功自动隐藏——状态字不得被 OBS 窗口捕捉拍到
  （2026-10-26 批次：移除常驻「已连接」状态显示）。

启动方式：主系统 web_server 按配置自动 spawn（web_captions_printer.enable &&
desktop_window），或字幕打印页「打开桌面字幕窗口」按钮经
/captions_desktop_window/open 打开。
"""

import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

try:
    import socketio
except ImportError:  # python-socketio 缺失时窗口仍可打开，状态栏明示
    socketio = None

# config.json 位于项目根（本脚本在 <root>/utils/web_captions/ 下）
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config.json")

FALLBACK_WINDOW_BG = "#219175"  # 源项目绿幕底色（利于 OBS 色键抠像）

# engine.io 客户端拼 URL 只用 netloc + socketio_path（丢弃 connect url 内的路径，
# 见 engineio/base_client.py _get_engineio_url），因此必须传含挂载前缀的完整路径
SOCKETIO_PATH = "/captions_ws/socket.io"

DEFAULT_CONFIG = {
    "enable": False,
    "subtitle_font_family": "Microsoft YaHei",
    "subtitle_font_size": 24,
    "subtitle_font_weight": "bold",
    "font_color": "#FFFFFF",
    "bg_color": "rgba(0,0,0,0.6)",
    "body_bg_color": "none",
    "subtitle_bg_width": 400,
    "subtitle_bg_height": 60,
    "show_over_hide_time": 2000,
}


def build_socket_url(api_ip, api_port):
    """主系统服务地址（0.0.0.0 回显转 127.0.0.1；路径由 SOCKETIO_PATH 提供）"""
    ip = str(api_ip or "127.0.0.1")
    if ip == "0.0.0.0":
        ip = "127.0.0.1"
    return f"http://{ip}:{int(api_port or 8082)}"


def _load_config_file(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_main_api(config_path=CONFIG_PATH):
    """主系统内部 API 地址（api_ip/api_port 顶层键）"""
    data = _load_config_file(config_path)
    try:
        port = int(data.get("api_port") or 8082)
    except Exception:
        port = 8082
    return str(data.get("api_ip") or "127.0.0.1"), port


def load_window_config(config_path=CONFIG_PATH):
    """web_captions_printer 节（读失败逐键回退默认）"""
    cfg = dict(DEFAULT_CONFIG)
    section = _load_config_file(config_path).get("web_captions_printer") or {}
    cfg.update({k: v for k, v in section.items() if v is not None})
    return cfg


def _px_int(value, default):
    """'600px'/'600'/600 → int（web 页样式键带 px 单位）"""
    try:
        return int(float(str(value).replace("px", "").strip()))
    except Exception:
        return default


def tk_color(value, fallback):
    """web 颜色值 → tk 可用颜色（rgba(...) 取 rgb 部分；none/空 → fallback）"""
    v = str(value or "").strip()
    if v == "" or v.lower() in ("none", "transparent"):
        return fallback
    if v.lower().startswith("rgba"):
        inner = v[v.find("(") + 1:v.rfind(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 3:
            try:
                return "#{:02x}{:02x}{:02x}".format(
                    int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
            except Exception:
                return fallback
        return fallback
    return v


def tk_weight(value):
    """css font-weight → tk 字重（bold/数值≥600 → bold）"""
    v = str(value or "").strip().lower()
    if v == "bold":
        return "bold"
    try:
        return "bold" if float(v) >= 600 else "normal"
    except Exception:
        return "normal"


class CaptionPresenter:
    """字幕显示时序（web 页 show1 语义简化版：立即显示，keep_time 后清除）

    服务端节流线程已消费 start_delay，客户端不再延时；调度器注入
    （tk root.after/after_cancel 或测试桩），逻辑与 UI 解耦可独立测试。
    """

    def __init__(self, show_cb, clear_cb, scheduler, default_keep_ms=4000):
        self._show = show_cb
        self._clear = clear_cb
        self._scheduler = scheduler
        self._default_keep_ms = default_keep_ms
        self._hide_token = None

    def present(self, data):
        content = str((data or {}).get("content", "") or "")
        if content == "":
            return
        try:
            keep_ms = int(float((data or {}).get("keep_time") or 0))
        except Exception:
            keep_ms = 0
        if keep_ms <= 0:
            keep_ms = self._default_keep_ms
        self.cancel()
        self._show(content)
        self._hide_token = self._scheduler.after(keep_ms, self._clear)

    def cancel(self):
        if self._hide_token is not None:
            try:
                self._scheduler.after_cancel(self._hide_token)
            except Exception:
                pass
            self._hide_token = None


class DesktopCaptionWindow:
    """tkinter 桌面字幕窗口：置顶/透明度/拖拽/控制面板（ESC）/帮助（F1）"""

    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = config_path
        self.config = load_window_config(config_path)
        self._hide_token = None
        self._closing = False  # 连接线程退出旗标（close_window 置位）

        self.root = tk.Tk()
        self.root.title("字幕显示窗口 - AUTOlive")
        self.root.geometry("600x200+100+100")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg=FALLBACK_WINDOW_BG)
        self.root.bind("<Button-3>", self._show_context_menu)
        self.root.bind("<Escape>", lambda e: self._toggle_controls())
        self.root.bind("<F1>", lambda e: self._show_help())

        self.main_frame = tk.Frame(self.root, bg=FALLBACK_WINDOW_BG)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.caption_label = tk.Label(
            self.main_frame, text="字幕显示区域\n等待字幕推送…",
            font=("Microsoft YaHei", 24, "bold"), fg="#FFFFFF",
            bg="#000000", wraplength=580, justify=tk.CENTER, bd=0)
        self.caption_label.pack(fill=tk.BOTH, expand=True)
        self.caption_label.bind("<Button-1>", self._start_drag)
        self.caption_label.bind("<B1-Motion>", self._on_drag)
        self.caption_label.bind("<ButtonRelease-1>", self._stop_drag)
        self.caption_label.bind("<Button-3>", self._show_context_menu)

        self.status_label = tk.Label(self.root, text="连接中…", fg="orange",
                                     bg=FALLBACK_WINDOW_BG, font=("Microsoft YaHei", 9))
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # 控制面板（默认隐藏，ESC/右键呼出）
        self.control_frame = tk.Frame(self.root, bg="#333333", relief=tk.RAISED, bd=2)
        self._build_control_panel()

        self.presenter = CaptionPresenter(self._show_text, self._clear_text, self.root)
        self._drag_start = None
        self._apply_config(self.config)
        self._setup_socket()

    # ---------- 配置应用 ----------
    def _apply_config(self, cfg):
        if not cfg:
            return
        self.config = dict(self.config)
        self.config.update({k: v for k, v in cfg.items() if v is not None})
        cfg = self.config

        window_bg = tk_color(cfg.get("body_bg_color"), FALLBACK_WINDOW_BG)
        self.root.configure(bg=window_bg)
        self.main_frame.configure(bg=window_bg)
        self.status_label.configure(bg=window_bg)

        self.caption_label.configure(
            font=(str(cfg.get("subtitle_font_family") or "Microsoft YaHei"),
                  max(8, _px_int(cfg.get("subtitle_font_size"), 24)),
                  tk_weight(cfg.get("subtitle_font_weight"))),
            fg=tk_color(cfg.get("font_color"), "#FFFFFF"),
            bg=tk_color(cfg.get("bg_color"), "#000000"))

        width = max(200, _px_int(cfg.get("subtitle_bg_width"), 400))
        height = max(60, _px_int(cfg.get("subtitle_bg_height"), 60))
        geo = self.root.geometry()
        pos = ""
        if "+" in geo:
            pos = geo[geo.index("+"):]
        self.root.geometry(f"{width}x{height}{pos or '+100+100'}")

        if not cfg.get("enable", False):
            self._set_status("字幕功能未开启（AUTOlive 字幕打印开关未打开）", "orange")

    # ---------- socket.io（主系统 /captions_ws） ----------
    def _setup_socket(self):
        if socketio is None:
            self._set_status("python-socketio 未安装，无法连接字幕服务", "red")
            return
        api_ip, api_port = load_main_api(self.config_path)
        url = build_socket_url(api_ip, api_port)
        self._client = socketio.Client(reconnection=True, reconnection_attempts=0,
                                       reconnection_delay=3)

        @self._client.event
        def connect():
            self._root_after(0, self._hide_status)

        @self._client.event
        def disconnect():
            self._root_after(0, lambda: self._set_status("连接断开（自动重连中）", "red"))

        @self._client.on("message")
        def on_message(data):
            self._root_after(0, lambda: self.presenter.present(data))

        @self._client.on("config_update")
        def on_config(data):
            self._root_after(0, lambda: self._apply_config(data))

        def _connect_job():
            # 首连失败必须重试：python-socketio 的 reconnection 只接管"连上后断线"，
            # 首次 connect 异常不会自动重试（2026-10-25 实测根因之一）。
            # 连接成功后 wait() 阻塞至断开，循环重建（服务重启场景自恢复）。
            while not self._closing:
                try:
                    self._client.connect(url, socketio_path=SOCKETIO_PATH,
                                         wait_timeout=5)
                    logging.info("字幕窗口已连接字幕服务")
                    self._root_after(0, self._hide_status)
                    self._client.wait()
                except Exception as e:
                    logging.error("字幕窗口连接失败（3 秒后重试）: %s", e)
                    self._root_after(0, lambda: self._set_status(
                        "连接失败（自动重试中）", "red"))
                    if self._closing:
                        break
                    time.sleep(3)

        threading.Thread(target=_connect_job, daemon=True, name="captions-ws").start()

    # ---------- UI 辅助 ----------
    def _root_after(self, ms, fn):
        try:
            self.root.after(ms, fn)
        except Exception:
            pass

    def _set_status(self, text, color):
        """状态栏仅承载未就绪/异常态（重新可见并更新文本/颜色）"""
        self.status_label.configure(text=text, fg=color)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def _hide_status(self):
        """连接成功后隐藏状态栏（状态字不得被 OBS 窗口捕捉拍到）"""
        self.status_label.pack_forget()

    def _show_text(self, text):
        self.caption_label.configure(text=text)

    def _clear_text(self):
        self.caption_label.configure(text="")

    def _start_drag(self, event):
        self._drag_start = (event.x_root, event.y_root)

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        x = self.root.winfo_x() + (event.x_root - self._drag_start[0])
        y = self.root.winfo_y() + (event.y_root - self._drag_start[1])
        self.root.geometry(f"+{x}+{y}")
        self._drag_start = (event.x_root, event.y_root)

    def _stop_drag(self, event):
        self._drag_start = None

    def _show_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="显示控制面板", command=self._toggle_controls)
        menu.add_separator()
        menu.add_command(label="关闭窗口", command=self.close_window)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _build_control_panel(self):
        row = tk.Frame(self.control_frame, bg="#333333")
        row.pack(fill=tk.X, padx=10, pady=6)
        tk.Label(row, text="透明度:", fg="white", bg="#333333").pack(side=tk.LEFT)
        self.alpha_var = tk.DoubleVar(value=0.9)
        tk.Scale(row, from_=0.3, to=1.0, resolution=0.1, orient=tk.HORIZONTAL,
                 variable=self.alpha_var, command=self._update_alpha, bg="#333333",
                 fg="white", highlightthickness=0).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        row2 = tk.Frame(self.control_frame, bg="#333333")
        row2.pack(fill=tk.X, padx=10, pady=2)
        self.topmost_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="窗口置顶", variable=self.topmost_var,
                       command=self._toggle_topmost, fg="white", bg="#333333",
                       selectcolor="#555555").pack(side=tk.LEFT)
        tk.Button(row2, text="关闭窗口", command=self.close_window, bg="#cc4444",
                  fg="white", relief=tk.FLAT).pack(side=tk.RIGHT)
        tk.Button(row2, text="隐藏面板", command=self.control_frame.place_forget,
                  bg="#555555", fg="white", relief=tk.FLAT).pack(side=tk.RIGHT)

    def _toggle_controls(self):
        if self.control_frame.winfo_viewable():
            self.control_frame.place_forget()
        else:
            x = self.root.winfo_x() + self.root.winfo_width() + 10
            self.control_frame.place(x=x, y=self.root.winfo_y())
            self.control_frame.lift()

    def _update_alpha(self, value):
        try:
            self.root.attributes("-alpha", float(value))
        except Exception:
            pass

    def _toggle_topmost(self):
        self.root.attributes("-topmost", self.topmost_var.get())

    def _show_help(self):
        messagebox.showinfo(
            "帮助信息",
            "字幕显示窗口 - AUTOlive 字幕打印\n\n"
            "快捷键：ESC 显示/隐藏控制面板，F1 显示本帮助\n"
            "操作：字幕区域拖拽移动窗口，右键呼出菜单\n\n"
            "窗口内容与字幕页（/captions）实时同步，\n"
            "可直接被直播工具（OBS 等）窗口捕捉。")

    def close_window(self):
        self._closing = True
        try:
            if getattr(self, "_client", None) is not None and self._client.connected:
                self._client.disconnect()
        except Exception:
            pass
        self.root.quit()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    # argv[1] 可选：自定义 config.json 路径（测试/多实例场景；缺省读主系统配置）
    config_path = sys.argv[1] if len(sys.argv) > 1 else CONFIG_PATH
    try:
        DesktopCaptionWindow(config_path=config_path).run()
    except Exception as e:
        logging.exception("程序运行异常: %s", e)
        try:
            messagebox.showerror("错误", f"程序运行异常: {e}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
