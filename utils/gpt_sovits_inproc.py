# -*- coding: UTF-8 -*-
"""
GPT-SoVITS 进程内推理引擎（深化整合 R-1/R-3/R-4）

职责：在 main.py 核心进程内 import GPT-SoVITS TTS 类，以专用锁+生成器消费循环
提供合成能力，替代 api_v2 HTTP 回环（用户终门 uc1a）。

硬性约束（Eng 审查发现，违反即错）：
1. 仅 main.py 核心进程 import——NiceGUI 前端进程（frontend/main.py）禁止使用，
   否则显存双份 + NiceGUI timer 阻塞事件循环（既有 investigate 记忆教训）。
2. TTS_Config 用 dict 构造且 config_path 指向 AUTOlive 自己的 yaml——禁止指上游
   GPT_SoVITS/configs/tts_infer.yaml（init_t2s/init_vits/enable_half_precision
   共 3 处无条件 save_configs 会回写该文件，污染上游配置）；引擎另以 monkeypatch
   将 save_configs 置为 no-op 双保险。
3. 路径预检 isabs+exists——TTS_Config 对缺失路径会静默回落到 cwd 相对默认值
   （TTS.py:366-377），必失败且无提示，故构造前先验。
4. 互斥锁包住"整个生成器消费过程"（非单 chunk）——TTS.run 非线程安全
   （prompt_cache 共享），并发请求排队等待。
5. 换模=实例重建式事务：新 TTS 对象构建成功才 swap 引用，失败旧对象继续服务
   （CEO-S2 语义平移），加载排队在无推理时执行。
6. EDTalk 实时链路=R-3 裁决：合成落盘→playback_manager 既有通道（is_edtalk_active
   分流）零改动复用；本引擎 on_chunk 回调仅作为观测接口（真流式 NOT in scope）。
7. 全局副作用落点声明：主进程启动需保证 ./音频输出/ 目录存在（TextPreprocessor
   写 ./音频输出/tts-out.txt，相对 cwd=AUTOlive 根）。

状态机：
  uninitialized --start(异步加载)--> loading --成功--> ready <--> inferencing(互斥)
                                        |--失败--> error
  换模事务: ready --switch--> loading(new) --成功--> ready[新模型]
                                        --失败--> ready[旧模型保持+notify]
"""
import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

import numpy as np
import soundfile as sf
from loguru import logger

from utils.autolive_paths import AutolivePaths

# 引擎状态
ST_UNINIT = "uninitialized"
ST_LOADING = "loading"
ST_READY = "ready"
ST_ERROR = "error"


class EngineNotReadyError(RuntimeError):
    """引擎未就绪（loading/error 态请求合成）。"""


class EngineConfigError(RuntimeError):
    """TTS_Config 路径预检失败（防 TTS.py:366-377 静默回落）。"""


