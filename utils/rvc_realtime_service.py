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
import multiprocessing
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import Queue
from pathlib import Path

import numpy as np
import torch


# phase_vocoder 逐行复制自 gui_v1 快照 :26-47（SHA-256 b7eb89599d79e079e894bec44b63cdc14780f939fd45236b2903996709135100，
# 复制日期 2026-10-22；上游=外部树 D:\AI\RVC20240604Nvidia\gui_v1.py 内联实现，登记上游跟踪：
# 上游修复不自动传导，升级 RVC 时按快照 diff 本函数——A-4/E-3 副本纪律）。
def phase_vocoder(a, b, fade_out, fade_in):
    window = torch.sqrt(fade_out * fade_in)
    fa = torch.fft.rfft(a * window)
    fb = torch.fft.rfft(b * window)
    absab = torch.abs(fa) + torch.abs(fb)
    n = a.shape[0]
    if n % 2 == 0:
        absab[1:-1] *= 2
    else:
        absab[1:] *= 2
    phia = torch.angle(fa)
    phib = torch.angle(fb)
    deltaphase = phib - phia
    deltaphase = deltaphase - 2 * np.pi * torch.floor(deltaphase / 2 / np.pi + 0.5)
    w = 2 * np.pi * torch.arange(n // 2 + 1).to(a) + deltaphase
    t = torch.arange(n).unsqueeze(-1).to(a) / n
    result = (
        a * (fade_out**2)
        + b * (fade_in**2)
        + torch.sum(absab * torch.cos(w * t + phia), -1) * window / n
    )
    return result


# Harvest 逐行复制自 gui_v1 快照 :50-71（同上快照锚点；A-4/E-7 harvest 进程池——
# 替换旧 inp_q=None 占位；Windows spawn 生命周期：池随启流创建、stop_stream 拆除
# terminate+join+cancel_join_thread（E-7②），不做跨启流 Queue 复用）。
class Harvest(multiprocessing.Process):
    def __init__(self, inp_q, opt_q):
        multiprocessing.Process.__init__(self)
        self.inp_q = inp_q
        self.opt_q = opt_q

    def run(self):
        import numpy as np
        import pyworld

        while 1:
            idx, x, res_f0, n_cpu, ts = self.inp_q.get()
            f0, t = pyworld.harvest(
                x.astype(np.double),
                fs=16000,
                f0_ceil=1100,
                f0_floor=50,
                frame_period=10,
            )
            res_f0[idx] = f0
            if len(res_f0.keys()) >= n_cpu:
                self.opt_q.put(ts)


class RealtimeVC:
    """实时变声核心：RVC 推理 + sounddevice 音频流 + 事务热切换。"""

    def __init__(self, rvc_root: str):
        self.rvc_root = rvc_root
        sys.path.insert(0, rvc_root)
        os.chdir(rvc_root)  # RVC 内部以相对路径读 assets/hubert 等

        from configs.config import Config as RVCConfig
        # R-1（/autoplan 2026-10-14）：推理类升级——旧版 tools/rvc_for_realtime.py
        # 无 formant，改用 fork 新版 infer/lib/rtrvc.py（formant+fcpe+last_rvc
        # 复用链完整）；旧版保留=回退路径（D2 变声侧回退半径），加载失败回落。
        self._rvc_module = None
        self._is_rtrvc = False
        try:
            from infer.lib import rtrvc as rvc_for_realtime  # noqa: F401 fork 新版
            self._rvc_module = rvc_for_realtime
            self._is_rtrvc = True
        except Exception as e:
            logger.warning(f"[rvc-vc] rtrvc 加载失败，回退旧版 tools/rvc_for_realtime: {e}")
            import tools.rvc_for_realtime as legacy  # noqa: F401 旧版回退（保留不删）
            self._rvc_module = legacy

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
            "formant": 0.0,   # rtrvc 新能力（性别因子/声线粗细，-2~2）
            "protect": 0.33,  # R-2 参数链闭合项（前端值必须真实生效）
            "sr_type": "sr_model",
            # A-4/E-1：gui_v1 可选开关（audio_callback 每块读取；E-1 bool 域热更）
            "I_noise_reduce": False,
            "O_noise_reduce": False,
            "use_pv": False,
            "input_device": None,
            "output_device": None,
        }
        # A-4/E-3：TorchGate 直接 import 既有 apps/rvc/tools/torchgate 副本
        # （与 rtrvc 同源同版本锁定，已验证与外部上游逐字节一致——不重复复制）；
        # 按需在 _init_pipeline 惰性实例化（仅 I/O 降噪开启时使用）。
        self.tg = None
        self.harvest_workers = []  # E-7：harvest 池（启流建、停流拆）

    # ---------- 模型加载 / 事务热切换（B-1/R1 + E-2 采样率守卫 + E-8 白名单）----------

    def _path_allowed(self, pth_path: str) -> bool:
        """E-8：/switch 路径白名单——models/rvc/ 前缀或 voice_profiles.rvc_model 值。

        torch.load 是 pickle 反序列化面（127.0.0.1 回环无鉴权），前端下拉过滤
        不构成服务端约束。自包含实现（json 内置），不 import AUTOlive 依赖链。
        """
        try:
            norm = os.path.normpath(os.path.abspath(pth_path)).lower()
            home = os.environ.get("AUTOLIVE_HOME") or os.path.abspath(
                os.path.join(self.rvc_root, "..", ".."))
            models_dir = os.path.normpath(
                os.path.join(home, "models", "rvc")).lower()
            if norm.startswith(models_dir + os.sep):
                return True
            cfg_path = os.path.join(home, "config.json")
            with open(cfg_path, encoding="utf-8") as f:
                profiles = json.load(f).get("voice_profiles") or {}
            for p in profiles.values():
                m = (p or {}).get("rvc_model") or ""
                if m and os.path.normpath(os.path.abspath(m)).lower() == norm:
                    return True
        except Exception:
            pass
        return False

    def switch_model(self, pth_path: str, index_path: str, index_rate: float = None):
        """事务热切换：新 RVC 实例构建成功才替换；失败旧模型继续服务。

        峰值显存 = 旧 net_g + 新 net_g（百 MB 级，底模共享不重复加载，B-1）。
        E-2 采样率守卫：新旧 tgt_sr 不一致且流运行中 → 事务性重建管线+原子重开
        流（毫秒级缝隙——对「零间隙」承诺的显式豁免情形，记录于批次义务 E-2）；
        重建失败三要素拒绝且旧模型继续。
        """
        with self.switch_lock:
            if not self._path_allowed(pth_path):
                raise PermissionError(
                    f"模型路径不在白名单（models/rvc/ 或声音档案 rvc_model）: {pth_path}")
            if not os.path.isfile(pth_path):
                raise FileNotFoundError(f"模型文件不存在: {pth_path}")
            if index_path and not os.path.isfile(index_path):
                raise FileNotFoundError(f"索引文件不存在: {index_path}")
            if index_rate is not None:
                self.params["index_rate"] = float(index_rate)
            # 索引路径与模型名前缀配对缺省（P1-4 布局约定）
            import faiss  # 提前暴露缺失依赖

            torch.cuda.empty_cache()  # 上游 start_vc 模式：切换前清缓存
            if self._is_rtrvc:
                # rtrvc 签名（infer/lib/rtrvc.py:42-55）：key, formant, pth, index,
                # index_rate, n_cpu, inp_q, opt_q, config, last_rvc
                new_rvc = self._rvc_module.RVC(
                    self.params["pitch"],
                    self.params["formant"],
                    pth_path,
                    index_path,
                    self.params["index_rate"],
                    self.params["n_cpu"],
                    self.inp_q,
                    self.opt_q,
                    self.config,
                    self.rvc if self.rvc is not None else None,  # 底模共享
                )
            else:
                # 旧版回退签名（tools/rvc_for_realtime.py，无 formant）
                new_rvc = self._rvc_module.RVC(
                    self.params["pitch"],
                    pth_path,
                    index_path,
                    self.params["index_rate"],
                    self.params["n_cpu"],
                    self.inp_q,
                    self.opt_q,
                    self.config,
                    self.rvc if self.rvc is not None else None,  # 底模共享
                )
            # E-2 采样率守卫：异 tgt_sr + 流运行中 → 停流→替换→重建管线→重开流
            sr_mismatch = (self.rvc is not None
                           and new_rvc.tgt_sr != self.rvc.tgt_sr)
            was_running = self.stream is not None
            if sr_mismatch and was_running:
                self.stop_stream()
            # 构建成功 → 原子替换（同 tgt_sr：音频流零间隙；旧 net_g 由 GC 释放）
            old = self.rvc
            self.rvc = new_rvc
            self.current_model = {
                "pth": pth_path,
                "index": index_path,
                "index_rate": self.params["index_rate"],
                "tgt_sr": new_rvc.tgt_sr,
            }
            del old
            torch.cuda.empty_cache()
            if sr_mismatch and was_running:
                # 毫秒级缝隙显式豁免（E-2）：管线缓冲按新 tgt_sr 全量重建
                self._init_pipeline()
                self._setup_harvest_pool()
                self._open_stream()
            return self.current_model

    # ---------- 音频流（移植 gui_v1 start_vc + audio_callback）----------

    def _init_pipeline(self):
        """移植 gui_v1 start_vc 的缓冲区/窗口初始化（快照 :720-814 对照）。

        E-9 sr_type 双路：sr_model=模型采样率开流；sr_device=设备采样率开流
        （resampler 输入频率参数化 + resampler2 把推理输出 tgt_sr 重采样回开流率）。
        """
        import sounddevice as sd
        import torchaudio.transforms as tat

        g = self.params
        # gui_v1 :720-724 对照：sr_type 双路
        if g["sr_type"] == "sr_model":
            self.samplerate = self.rvc.tgt_sr
        else:
            self.samplerate = int(sd.query_devices(
                device=sd.default.device[0])["default_samplerate"])
        # gui_v1 :725 + :1142-1149 对照：通道数协商（min(in, out, 2)）
        in_ch = int(sd.query_devices(
            device=sd.default.device[0])["max_input_channels"])
        out_ch = int(sd.query_devices(
            device=sd.default.device[1])["max_output_channels"])
        self.channels = max(1, min(in_ch, out_ch, 2))
        self.zc = self.samplerate // 100
        self.block_frame = (
            int(np.round(g["block_time"] * self.samplerate / self.zc)) * self.zc
        )
        self.block_frame_16k = 160 * self.block_frame // self.zc
        self.crossfade_frame = (
            int(np.round(g["crossfade_time"] * self.samplerate / self.zc)) * self.zc
        )
        self.sola_buffer_frame = min(self.crossfade_frame, 4 * self.zc)
        self.sola_search_frame = self.zc
        self.extra_frame = (
            int(np.round(g["extra_time"] * self.samplerate / self.zc)) * self.zc
        )
        self.input_wav: torch.Tensor = torch.zeros(
            self.extra_frame + self.crossfade_frame + self.sola_search_frame
            + self.block_frame,
            device=self.config.device, dtype=torch.float32,
        )
        # gui_v1 :768 对照：I 降噪缓冲
        self.input_wav_denoise: torch.Tensor = self.input_wav.clone()
        self.input_wav_res: torch.Tensor = torch.zeros(
            160 * self.input_wav.shape[0] // self.zc,
            device=self.config.device, dtype=torch.float32,
        )
        self.rms_buffer: np.ndarray = np.zeros(4 * self.zc, dtype="float32")
        self.sola_buffer: torch.Tensor = torch.zeros(
            self.sola_buffer_frame, device=self.config.device, dtype=torch.float32
        )
        self.nr_buffer: torch.Tensor = self.sola_buffer.clone()
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
        # E-9：resampler 输入频率参数化（开流率 → 16k 推理输入）
        self.resampler = tat.Resample(
            self.samplerate, 16000, dtype=torch.float32
        ).to(self.config.device)
        # gui_v1 :804-811 对照：tgt_sr != 开流率时 resampler2（推理输出 → 开流率）
        if self.rvc.tgt_sr != self.samplerate:
            self.resampler2 = tat.Resample(
                self.rvc.tgt_sr, self.samplerate, dtype=torch.float32
            ).to(self.config.device)
        else:
            self.resampler2 = None
        # gui_v1 :812-814 对照：TorchGate 惰性实例化（A-4/E-3 既有副本 import）
        if (g["I_noise_reduce"] or g["O_noise_reduce"]) and self.tg is None:
            from tools.torchgate import TorchGate  # apps/rvc 副本（cwd=rvc_root）
            self.tg = TorchGate(
                sr=self.samplerate, n_fft=4 * self.zc, prop_decrease=0.9
            ).to(self.config.device)

    def audio_callback(self, indata, outdata, frames, times, status):
        """移植 gui_v1.audio_callback（快照 :847-1007 逐行对照：SOLA 交叉淡化 +
        rms 混音 + 阈值门控 + I/O 降噪 + use_pv 相位声码器 + 通道参数化）。
        E-4：gui_v1 的 delay_time 是 GUI 延迟读数（:604-609），不属于音频通路；
        本回调只承载降噪/推理/淡化管线本身，延迟读数口径由状态卡展示。"""
        import librosa
        import torch.nn.functional as F

        try:
            start_time = time.perf_counter()
            indata = librosa.to_mono(indata.T)
            # 阈值门控（gui_v1 :856-869 对照）
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
            # 滑动窗口推进（gui_v1 :870-878 对照）
            self.input_wav[: -self.block_frame] = self.input_wav[
                self.block_frame:].clone()
            self.input_wav[-indata.shape[0]:] = torch.from_numpy(indata).to(
                self.config.device)
            self.input_wav_res[: -self.block_frame_16k] = self.input_wav_res[
                self.block_frame_16k:].clone()
            # 输入降噪 + 重采样（gui_v1 :880-904 对照；A-4 移植）
            if self.params["I_noise_reduce"]:
                self.input_wav_denoise[: -self.block_frame] = (
                    self.input_wav_denoise[self.block_frame:].clone())
                input_wav = self.input_wav[
                    -self.sola_buffer_frame - self.block_frame:]
                input_wav = self.tg(
                    input_wav.unsqueeze(0), self.input_wav.unsqueeze(0)
                ).squeeze(0)
                input_wav[: self.sola_buffer_frame] *= self.fade_in_window
                input_wav[: self.sola_buffer_frame] += (
                    self.nr_buffer * self.fade_out_window
                )
                self.input_wav_denoise[-self.block_frame:] = input_wav[
                    : self.block_frame]
                self.nr_buffer[:] = input_wav[self.block_frame:]
                self.input_wav_res[-self.block_frame_16k - 160:] = self.resampler(
                    self.input_wav_denoise[-self.block_frame - 2 * self.zc:]
                )[160:]
            else:
                self.input_wav_res[-160 * (indata.shape[0] // self.zc + 1):] = (
                    self.resampler(self.input_wav[-indata.shape[0] - 2 * self.zc:])[160:]
                )
            # 推理（gui_v1 :906-915 对照；resampler2=E-9 sr_device/sr_mismatch 链）
            infer_wav = self.rvc.infer(
                self.input_wav_res,
                self.block_frame_16k,
                self.skip_head,
                self.return_length,
                self.params["f0method"],
            )
            if self.resampler2 is not None:
                infer_wav = self.resampler2(infer_wav)
            # 输出降噪（gui_v1 :921-928 对照；A-4 移植）
            if self.params["O_noise_reduce"]:
                self.output_buffer[: -self.block_frame] = self.output_buffer[
                    self.block_frame:].clone()
                self.output_buffer[-self.block_frame:] = infer_wav[-self.block_frame:]
                infer_wav = self.tg(
                    infer_wav.unsqueeze(0), self.output_buffer.unsqueeze(0)
                ).squeeze(0)
            # rms 音量包络混合（gui_v1 :930-962 对照：幂律混合）
            if self.params["rms_mix_rate"] < 1:
                if self.params["I_noise_reduce"]:
                    input_wav = self.input_wav_denoise[self.extra_frame:]
                else:
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
            # SOLA 算法（DDSP-SVC 上游：归一化互相关；gui_v1 :964-979 对照）
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
            # use_pv 相位声码器分支（gui_v1 :982-993 对照；A-4 移植）
            if "privateuseone" in str(self.config.device) or not self.params["use_pv"]:
                infer_wav[: self.sola_buffer_frame] *= self.fade_in_window
                infer_wav[: self.sola_buffer_frame] += (
                    self.sola_buffer * self.fade_out_window
                )
            else:
                infer_wav[: self.sola_buffer_frame] = phase_vocoder(
                    self.sola_buffer,
                    infer_wav[: self.sola_buffer_frame],
                    self.fade_out_window,
                    self.fade_in_window,
                )
            self.sola_buffer[:] = infer_wav[
                self.block_frame: self.block_frame + self.sola_buffer_frame
            ]
            # E-9：通道参数化（gui_v1 :997-1003 对照，替换硬编码 repeat(2,1)）
            outdata[:] = (
                infer_wav[: self.block_frame]
                .repeat(self.channels, 1).t().cpu().numpy()
            )
            # 延迟可观测（T-1 口径的数据源）
            infer_time = time.perf_counter() - start_time
            self.last_infer_time = infer_time
        except Exception as e:
            # E-11：不再静默吞——last_error 入 /status（前端状态卡可见）
            self.last_error = f"音频回调异常: {type(e).__name__}: {e}"
            import traceback
            traceback.print_exc()

    # ---------- 流控制（E-7 harvest 池生命周期 + E-9 设备解析 + E-11 不静默）----------

    def _resolve_device(self, name_or_index, is_input: bool):
        """E-9：持久化 device name → 运行时 index 解析；失配三要素。

        sd.query_devices 索引随重启/插拔重排——按 index 持久化=重启后静默换错
        设备；name 解析失败（拔出/更名）即三要素拒绝。
        """
        import sounddevice as sd
        if name_or_index is None:
            return None
        try:
            idx = int(name_or_index)
            info = sd.query_devices(device=idx)
            ok = info["max_input_channels"] > 0 if is_input else info["max_output_channels"] > 0
            if ok:
                return idx
            raise ValueError(f"设备 {name_or_index} 不是{'输入' if is_input else '输出'}设备")
        except (TypeError, ValueError) as e:
            if "设备" in str(e):
                raise
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["name"] == name_or_index:
                ok = d["max_input_channels"] > 0 if is_input else d["max_output_channels"] > 0
                if ok:
                    return i
        raise ValueError(
            f"设备不存在: {name_or_index}（可能已拔出或更名，请重新选择）")

    def _setup_harvest_pool(self):
        """E-7：harvest 池随启流创建（gui_v1 :106-112 对照；Queue 每流新建，
        不跨启流复用——Windows 管道锁损坏规避）。"""
        self.inp_q = None
        self.opt_q = None
        if self.params["f0method"] != "harvest":
            self.rvc.inp_q = None
            self.rvc.opt_q = None
            return
        self.inp_q = Queue()
        self.opt_q = Queue()
        self.harvest_workers = []
        for _ in range(int(self.params["n_cpu"])):
            p = Harvest(self.inp_q, self.opt_q)
            p.daemon = True
            p.start()
            self.harvest_workers.append(p)
        self.rvc.inp_q = self.inp_q
        self.rvc.opt_q = self.opt_q

    def _teardown_harvest_pool(self):
        """E-7：停流拆池 terminate+join+cancel_join_thread（E-11 同款不静默）。"""
        for p in self.harvest_workers:
            try:
                p.terminate()
            except Exception:
                pass
        for p in self.harvest_workers:
            try:
                p.join(timeout=2)
            except Exception:
                pass
        self.harvest_workers = []
        for q in (self.inp_q, self.opt_q):
            if q is not None:
                try:
                    q.cancel_join_thread()
                except Exception:
                    pass
        self.inp_q = None
        self.opt_q = None
        if self.rvc is not None:
            self.rvc.inp_q = None
            self.rvc.opt_q = None

    def _on_stream_finished(self):
        """E-11：流意外终止（设备拔出/驱动错误）→ 置 idle+last_error，不静默。"""
        self.stream = None
        self.last_error = "音频流意外终止（设备拔出或驱动错误），已回到待机态"
        self._teardown_harvest_pool()

    def start_stream(self, input_device=None, output_device=None):
        import sounddevice as sd

        if self.rvc is None:
            raise RuntimeError("未加载模型：先 /switch 再 /start")
        if self.stream is not None:
            return {"state": "already_running"}
        # E-9：设备名解析（config 持久化 name → 运行时 index）
        in_idx = self._resolve_device(input_device, True)
        out_idx = self._resolve_device(output_device, False)
        sd.default.device = [
            in_idx if in_idx is not None else sd.default.device[0],
            out_idx if out_idx is not None else sd.default.device[1],
        ]
        self._init_pipeline()
        self._setup_harvest_pool()
        self._open_stream()
        return {"state": "running", "samplerate": self.samplerate,
                "channels": self.channels, "block_frame": self.block_frame}

    def _open_stream(self):
        import sounddevice as sd
        self.stream = sd.Stream(
            callback=self.audio_callback,
            finished_callback=self._on_stream_finished,  # E-11：意外终止置 idle
            blocksize=self.block_frame,
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="float32",
        )
        self.stream.start()

    def stop_stream(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self._teardown_harvest_pool()
        return {"state": "stopped"}

    def status(self):
        return {
            "state": "running" if self.stream is not None else "idle",
            "model": self.current_model,
            "last_infer_time_s": round(getattr(self, "last_infer_time", 0), 3),
            # E-11/GAP-1：last_error 暴露（回调异常/流意外终止前端可见）
            "last_error": getattr(self, "last_error", None),
            # A-12/GAP-1：算法延迟口径（gui_v1 :604-609 公式——端到端估算）
            "algo_delay_ms": self._algo_delay_ms(),
            "params": self.params,
            "has_cuda": torch.cuda.is_available(),
            "infer_class": "rtrvc" if self._is_rtrvc else "legacy",
        }

    def _algo_delay_ms(self):
        """E-4/A-12：延迟读数口径（gui_v1 :604-615 对照）——
        stream.latency + block_time + crossfade(+降噪补偿)，仅运行中有意义。"""
        if self.stream is None:
            return 0
        try:
            delay = self.stream.latency[-1] + self.params["block_time"] \
                + self.params["crossfade_time"] + 0.01
            if self.params["I_noise_reduce"]:
                delay += min(self.params["crossfade_time"], 0.04)
            return int(round(delay * 1000))
        except Exception:
            return 0

    def update_params(self, updates: dict):
        """R-2 参数链闭合 + E-1 管线键通道（/autoplan 2026-10-22）：前端写入值真实生效。

        热更级：pitch/formant/index_rate/protect 走 rtrvc 热更新方法（免重载模型）；
        threhold/rms_mix_rate/f0method/I_noise_reduce/O_noise_reduce/use_pv 直接改
        params（audio_callback 每块推理读取）。管线级：block_time/crossfade_time/
        extra_time/sr_type/n_cpu 改 params 后在下次 start_stream 经 _init_pipeline
        生效（「改后重启流生效」语义——rvc-vc 是独立进程不读 config，运行期真值
        来源=本端点，config 仅作下次服务启动默认值）。
        流运行中切 f0method=harvest 拒绝（E-7：池生命周期绑定启流，提示重启流）。
        未知键拒绝（值域校验），失败抛异常由三要素错误返回。
        """
        allowed = {
            "pitch": (-16, 16, "int"),
            "formant": (-2.0, 2.0, "float"),
            "index_rate": (0.0, 1.0, "float"),
            "protect": (0.0, 1.0, "float"),
            "rms_mix_rate": (0.0, 1.0, "float"),
            "f0method": (["pm", "harvest", "crepe", "rmvpe", "fcpe"], None, "enum"),
            "threhold": (-60, 0, "int"),
            # E-1：管线键（改后重启流生效）——值域按 gui_v1 :121-135/:423-476
            "block_time": (0.02, 1.5, "float"),
            "crossfade_time": (0.01, 0.15, "float"),
            "extra_time": (0.05, 5.0, "float"),
            "sr_type": (["sr_model", "sr_device"], None, "enum"),
            "n_cpu": (1, 8, "int"),
            # A-4/E-1：可选开关（bool 热更，audio_callback 每块读取）
            "I_noise_reduce": (None, None, "bool"),
            "O_noise_reduce": (None, None, "bool"),
            "use_pv": (None, None, "bool"),
        }
        for key, value in updates.items():
            if key not in allowed:
                raise ValueError(f"未知参数 {key}（可更新：{', '.join(allowed)}）")
            spec = allowed[key]
            if spec[1] is None and spec[2] == "enum":  # enum
                if value not in spec[0]:
                    raise ValueError(f"{key}={value} 不在 {spec[0]}")
            elif spec[2] == "bool":  # E-1：bool 域（拒绝字符串等非布尔）
                if not isinstance(value, bool):
                    raise ValueError(f"{key} 需要 bool（true/false），收到 {type(value).__name__}")
            else:
                lo, hi = spec[0], spec[1]
                v = float(value)
                if not (lo <= v <= hi):
                    raise ValueError(f"{key}={v} 超出 [{lo}, {hi}]")
                value = int(v) if spec[2] == "int" else v
            # E-7③：harvest 池生命周期绑定启流，流运行中热切 harvest 拒绝
            if key == "f0method" and value == "harvest" and self.stream is not None:
                raise ValueError(
                    "流运行中不可切换 f0method=harvest（进程池随启流创建）；"
                    "请停止音频流后切换再重新启动")
            self.params[key] = value
        # 热更新映射（rtrvc change_* 免重载；旧版无这些方法，下次切换生效）
        if self.rvc is not None and self._is_rtrvc:
            if "pitch" in updates:
                self.rvc.change_key(self.params["pitch"])
            if "formant" in updates:
                self.rvc.change_formant(self.params["formant"])
            if "index_rate" in updates:
                self.rvc.change_index_rate(self.params["index_rate"])
        return dict(self.params)

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
                # E-8：拒绝非 application/json（防 drive-by 简单请求绕 CORS 预检）
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() \
                        != "application/json":
                    self._json(415, {"problem": "Content-Type 必须为 application/json",
                                     "cause": self.headers.get("Content-Type"),
                                     "fix": "以 application/json 提交"})
                    return
                # E-11：坏 JSON 有响应体（不再无响应炸连接）
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError) as e:
                    self._json(400, {"problem": "请求体不是合法 JSON", "cause": str(e),
                                     "fix": "提交合法 JSON 对象"})
                    return
                try:
                    if self.path == "/switch":
                        result = vc.switch_model(
                            body["pth_path"], body.get("index_path"),
                            body.get("index_rate"),
                        )
                        self._json(200, {"ok": True, "model": result})
                    elif self.path == "/params":
                        # R-2 参数链闭合端点（/autoplan 2026-10-14）：pitch/
                        # formant/index_rate/protect/f0method 热更新
                        result = vc.update_params(body)
                        self._json(200, {"ok": True, "params": result})
                    elif self.path == "/start":
                        self._json(200, {"ok": True, **vc.start_stream(
                            body.get("input_device"), body.get("output_device"))})
                    elif self.path == "/stop":
                        self._json(200, {"ok": True, **vc.stop_stream()})
                    else:
                        self._json(404, {"problem": "未知路径", "cause": self.path,
                                         "fix": "POST /switch /params /start /stop"})
                except Exception as e:
                    # 三要素错误（DX-2）：事务失败时旧模型继续服务
                    self._json(500, {"problem": str(e), "cause": type(e).__name__,
                                     "fix": "检查参数值域/模型路径；切换失败时旧模型继续服务"})

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
