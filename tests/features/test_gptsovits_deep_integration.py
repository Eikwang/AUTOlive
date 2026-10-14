# -*- coding: utf-8 -*-
"""TST-1 单测包（深化整合）：不依赖 GPU/真实模型的回归测试。

覆盖：引擎状态机/换模事务（mock TTS 类接口）/EngineConfigError 预检/
合成参数白名单剥离/训练管线参数固化（bernard 回归/双减半修正/11 argv）/ASR 路由。
GPU 全链路冒烟=TST-2（环境依赖，另行执行）。
"""
import json
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.train_voice_pipeline import (  # noqa: E402
    ASR_ROUTE, DEFAULTS, REQUIRED_ASSETS, TrainVoicePipeline,
)
from utils.gpt_sovits_inproc import (  # noqa: E402
    ST_READY, EngineConfigError, GptSovitsInproc,
)

# ---------- 训练管线（T-2/T-3/T-4 回归） ----------


class _FakeConfig:
    def get(self, *keys):
        return None


class _FakePaths:
    autolive_home = os.getcwd()
    gpt_sovits_root = os.path.join(os.getcwd(), "apps", "gpt-sovits")
    gpt_sovits_python = "python"

    def subprocess_env(self, extra=None):
        import os as _os
        env = _os.environ.copy()
        if extra:
            env.update(extra)
        return env


def _make_pipeline():
    p = TrainVoicePipeline(_FakeConfig(), _FakePaths())
    p.params = dict(DEFAULTS, dataset_dir=".", exp_name="t")
    return p


def test_defaults_match_user_spec():
    """用户规格固化：SoVITS batch4/epoch10/lr0.4/save5；GPT epoch20/save10/DPO。"""
    assert DEFAULTS["batch_size"] == 4
    assert DEFAULTS["epochs_s2"] == 10
    assert DEFAULTS["epochs_s1"] == 20
    assert DEFAULTS["text_low_lr_rate"] == 0.4
    assert DEFAULTS["save_every_s2"] == 5
    assert DEFAULTS["save_every_s1"] == 10
    assert DEFAULTS["if_dpo"] is True


def test_s1_pretrained_is_s1v3_not_bernard():
    """bernard 死路径回归（T-3 关键修复）：pretrained_s1 必须=s1v3.ckpt。"""
    p = _make_pipeline()
    p._opt_dir = lambda: "."
    import yaml as _yaml
    data = {"train": {"epochs": 0, "batch_size": 0, "save_every_n_epoch": 0,
                      "if_save_latest": False, "if_save_every_weights": False,
                      "if_dpo": 0},
            "data": {"train": {"phoneme_path": "", "semantics_path": ""},
                     "eval": {"phoneme_path": "", "semantics_path": {}}}}
    captured = {}

    def _fake_run(self, cmd, env_extra=None, timeout=7200):
        captured["cmd"] = cmd
        return True

    with patch.object(TrainVoicePipeline, "_run", _fake_run), \
         patch("builtins.open", MagicMock()), \
         patch("yaml.safe_load", return_value=data), \
         patch("yaml.safe_dump", return_value=""):
        p._st_s1()
    # pretrained_s1 写入 yaml 配置（非命令行）——断言配置值=s1v3.ckpt（bernard 回归）
    assert "s1v3.ckpt" in data["pretrained_s1"]
    assert "bernard" not in data["pretrained_s1"]
    assert data["train"]["batch_size"] == DEFAULTS["batch_size"]  # UI 值直写（无 //2）
    assert data["train"]["if_dpo"] == 1