class GptSovitsInproc:
    """进程内 TTS 引擎（单实例，模块级 get_engine() 取用）。"""

    # ManagedProcess 鸭子接口（R-5 胶囊接入）：audio_player builtin 先例同款——
    # 胶囊组件零改动消费 name/state/fail_count/tail_log/manual_restart。
    name = "gpt-sovits(进程内)"
    fail_count = 0

    def tail_log(self, lines: int = 8) -> str:
        eng_line = (f"state={self.state} inferencing={self._inferencing} "
                    f"last_synth={self.last_synth_ms}ms first_chunk={self.last_first_chunk_ms}ms")
        return "\n".join([eng_line] + ([f"error: {self._error}"] if self._error else [])[-lines:])

    def __init__(self, config, paths: AutolivePaths):
        self.config = config
        self.paths = paths
        self._gsv_root = paths.gpt_sovits_root
        self._lock = threading.Lock()          # 合成与换模互斥（整个消费过程）
        self._state_lock = threading.Lock()    # 状态字段读写
        self._state = ST_UNINIT
        self._tts = None                       # GPT_SoVITS.TTS 实例
        self._tts_cfg = None                   # TTS_Config 实例（换模复用）
        self._error: str = ""
        self._inferencing = False
        self.on_state_change: Optional[Callable[[str, str], None]] = None
        # 合成观测（S8 增补：耗时+首块延迟）
        self.last_synth_ms: int = 0
        self.last_first_chunk_ms: int = 0

    # ---------- 状态 ----------

    @property
    def ready(self) -> bool:
        """内部就绪判定（与胶囊 state 词汇解耦——property state 返回 ManagedProcess 词汇）。"""
        with self._state_lock:
            return self._state == ST_READY and self._tts is not None

    @property
    def state(self) -> str:
        """胶囊语义映射（D-2 四态）：loading→starting、ready→healthy、error→red。"""
        with self._state_lock:
            raw = self._state
        return {"loading": "starting", ST_READY: "healthy", ST_ERROR: "red"}.get(raw, "stopped")

    def raw_state(self) -> str:
        with self._state_lock:
            return self._state

    def manual_restart(self) -> bool:
        """胶囊"重启"按钮（R-5/D-2：语义=重新加载模型，非进程重启）。"""
        if self._tts is None:
            self.start()
            return True
        ok, _msg = self.switch_model("", "")
        return ok

    @property
    def inferencing(self) -> bool:
        return self._inferencing

    @property
    def error(self) -> str:
        return self._error

    def _set_state(self, st: str, err: str = ""):
        with self._state_lock:
            self._state = st
            self._error = err
        logger.info(f"[gsv-inproc] 状态 → {st}" + (f"（{err}）" if err else ""))
        if self.on_state_change:
            try:
                self.on_state_change(st, err)
            except Exception:
                logger.error("[gsv-inproc] 状态回调异常", exc_info=True)

    # ---------- 配置构造 ----------

    def _resolve_weight(self, value: str, root_key: str) -> str:
        """权重路径解析：绝对路径原样；相对路径按 gsv_root 拼（对齐上游 cwd 语义）。"""
        if not value:
            return ""
        p = str(value).strip()
        if not os.path.isabs(p):
            p = os.path.join(self._gsv_root, p)
        return os.path.abspath(p)

    def _build_tts_config(self, gpt_model: str = "", sovits_model: str = "") -> tuple:
        """构造 TTS_Config 输入 dict + 输出 yaml 路径（回写禁令见模块头 #2）。

        预检（Eng P2-4）：所有路径 isabs+exists，缺失抛 EngineConfigError。
        """
        gs = self.config.get("gpt_sovits") or {}
        params = gs.get("params") or {}
        version = params.get("version") or "v2ProPlus"

        t2s = gpt_model or params.get("gpt_model_path") or ""
        vits = sovits_model or params.get("sovits_model_path") or ""
        bert = params.get("bert_base_path") or "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
        hubert = params.get("cnhuhbert_base_path") or "GPT_SoVITS/pretrained_models/chinese-hubert-base"

        t2s = self._resolve_weight(t2s, "gpt")
        vits = self._resolve_weight(vits, "sovits")
        bert = self._resolve_weight(bert, "bert")
        hubert = self._resolve_weight(hubert, "hubert")

        missing = [(k, v) for k, v in
                   (("t2s", t2s), ("vits", vits), ("bert", bert), ("hubert", hubert)) if not v or not os.path.exists(v)]
        if missing:
            raise EngineConfigError(
                "TTS 模型路径缺失（TTS_Config 会静默回落到必失败的相对默认值，故构造前拦截）: "
                + ", ".join(f"{k}={v}" for k, v in missing))

        cfg_dict = {
            "custom": {
                "device": params.get("device") or "cuda",
                "is_half": bool(params.get("is_half", True)),
                "version": version,
                "t2s_weights_path": t2s,
                "vits_weights_path": vits,
                "bert_base_path": bert,
                "cnhuhbert_base_path": hubert,
            },
        }
        # 回写目标=AUTOlive 自己的 yaml（绝不上游 configs/tts_infer.yaml）
        out_yaml = os.path.join(self.paths.autolive_home, "config", "gpt_sovits_inproc.yaml")
        return cfg_dict, out_yaml

    # ---------- 加载 / 换模 ----------

    def _import_tts(self):
        """import GPT-SoVITS TTS（PYTHONPATH 契约：调用前 autolive_paths 注入已生效）。"""
        # sv.py:2 按 os.getcwd() 拼 eres2net 路径（上游预期 cwd=gsv_root）——主进程
        # cwd=AUTOlive 根，故预注入正确绝对路径（上游 append 的坏路径无害）
        eres = os.path.join(self._gsv_root, "GPT_SoVITS", "eres2net")
        if os.path.isdir(eres) and eres not in sys.path:
            sys.path.insert(0, eres)
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config  # noqa: 延迟 import
        # 双保险 #2：save_configs no-op（防 3 处无条件回写任何 yaml）
        TTS_Config.save_configs = lambda *a, **k: None
        self._patch_shadows()
        return TTS, TTS_Config

    def _patch_shadows(self):
        """主进程模块遮蔽补丁（users.pth 隐患的显式化）。

        权重 pickle 记录 'utils.HParams'（上游 GPT_SoVITS/utils.py 的类），而
        'utils' 在主进程被 AUTOlive utils 包占据 → torch.load 反序列化崩。
        处置：从上游文件动态加载 utils 模块，把 HParams 类附加到 AUTOlive utils
        命名空间（pickle find_class 经 sys.modules['utils'] getattr 命中）。
        同理处理 sv.py 相对 cwd 的 sv_path 硬编码。
        """
        try:
            import importlib.util
            import utils as _autolive_utils
            if not hasattr(_autolive_utils, "HParams"):
                up = os.path.join(self._gsv_root, "GPT_SoVITS", "utils.py")
                spec = importlib.util.spec_from_file_location("gsv_upstream_utils", up)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # 顶层仅 librosa/torch 等（环境已备）
                _autolive_utils.HParams = mod.HParams
                logger.info("[gsv-inproc] HParams 已附加到 utils 命名空间（遮蔽补丁）")
        except Exception:
            logger.warning("[gsv-inproc] HParams 遮蔽补丁失败", exc_info=True)
        # sv.py:3 硬编码相对 cwd 的 SV 底模路径（上游预期 cwd=gsv_root）——
        # 主进程 cwd=AUTOlive 根，SV 实例化前改写为绝对路径（不改上游文件）
        try:
            import sv as _sv_mod
            if not os.path.isabs(_sv_mod.sv_path):
                _abs = os.path.join(self._gsv_root, _sv_mod.sv_path)
                if os.path.exists(_abs):
                    _sv_mod.sv_path = _abs
                    logger.info(f"[gsv-inproc] sv_path 已改写为绝对路径: {_abs}")
        except Exception:
            logger.warning("[gsv-inproc] sv_path 补丁失败（SV 模型可能加载失败）", exc_info=True)
        # text/chinese2.py:36 读 os.environ["bert_path"]（上游 webui env 约定），
        # 缺省相对 cwd——合成链 BERT 推理会重载失败，这里按上游约定注入绝对路径
        try:
            bert_abs = self._resolve_weight(
                (self.config.get("gpt_sovits") or {}).get("params", {}).get("bert_base_path")
                or "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large", "bert")
            if os.path.isdir(bert_abs):
                os.environ["bert_path"] = bert_abs
                logger.info(f"[gsv-inproc] bert_path env 已注入: {bert_abs}")
        except Exception:
            logger.warning("[gsv-inproc] bert_path env 注入失败", exc_info=True)

    def _build_tts(self, gpt_model: str = "", sovits_model: str = ""):
        """构建全新 TTS 实例（换模事务的"新对象"侧，不触碰旧实例）。"""
        TTS, TTS_Config = self._import_tts()
        cfg_dict, out_yaml = self._build_tts_config(gpt_model, sovits_model)
        cfg = TTS_Config(cfg_dict)
        cfg.config_path = out_yaml  # save_configs 已 no-op，此行仅占位语义
        return TTS(cfg)

    def start(self, blocking: bool = False):
        """异步加载模型（不阻塞主进程启动，胶囊显示 loading）。"""
        if self.state in (ST_LOADING, ST_READY):
            return
        self._set_state(ST_LOADING)
        threading.Thread(target=self._load, daemon=True,
                         name="gsv-inproc-load").start() if not blocking else self._load()

    def _load(self):
        try:
            t0 = time.time()
            self._tts = self._build_tts()
            self._tts_cfg = self._tts.configs
            logger.info(f"[gsv-inproc] 模型加载完成 {time.time() - t0:.1f}s（v2ProPlus 四件套+SV）")
            self._set_state(ST_READY)
        except Exception as e:
            logger.error("[gsv-inproc] 模型加载失败", exc_info=True)
            self._set_state(ST_ERROR, str(e))

    def switch_model(self, gpt_model: str, sovits_model: str) -> tuple:
        """换模事务（R-4）：新 TTS 对象构建成功才 swap；失败旧实例继续服务。

        排队语义：与合成共用 _lock——换模等待当前合成完成（InferenceGate 语义平移）。
        返回 (ok, message)。
        """
        with self._lock:
            if self._inferencing:
                # 理论不可达（_lock 已串行），防御性双检
                return False, "推理进行中，换模被拒绝"
            prev = self._tts
            try:
                t0 = time.time()
                new_tts = self._build_tts(gpt_model, sovits_model)
            except Exception as e:
                # 事务回滚：旧实例未动，继续服务
                logger.error(f"[gsv-inproc] 换模失败，保持旧模型: {e}")
                return False, f"新模型加载失败，已保持旧模型：{e}"
            self._tts = new_tts
            self._tts_cfg = new_tts.configs
            del prev  # 释放旧实例引用（显存回收交 torch GC）
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            if self._state != ST_READY:
                self._set_state(ST_READY)
            logger.info(f"[gsv-inproc] 换模完成 {time.time() - t0:.1f}s")
            return True, "切换成功"

    # ---------- 合成 ----------

    def synthesize(self, text: str, params: Dict,
                   out_path: str,
                   on_chunk: Optional[Callable[[int, np.ndarray], None]] = None) -> str:
        """同步合成（R-1 核心）：互斥锁包住整个生成器消费过程。

        params 键对齐 TTS.run 清单（text_lang/ref_audio_path/prompt_text/prompt_lang/
        top_k/top_p/temperature/text_split_method/batch_size/speed_factor/fragment_interval/
        repetition_penalty/seed 等，来自 gpt_sovits.params 节或调用方覆盖）。
        消费循环逐块回调 on_chunk（R-3 观测接口；文件级推送由 playback_manager 既有通道负责）。
        返回 wav 路径；异常向上抛（调用方=my_tts 层转四要素文案 DX-12）。
        """
        if not self.ready:
            raise EngineNotReadyError(
                f"引擎状态 {self.raw_state()}（就绪后可用；error 态请查看胶囊日志并重载模型）")
        with self._lock:
            self._inferencing = True
            t0 = time.time()
            first_ms = 0
            chunks: List[np.ndarray] = []
            sr0 = None
            try:
                gen = self._tts.run({"text": text, **params})
                for sr, chunk in gen:
                    if first_ms == 0:
                        first_ms = int((time.time() - t0) * 1000)
                    if sr0 is None:
                        sr0 = sr
                    chunks.append(np.asarray(chunk, dtype=np.int16))
                    if on_chunk:
                        try:
                            on_chunk(sr, chunk)
                        except Exception:
                            logger.warning("[gsv-inproc] on_chunk 回调异常（忽略，不影响合成）", exc_info=True)
                pcm = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                sf.write(out_path, pcm, sr0 or 32000, subtype="PCM_16")
                self.last_synth_ms = int((time.time() - t0) * 1000)
                self.last_first_chunk_ms = first_ms
                logger.info(f"[gsv-inproc] 合成完成 {len(text)} 字 "
                            f"首块 {first_ms}ms 总 {self.last_synth_ms}ms → {os.path.basename(out_path)}")
                return out_path
            except Exception as e:
                logger.error("[gsv-inproc] 合成异常", exc_info=True)
                # TTS.run 内部已自愈（置 None+重 init，TTS.py:1439-1484）；此处转状态提示
                raise
            finally:
                self._inferencing = False

    def warmup(self):
        """预热（S7 语义平移）：就绪后进程内合成一条短句，防首条直播消息吃冷启动。"""
        try:
            gs = self.config.get("gpt_sovits") or {}
            params = gs.get("params") or {}
            ref = params.get("ref_audio_path") or ""
            if not ref or not os.path.exists(ref):
                logger.info("[gsv-inproc] 预热跳过（ref_audio_path 未配置或不存在）")
                return
            out = os.path.join(self.paths.autolive_home, "音频输出", "inproc_warmup.wav")
            self.synthesize("预热。", {
                "text_lang": params.get("language") or "zh",
                "ref_audio_path": ref,
                "prompt_text": params.get("prompt_text") or "",
                "prompt_lang": params.get("prompt_language") or "zh",
                "streaming_mode": False,
            }, out)
            logger.info("[gsv-inproc] 预热完成")
        except Exception as e:
            logger.warning(f"[gsv-inproc] 预热失败（不阻塞启动）: {e}")


