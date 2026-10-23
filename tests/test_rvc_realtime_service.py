# -*- coding: utf-8 -*-
"""RVC 实时变声服务端单测（TE1-TE5 服务端逻辑；/autoplan 2026-10-22 批次）。

绕过 RealtimeVC.__init__ 的 RVC runtime 依赖（torch+RVCConfig），用 __new__
直测纯逻辑：E-1 参数域校验 / E-2 守卫要素 / E-7 harvest 拒绝 / E-8 白名单 /
E-9 设备解析 / E-11 状态暴露。
"""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 主进程无 torch（RVC runtime 专用依赖）——stub 注入仅支撑模块加载与纯逻辑测试；
# 真实张量路径的测试用 pytest.importorskip("torch") 条件执行。
try:
    import torch  # noqa: F401
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    if "torch" not in sys.modules:
        _torch_stub = types.ModuleType("torch")
        _torch_stub.cuda = types.SimpleNamespace(
            is_available=lambda: False, empty_cache=lambda: None)
        sys.modules["torch"] = _torch_stub


def _make_svc(tmp_path, monkeypatch, params_over=None, stream=None):
    """构造跳过 __init__ 的 RealtimeVC（纯逻辑测试面）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rvc_realtime_service_test",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "utils", "rvc_realtime_service.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    svc = mod.RealtimeVC.__new__(mod.RealtimeVC)
    svc.params = {
        "block_time": 0.25, "crossfade_time": 0.04, "extra_time": 0.5,
        "threhold": -60, "rms_mix_rate": 0.25, "index_rate": 0.75,
        "n_cpu": 4, "f0method": "rmvpe", "pitch": 0, "formant": 0.0,
        "protect": 0.33, "sr_type": "sr_model",
        "I_noise_reduce": False, "O_noise_reduce": False, "use_pv": False,
        "input_device": None, "output_device": None,
    }
    if params_over:
        svc.params.update(params_over)
    svc.stream = stream
    svc.rvc = None
    svc._is_rtrvc = True
    svc.rvc_root = str(tmp_path / "apps" / "rvc")
    svc.current_model = None
    svc.last_error = None
    monkeypatch.setenv("AUTOLIVE_HOME", str(tmp_path))
    return svc, mod




# sounddevice 同样是 RVC runtime 依赖——涉及 sd.query_devices 的测试用 stub。
class _FakeSD:
    @staticmethod
    def query_devices(device=None):
        if device is None:
            return [
                {"name": "测试麦克风", "max_input_channels": 2, "max_output_channels": 0},
                {"name": "测试扬声器", "max_input_channels": 0, "max_output_channels": 2},
            ]
        return {"name": "测试麦克风", "max_input_channels": 2, "max_output_channels": 0,
                "default_samplerate": 48000}


@pytest.fixture
def fake_sd(monkeypatch):
    try:
        import sounddevice  # noqa: F401
        return None  # 真实 sd 可用则不打桩
    except ImportError:
        import types as _t
        stub = _t.ModuleType("sounddevice")
        stub.query_devices = _FakeSD.query_devices
        monkeypatch.setitem(sys.modules, "sounddevice", stub)
        return stub

# ---------- E-1：/params 五管线键 + bool 域 ----------

class TestE1ParamChannel:
    def test_pipeline_keys_accepted(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        out = svc.update_params({
            "block_time": 0.5, "crossfade_time": 0.08, "extra_time": 1.0,
            "sr_type": "sr_device", "n_cpu": 6,
        })
        assert out["block_time"] == 0.5 and out["sr_type"] == "sr_device"
        assert out["n_cpu"] == 6 and out["extra_time"] == 1.0

    def test_bool_keys_type_checked(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        out = svc.update_params({"I_noise_reduce": True, "use_pv": True})
        assert out["I_noise_reduce"] is True and out["use_pv"] is True
        with pytest.raises(ValueError, match="bool"):
            svc.update_params({"I_noise_reduce": "true"})  # 字符串拒绝

    def test_pipeline_value_ranges(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="超出"):
            svc.update_params({"block_time": 2.0})
        with pytest.raises(ValueError, match="超出"):
            svc.update_params({"n_cpu": 16})
        with pytest.raises(ValueError, match="不在"):
            svc.update_params({"sr_type": "sr_invalid"})

    def test_unknown_key_rejected(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="未知参数"):
            svc.update_params({"nonexistent": 1})


# ---------- E-7③：流运行中切 harvest 拒绝 ----------

class TestE7HarvestGate:
    def test_harvest_switch_rejected_while_running(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch, stream=object())
        with pytest.raises(ValueError, match="harvest"):
            svc.update_params({"f0method": "harvest"})

    def test_harvest_switch_allowed_when_idle(self, tmp_path, monkeypatch):
        svc, _ = _make_svc(tmp_path, monkeypatch, stream=None)
        out = svc.update_params({"f0method": "harvest"})
        assert out["f0method"] == "harvest"


# ---------- E-8：/switch 路径白名单 ----------

class TestE8SwitchWhitelist:
    def _setup_home(self, tmp_path, profiles):
        (tmp_path / "models" / "rvc").mkdir(parents=True)
        with open(tmp_path / "config.json", "w", encoding="utf-8") as f:
            json.dump({"voice_profiles": profiles}, f, ensure_ascii=False)

    def test_models_rvc_prefix_allowed(self, tmp_path, monkeypatch):
        self._setup_home(tmp_path, {})
        svc, _ = _make_svc(tmp_path, monkeypatch)
        p = tmp_path / "models" / "rvc" / "modelA.pth"
        assert svc._path_allowed(str(p)) is True

    def test_profile_model_allowed(self, tmp_path, monkeypatch):
        prof = r"D:\elsewhere\my_model.pth"
        self._setup_home(tmp_path, {"p1": {"rvc_model": prof}})
        svc, _ = _make_svc(tmp_path, monkeypatch)
        assert svc._path_allowed(prof) is True

    def test_outside_path_rejected(self, tmp_path, monkeypatch):
        self._setup_home(tmp_path, {"p1": {"rvc_model": ""}})
        svc, _ = _make_svc(tmp_path, monkeypatch)
        assert svc._path_allowed(r"C:\Windows\evil.pth") is False
        assert svc._path_allowed(r"C:\Users\x\Downloads\trap.pth") is False


# ---------- E-9：设备名解析 ----------

class TestE9DeviceResolve:
    def test_none_passthrough(self, tmp_path, monkeypatch, fake_sd):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        assert svc._resolve_device(None, True) is None

    def test_missing_device_three_elements(self, tmp_path, monkeypatch, fake_sd):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="设备不存在"):
            svc._resolve_device("不存在的麦克风 XYZ", True)


# ---------- E-11：状态暴露 ----------

class TestE11Status:
    def test_status_includes_last_error_and_algo_delay(self, tmp_path, monkeypatch, fake_sd):
        svc, _ = _make_svc(tmp_path, monkeypatch)
        st = svc.status()
        assert "last_error" in st and "algo_delay_ms" in st
        assert st["state"] == "idle" and st["algo_delay_ms"] == 0

    def test_last_error_survives_callback_failure(self, tmp_path, monkeypatch):
        if not HAS_TORCH:
            pytest.skip("主进程无 torch（张量路径在 RVC runtime 验证）")
        svc, mod = _make_svc(tmp_path, monkeypatch)

        svc._init_pipeline = lambda: None
        svc.channels = 2
        svc.block_frame = 480
        svc.zc = 48
        svc.sola_buffer_frame = 192
        svc.sola_search_frame = 48
        svc.block_frame_16k = 480
        svc.extra_frame = 0
        svc.skip_head = 0
        svc.return_length = 480
        import numpy as np
        svc.rms_buffer = np.zeros(4 * svc.zc, dtype="float32")
        svc.params["threhold"] = -30
        outdata = np.zeros((480, 2), dtype="float32")
        indata = np.zeros((480, 2), dtype="float32")
        import torch
        svc.config = types.SimpleNamespace(device="cpu")
        svc.input_wav = torch.zeros(1024)
        svc.input_wav_res = torch.zeros(1024)
        svc.rvc = types.SimpleNamespace(infer=lambda *a: (_ for _ in ()).throw(
            RuntimeError("infer boom")))
        svc.resampler = lambda x: x
        svc.resampler2 = None
        svc.audio_callback(indata, outdata, 480, None, None)
        assert "last_error" in svc.status()