def test_s2_params_written():
    """s2 模板覆写：batch/epochs/lr 权重/save_every/pretrained v2ProPlus 路径。"""
    p = _make_pipeline()
    p._opt_dir = lambda: "."
    data = {"train": {}, "model": {}, "data": {}, "s2_ckpt_dir": "", "save_weight_dir": "",
            "name": "", "version": ""}
    with patch.object(TrainVoicePipeline, "_run", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("json.load", return_value=data), \
         patch("json.dump", return_value=None):
        p._st_s2()
    assert data["train"]["batch_size"] == 4
    assert data["train"]["epochs"] == 10
    assert data["train"]["text_low_lr_rate"] == 0.4
    assert data["train"]["save_every_epoch"] == 5
    assert data["train"]["if_save_latest"] is True
    assert "s2Gv2ProPlus" in data["train"]["pretrained_s2G"]
    assert "s2Dv2ProPlus" in data["train"]["pretrained_s2D"]


def test_slice_11_argv():
    """切分调用回归（2026-10-14 用户裁决）：自有脚本+上游 9 参数+max_sec=8 一道流程。"""
    p = _make_pipeline()
    captured = {}

    def _fake_run(self, cmd, env_extra=None, timeout=7200):
        captured["cmd"] = [str(x) for x in cmd]
        return True

    with patch.object(TrainVoicePipeline, "_run", _fake_run):
        p._st_slice()
    argv = captured["cmd"]
    assert any("smart_slice_audio.py" in x for x in argv), f"应调自有切分脚本: {argv}"
    # 上游 9 参数（threshold/min_length/min_interval/hop/max_sil/_max/alpha/i/all）+ max_sec
    assert argv[-10:] == ["-34", "4000", "300", "10", "500", "0.9", "0.25", "0", "1", "8"]


def test_asr_route_mapping():
    """T-2 路由：zh/yue→funasr float32；en/ja/ko/auto→fasterwhisper large-v3 float16。"""
    assert "funasr_asr" in ASR_ROUTE["zh"][0] and "float32" in ASR_ROUTE["zh"][1]
    assert "funasr_asr" in ASR_ROUTE["yue"][0]
    for lang in ("en", "ja", "ko", "auto"):
        assert "fasterwhisper_asr" in ASR_ROUTE[lang][0], lang
        assert "large-v3" in ASR_ROUTE[lang][1]
        assert "float16" in ASR_ROUTE[lang][1]


def test_check_assets_reports_missing():
    """T-4 预检：缺失项有清单且 s1v3 在 REQUIRED_ASSETS（实际配置路径校验）。"""
    assert any("s1v3.ckpt" in rel for rel, _ in REQUIRED_ASSETS)
    assert not any("bernard" in rel for rel, _ in REQUIRED_ASSETS)
    p = _make_pipeline()
    p.params = {"asr_lang": "zh"}
    p.gsv_root = os.path.join("nonexistent-root")  # 强制全缺
    p.autolive_home = os.path.join("nonexistent-root")
    issues = p.check_assets()
    assert issues, "全缺场景应报告缺失清单"
    notes = [n for _, n, _ in issues]
    assert any("s1v3" in n for n in notes)


# ---------- 进程内引擎（R-1 回归，mock TTS 类接口） ----------


def _make_engine(cfg_dict=None):
    cfg = MagicMock()
    cfg.get = lambda *keys: (cfg_dict or {}).get(keys[-1]) if keys else None
    eng = GptSovitsInproc.__new__(GptSovitsInproc)
    GptSovitsInproc.__init__(eng, cfg, _FakePaths())
    return eng


def test_synthesize_rejects_when_not_ready():
    eng = _make_engine()
    with pytest.raises(Exception):
        eng.synthesize("x", {}, "out.wav")


def test_synthesize_critical_section_and_chunks():
    """互斥临界区：并发合成串行执行（R-1 验收），on_chunk 逐块回调。"""
    eng = _make_engine()
    import numpy as np

    class _FakeTTS:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def run(self, params):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            for i in range(3):
                import time as _t
                _t.sleep(0.01)
                yield (32000, np.zeros(160, dtype=np.int16))
            with self.lock:
                self.active -= 1

    eng._tts = _FakeTTS()
    with eng._state_lock:
        eng._state = ST_READY
    chunks_seen = []
    out = os.path.join("音频输出", "test_crit.wav")
    eng.synthesize("测试", {}, out, on_chunk=lambda sr, c: chunks_seen.append(len(c)))
    assert len(chunks_seen) == 3
    assert eng._tts.max_active == 1  # 临界区生效

    # 并发两条：第二条排队（max_active 仍 1）
    ts = [threading.Thread(target=eng.synthesize, args=("x", {}, out)) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(timeout=10) for t in ts]
    assert eng._tts.max_active == 1
    os.path.exists(out) and os.remove(out)


def test_switch_model_rollback_on_failure():
    """换模事务（R-4）：新模型构建失败→旧实例保持、状态不破坏。"""
    eng = _make_engine()
    old_tts = object()
    eng._tts = old_tts
    with eng._state_lock:
        eng._state = ST_READY
    with patch.object(GptSovitsInproc, "_build_tts", side_effect=RuntimeError("加载失败")):
        ok, msg = eng.switch_model("new.ckpt", "new.pth")
    assert ok is False
    assert "保持旧模型" in msg
    assert eng._tts is old_tts  # 旧实例原封不动


def test_config_precheck_blocks_relative_fallback():
    """Eng P2-4：TTS_Config 缺失路径会静默回落，构造前必须拦截。"""
    eng = _make_engine(cfg_dict={"params": {}})
    # gsv_root 下无模型文件→EngineConfigError
    with pytest.raises(EngineConfigError):
        eng._build_tts_config()


def test_inproc_param_whitelist_strips_http_only_keys():
    """my_tts 白名单：media_type/streaming_mode 等 HTTP 专属键不进 TTS.run 参数。"""
    from utils.audio_handle.my_tts import MY_TTS
    for banned in ("media_type", "streaming_mode", "response_format", "delivery"):
        assert banned not in MY_TTS._INPROC_PARAM_KEYS
    assert "ref_audio_path" in MY_TTS._INPROC_PARAM_KEYS


def test_prepare_accepts_full_frontend_signature():
    """防回归（E2E 实跑暴露）：prepare 签名必须接受前端 F-1 全部控件参数。"""
    p = TrainVoicePipeline(_FakeConfig(), _FakePaths())
    p.prepare(dataset_dir=".", exp_name="t", authorized=True,
              batch_size=4, already_dry=False, asr_lang="zh",
              epochs_s2=10, epochs_s1=20, text_low_lr_rate=0.4,
              save_every_s2=5, save_every_s1=10, if_dpo=True)
    assert p.params["epochs_s2"] == 10 and p.params["epochs_s1"] == 20
    assert p.params["if_dpo"] is True and p.params["asr_lang"] == "zh"


def test_split_long_clips():
    """OOM 防护回归（2026-10-14 用户裁决）：自有切分脚本一道流程——chunk 细分产物全部 <=8s。"""
    import numpy as np
    import importlib.util
    import tempfile
    # 加载 smart_slice_audio（依赖 gsv_root/tools 的 slicer2）
    sys.path.insert(0, os.path.join(os.getcwd(), "apps", "gpt-sovits", "tools"))
    spec = importlib.util.spec_from_file_location(
        "smart_slice_audio", os.path.join(os.getcwd(), "utils", "smart_slice_audio.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sr = 32000
    # 构造 25 秒连续正弦（无静音——绕过静音切分的场景）+分段细分
    t = np.linspace(0, 25, sr * 25, endpoint=False)
    chunk = np.sin(2 * np.pi * 220 * t) * 0.5
    pieces = list(mod._subdivide(chunk, sr, 8.0))
    assert len(pieces) >= 3, f"25s 应细分为 >=3 段，实际 {len(pieces)}"
    for piece in pieces:
        assert len(piece) <= 8.0 * sr, f"细分段 {len(piece)/sr:.2f}s 超 8s"
    assert sum(len(p) for p in pieces) == len(chunk), "细分应无缝覆盖原 chunk"