# ---------- 模块级单例 ----------

_engine: Optional[GptSovitsInproc] = None


def init_engine(config, paths: AutolivePaths) -> GptSovitsInproc:
    """main.py 核心进程启动时调用（仅一次）。前端进程禁止调用（模块头约束 #1）。"""
    global _engine
    if _engine is None:
        _engine = GptSovitsInproc(config, paths)
    return _engine


def get_engine() -> Optional[GptSovitsInproc]:
    return _engine


# ---------- 控制面窄端点（F-2 试听链/R-5 胶囊数据源） ----------

def register_tts_routes(app, config) -> None:
    """把 /tts/* 路由挂到 main.py 进程的 FastAPI 应用（web_server 一行挂接）。

    前端（webui 进程）没有引擎（模块头约束 #1），试听与状态查询全部走本通道。
    """
    from fastapi import HTTPException

    @app.get("/tts/status")
    def tts_status():
        eng = get_engine()
        if eng is None:
            return {"backend": config.get("gpt_sovits", "backend") or "inproc",
                    "state": "uninitialized", "error": "", "inferencing": False}
        return {"backend": config.get("gpt_sovits", "backend") or "inproc",
                "state": eng.state, "error": eng.error, "inferencing": eng.inferencing,
                "last_synth_ms": eng.last_synth_ms,
                "last_first_chunk_ms": eng.last_first_chunk_ms}

    @app.post("/tts/reload")
    def tts_reload():
        eng = get_engine()
        if eng is None:
            raise HTTPException(status_code=503, detail="进程内引擎未初始化（backend=api 模式下不可用）")
        ok, msg = eng.switch_model("", "")  # 空值=按 params 当前值重建（重载语义）
        return {"success": ok, "message": msg, "state": eng.state}

    @app.post("/tts/preview")
    def tts_preview(body: dict):
        """试听：进程内合成指定文本（默认固定文案），返回音频路径（前端 web audio 播放）。"""
        eng = get_engine()
        if eng is None:
            raise HTTPException(status_code=503, detail="进程内引擎未初始化")
        if eng.state != ST_READY:
            raise HTTPException(status_code=409,
                                detail=f"引擎未就绪（{eng.state}）——problem: 模型加载中或失败；"
                                       "cause: 见 /tts/status error 字段；fix: 等待加载完成后重试")
        gs = config.get("gpt_sovits") or {}
        params = dict(gs.get("params") or {})
        text = (body or {}).get("text") or "你好，这里是声音试听。"
        ref = params.get("ref_audio_path") or ""
        if not ref or not os.path.exists(ref):
            raise HTTPException(status_code=409,
                                detail="ref_audio_path 未配置或文件不存在——problem: 参考音频缺失；"
                                       "fix: 在 TTS 页配置参考音频或选择声音档案")
        run_params = {
            "text_lang": params.get("text_lang") or "zh",
            "ref_audio_path": ref,
            "prompt_text": params.get("prompt_text") or "",
            "prompt_lang": params.get("prompt_lang") or "zh",
            "top_k": params.get("top_k", 15), "top_p": params.get("top_p", 1.0),
            "temperature": params.get("temperature", 1.0),
            "text_split_method": params.get("text_split_method") or "cut5",
            "batch_size": params.get("batch_size", 1),
            "speed_factor": params.get("speed_factor", 1.0),
            "streaming_mode": False,
        }
        out = os.path.join(os.getcwd(), "音频输出", f"preview_{int(time.time())}.wav")
        try:
            path = eng.synthesize(text, run_params, out)
            return {"success": True, "path": path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"合成失败: {e}")
