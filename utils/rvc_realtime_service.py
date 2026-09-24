# -*- coding: UTF-8 -*-
"""
RVC 包裹式实时变声服务（P2-1 主实现，运行于 RVC 自带 runtime 子进程）

架构（E-3）：本模块为 AUTOlive 树内主实现；apps/rvc/rvc_adapter.py 是薄入口
（RVC runtime python 经 PYTHONPATH 反向加载本模块）。

管线来源：audio_callback/SOLA 交叉淡化/rms 混音/阈值门控逐行移植自
gui_v1.py（版本锁定 RVC20240604Nvidia，上游 MIT）。复用 tools/rvc_for_realtime.py
的 RVC 推理类（版本锁定，不维护 fork——CEO 关键裁决 2）。

热切换契约（R1/B-1/T-1，上游 start_vc 模式 + 事务语义强化）：
- 切换 = 构建 new RVC(last_rvc=旧实例)——hubert/rmvpe/fcpe 底模共享，
  只加载新 net_g（显存峰值=新旧 net_g 并存，百 MB 级）
- 事务语义：新实例构建成功 → 原子赋值替换（音频流零间隙，旧→新无缝）；
  失败 → 释放半初始化态，旧模型继续服务
- 延迟目标 ≤500ms（T-1 口径：block_time 0.25s + 推理 + 交叉淡化）

控制面：HTTP 127.0.0.1:{http_port}（E-1 同款回环约束）
- GET  /status  → {state, model, devices}
- GET  /devices → 输入/输出设备清单（P2-2 前端下拉）
- POST /switch  → {pth_path, index_path, index_rate}（事务热切换）
- POST /start   → {input_device, output_device} 启动音频流
- POST /stop    → 停止音频流
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch


class RealtimeVC:
    """实时变声核心：RVC 推理 + sounddevice 音频流 + 事务热切换。"""

    def __init__(self, rvc_root: str):
        self.rvc_root = rvc_root
        sys.path.insert(0, rvc_root)
        os.chdir(rvc_root)  # RVC 内部以相对路径读 assets/hubert 等

        from configs.config import Config as RVCConfig
        import tools.rvc_for_realtime as rvc_for_realtime  # noqa: F401 版本锁定

        self._rvc_module = rvc_for_realtime
        self.config = RVCConfig()
        self.rvc = None  # 当前 RVC 实例（None=未加载模型）
        self.current_model = None  # {"pth","index","index_rate"}
        self.stream = None
        self.function = "vc"
        # RVC 构造器要求存引用（仅 harvest f0 路径使用；rmvpe 默认不触）
        self.inp_q = None
        self.opt_q = None
        self.switch_lock = threading.Lock()

        # 管线参数（gui_v1 默认值；block_time 取 0.25s 服务 ≤500ms 延迟目标）
        self.params = {
            "block_time": 0.25,
            "crossfade_time": 0.04,
            "extra_time": 0.5,
            "threhold": -60,
            "rms_mix_rate": 0.25,
            "index_rate": 0.75,
            "n_cpu": 4,
            "f0method": "rmvpe",
            "pitch": 0,
            "sr_type": "sr_model",
            "input_device": None,
            "output_device": None,
        }

    # ---------- 模型加载 / 事务热切换（B-1/R1）----------

    def switch_model(self, pth_path: str, index_path: str, index_rate: float = None):
        """事务热切换：新 RVC 实例构建成功才替换；失败旧模型继续服务。

        峰值显存 = 旧 net_g + 新 net_g（百 MB 级，底模共享不重复加载，B-1）。
        """
        with self.switch_lock:
            if not os.path.isfile(pth_path):
                raise FileNotFoundError(f"模型文件不存在: {pth_path}")
            if index_path and not os.path.isfile(index_path):
                raise FileNotFoundError(f"索引文件不存在: {index_path}")
            if index_rate is not None:
                self.params["index_rate"] = float(index_rate)
            # 索引路径与模型名前缀配对缺省（P1-4 布局约定）
            import faiss  # 提前暴露缺失依赖

            torch.cuda.empty_cache()  # 上游 start_vc 模式：切换前清缓存
            new_rvc = self._rvc_module.RVC(
                self.params["pitch"],
                # 注：本副本 RVC 类签名无 formant（gui_v1 调用与文件不同步，按实文件调用）
                pth_path,
                index_path,
                self.params["index_rate"],
                self.params["n_cpu"],
                self.inp_q,
                self.opt_q,
                self.config,
                self.rvc if self.rvc is not None else None,  # 底模共享
            )
            # 构建成功 → 原子替换（音频流零间隙；旧 net_g 由 GC 释放）
            old = self.rvc
            self.rvc = new_rvc
            self.current_model = {
                "pth": pth_path,
                "index": index_path,
                "index_rate": self.params["index_rate"],
            }
            del old
            torch.cuda.empty_cache()
            return self.current_model

    # ---------- 音频流（移植 gui_v1 start_vc + audio_callback）----------

    def _init_pipeline(self):
        """移植 gui_v1 start_vc 的缓冲区/窗口初始化。"""
        import librosa
        import torchaudio.transforms as tat

        g = self.params
        self.zc = self.rvc.tgt_sr // 100
        self.block_frame = (
            int(np.round(g["block_time"] * self.rvc.tgt_sr / self.zc)) * self.zc
        )
        self.block_frame_16k = 160 * self.block_frame // self.zc
        self.crossfade_frame = (
            int(np.round(g["crossfade_time"] * self.rvc.tgt_sr / self.zc)) * self.zc
        )
        self.sola_buffer_frame = min(self.crossfade_frame, 4 * self.zc)
        self.sola_search_frame = self.zc
        self.extra_frame = (
            int(np.round(g["extra_time"] * self.rvc.tgt_sr / self.zc)) * self.zc
        )
        self.input_wav: torch.Tensor = torch.zeros(
            self.extra_frame + self.crossfade_frame + self.sola_search_frame
            + self.block_frame,
            device=self.config.device, dtype=torch.float32,
        )
        self.input_wav_res: torch.Tensor = torch.zeros(
            160 * self.input_wav.shape[0] // self.zc,
            device=self.config.device, dtype=torch.float32,
        )
        self.rms_buffer: np.ndarray = np.zeros(4 * self.zc, dtype="float32")
        self.sola_buffer: torch.Tensor = torch.zeros(
            self.sola_buffer_frame, device=self.config.device, dtype=torch.float32
        )
        self.output_buffer: torch.Tensor = self.input_wav.clone()
        self.skip_head = self.extra_frame // self.zc
        self.return_length = (
            self.block_frame + self.sola_buffer_frame + self.sola_search_frame
        ) // self.zc
        self.fade_in_window: torch.Tensor = (
            torch.sin(
                0.5 * np.pi
                * torch.linspace(0.0, 1.0, steps=self.sola_buffer_frame,
                                 device=self.config.device, dtype=torch.float32)
            ) ** 2
        )
        self.fade_out_window: torch.Tensor = 1 - self.fade_in_window
        self.resampler = tat.Resample(
            self.rvc.tgt_sr, 16000, dtype=torch.float32
        ).to(self.config.device)

    def audio_callback(self, indata, outdata, frames, times, status):
        """移植 gui_v1.audio_callback 主路径（SOLA 交叉淡化 + rms 混音；
        噪声减免 I/O 可选关闭路径未移植——默认关，gui_v1 同为可选）。"""
        import librosa
        import torch.nn.functional as F

        try:
            start_time = time.perf_counter()
            indata = librosa.to_mono(indata.T)
            # 阈值门控（gui_v1 同款）
            if self.params["threhold"] > -60:
                indata = np.append(self.rms_buffer, indata)
                rms = librosa.feature.rms(
                    y=indata, frame_length=4 * self.zc, hop_length=self.zc
                )[:, 2:]
                self.rms_buffer[:] = indata[-4 * self.zc:]
                indata = indata[2 * self.zc - self.zc // 2:]
                db = librosa.amplitude_to_db(rms, ref=1.0)[0] < self.params["threhold"]
                for i in range(db.shape[0]):
                    if db[i]:
                        indata[i * self.zc:(i + 1) * self.zc] = 0
                indata = indata[self.zc // 2:]
            # 滑动窗口推进
            self.input_wav[: -self.block_frame] = self.input_wav[
                self.block_frame:].clone()
            self.input_wav[-indata.shape[0]:] = torch.from_numpy(indata).to(
                self.config.device)
            self.input_wav_res[: -self.block_frame_16k] = self.input_wav_res[
                self.block_frame_16k:].clone()
            self.input_wav_res[-160 * (indata.shape[0] // self.zc + 1):] = (
                self.resampler(self.input_wav[-indata.shape[0] - 2 * self.zc:])[160:]
            )
            # 推理
            infer_wav = self.rvc.infer(
                self.input_wav_res,
                self.block_frame_16k,
                self.skip_head,
                self.return_length,
                self.params["f0method"],
            )
            # rms 音量包络混合（gui_v1 上游：幂律混合）
            if self.params["rms_mix_rate"] < 1:
                input_wav = self.input_wav[self.extra_frame:]
                rms1 = librosa.feature.rms(
                    y=input_wav[: infer_wav.shape[0]].cpu().numpy(),
                    frame_length=4 * self.zc, hop_length=self.zc,
                )
                rms1 = torch.from_numpy(rms1).to(self.config.device)
                rms1 = F.interpolate(
                    rms1.unsqueeze(0),
                    size=infer_wav.shape[0] + 1, mode="linear", align_corners=True,
                )[0, 0, :-1]
                rms2 = librosa.feature.rms(
                    y=infer_wav[:].cpu().numpy(),
                    frame_length=4 * self.zc, hop_length=self.zc,
                )
                rms2 = torch.from_numpy(rms2).to(self.config.device)
                rms2 = F.interpolate(
                    rms2.unsqueeze(0),
                    size=infer_wav.shape[0] + 1, mode="linear", align_corners=True,
                )[0, 0, :-1]
                rms2 = torch.max(rms2, torch.zeros_like(rms2) + 1e-3)
                infer_wav *= torch.pow(
                    rms1 / rms2,
                    torch.tensor(1 - self.params["rms_mix_rate"]),
                )
            # SOLA 算法（DDSP-SVC 上游：归一化互相关）
            conv_input = infer_wav[
                None, None, : self.sola_buffer_frame + self.sola_search_frame
            ]
            cor_nom = F.conv1d(conv_input, self.sola_buffer[None, None, :])
            cor_den = torch.sqrt(
                F.conv1d(
                    conv_input ** 2,
                    torch.ones(1, 1, self.sola_buffer_frame,
                               device=self.config.device),
                ) + 1e-8
            )
            sola_offset = torch.argmax(cor_nom[0, 0] / cor_den[0, 0])
            infer_wav = infer_wav[sola_offset:]
            infer_wav[: self.sola_buffer_frame] *= self.fade_in_window
            infer_wav[: self.sola_buffer_frame] += (
                self.sola_buffer * self.fade_out_window
            )
            self.sola_buffer[:] = infer_wav[
                self.block_frame: self.block_frame + self.sola_buffer_frame
            ]
            outdata[:] = (
                infer_wav[: self.block_frame].repeat(2, 1).t().cpu().numpy()
            )
            # 延迟可观测（T-1 口径的数据源）
            infer_time = time.perf_counter() - start_time
            self.last_infer_time = infer_time
        except Exception:
            self.last_error = "音频回调异常"
            import traceback
            traceback.print_exc()

    # ---------- 流控制 ----------

    def start_stream(self, input_device=None, output_device=None):
        import sounddevice as sd

        if self.rvc is None:
            raise RuntimeError("未加载模型：先 /switch 再 /start")
        if self.stream is not None:
            return {"state": "already_running"}
        self._init_pipeline()
        self.inp_q = None
        self.opt_q = None
        # RVC 类构造需要队列参数（gui_v1 传入 mp 队列）——占位 None 兼容
        self.rvc.inp_q = self.inp_q
        self.rvc.opt_q = self.opt_q
        sd.default.device = [
            input_device if input_device is not None else sd.default.device[0],
            output_device if output_device is not None else sd.default.device[1],
        ]
        self.stream = sd.Stream(
            callback=self.audio_callback,
            blocksize=self.block_frame,
            samplerate=self.rvc.tgt_sr,
            dtype="float32",
        )
        self.stream.start()
        return {"state": "running", "samplerate": self.rvc.tgt_sr,
                "block_frame": self.block_frame}

    def stop_stream(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return {"state": "stopped"}

    def status(self):
        import sounddevice as sd
        return {
            "state": "running" if self.stream is not None else "idle",
            "model": self.current_model,
            "last_infer_time_s": round(getattr(self, "last_infer_time", 0), 3),
            "params": self.params,
            "has_cuda": torch.cuda.is_available(),
        }

    def devices(self):
        import sounddevice as sd
        return {"hosts": sd.query_hostapis(),
                "devices": sd.query_devices()}


class ControlServer:
    """HTTP 控制面（127.0.0.1 回环，E-1）。"""

    def __init__(self, vc: RealtimeVC, http_port: int):
        self.vc = vc

        class Handler(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/status":
                    self._json(200, vc.status())
                elif self.path == "/devices":
                    self._json(200, vc.devices())
                else:
                    self._json(404, {"problem": "未知路径", "cause": self.path,
                                     "fix": "GET /status 或 /devices"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                try:
                    if self.path == "/switch":
                        result = vc.switch_model(
                            body["pth_path"], body.get("index_path"),
                            body.get("index_rate"),
                        )
                        self._json(200, {"ok": True, "model": result})
                    elif self.path == "/start":
                        self._json(200, {"ok": True, **vc.start_stream(
                            body.get("input_device"), body.get("output_device"))})
                    elif self.path == "/stop":
                        self._json(200, {"ok": True, **vc.stop_stream()})
                    else:
                        self._json(404, {"problem": "未知路径", "cause": self.path,
                                         "fix": "POST /switch /start /stop"})
                except Exception as e:
                    # 三要素错误（DX-2）：事务失败时旧模型继续服务
                    self._json(500, {"problem": str(e), "cause": type(e).__name__,
                                     "fix": "检查模型/索引路径；切换失败时旧模型继续服务"})

            def log_message(self, fmt, *args):
                pass  # 静默访问日志

        self.httpd = ThreadingHTTPServer(("127.0.0.1", http_port), Handler)

    def serve_forever(self):
        self.httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="RVC 实时变声服务（AUTOlive P2-1）")
    ap.add_argument("--rvc-root", default=os.environ.get(
        "RVC_ROOT", r"D:\AI\RVC20240604Nvidia"))
    ap.add_argument("--http-port", type=int,
                    default=int(os.environ.get("RVC_VC_HTTP_PORT", 9391)))
    # parse_known_args：adapter 已消费 --autolive-home 等自有参数，
    # 本 parser 只取自己关心的键（双层 argparse 桥接）
    args, _unknown = ap.parse_known_args()
    # RVC 的 configs/config.py 在 import 时会解析 sys.argv（--port/--dml 等），
    # 清空避免撞我们的参数（本服务的绑定/端口全部自管，不经 RVC Config）
    sys.argv = [sys.argv[0]]

    vc = RealtimeVC(args.rvc_root)
    print(f"[rvc-vc] 控制面 http://127.0.0.1:{args.http_port} 就绪", flush=True)
    ControlServer(vc, args.http_port).serve_forever()


if __name__ == "__main__":
    main()
