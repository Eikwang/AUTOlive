# -*- coding: utf-8 -*-
"""TST-1b：R/C 面单测（/autoplan 2026-10-14 eng 测试计划断言 15-20）。

-oformat 回归/参数链端点契约/翻唱音高直传/档案选型契约+基根/失配隔离/
cover_profile_id 持久化键。R-1 的 rtrvc 升级与 /params 值域校验同测。
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# torch 为主环境缺席的重依赖——RVC 推理组件由独立运行时解释器承载（EDTalk runtime），
# 与仓库其他 RVC 测试的 importorskip 守卫惯例一致，主环境整文件跳过
pytest.importorskip("torch", reason="主环境无 torch，RVC 推理依赖独立运行时解释器")

from utils.cover_worker import CoverQueue  # noqa: E402
from utils.rvc_realtime_service import RealtimeVC  # noqa: E402


def _queue(tmp_path, cfg_extra=None):
    cfg = MagicMock()
    data = {"choose_song": {"song_path": str(tmp_path / "song"),
                            "cover_pitch": 0},
            "paths": {"aicovergen_root": str(tmp_path / "aicovergen")},
            "rvc_vc": {"runtime_python": str(tmp_path / "py.exe")}}
    data.update(cfg_extra or {})
    cfg.get = lambda *keys: _nested(data, keys)
    return CoverQueue(cfg, None)


def _nested(d, keys):
    node = d
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


# ---------- 断言 15：-oformat 回归 ----------


def test_dispatch_has_oformat_wav(tmp_path):
    """C-1 回归：派发必须含 -oformat wav（旧默认 mp3→finder 落库中间干人声 bug）。"""
    (tmp_path / "x.mp3").write_bytes(b"a")  # ffprobe 文件存在性桩（缺则 _process 提前 return）
    q = _queue(tmp_path)
    job = {"song": "x", "song_path": str(tmp_path / "x.mp3"), "cover_path": "c.wav",
           "profile_id": "p", "retries_left": 0, "started_at": None}
    cmds = {}

    def fake_popen(cmd, **kw):
        cmds["cmd"] = [str(x) for x in cmd]

        class P:
            pid = 1
            stdout = iter([])

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        return P()

    # GPU 门=函数内 import → patch 来源模块；模型准备打桩（cmd 在其后构造）
    import utils.gpu_policy as gp
    with patch("utils.cover_worker.subprocess.Popen", fake_popen), \
         patch.object(gp, "is_background_task_allowed", lambda *a, **k: (True, "")), \
         patch.object(CoverQueue, "_prepare_rvc_model_dir", lambda self, job: None), \
         patch.object(CoverQueue, "_find_produced_wav", lambda self, job: None):
        q._process(job, {"vram_reserve_gb": 4, "job_timeout_min": 1})
    assert "-oformat" in cmds.get("cmd", []), f"派发缺 -oformat: {cmds.get('cmd')}"
    idx = cmds["cmd"].index("-oformat")
    assert cmds["cmd"][idx + 1] == "wav"


# ---------- 断言 17：音高直传（outside 发现 1 回归） ----------


def test_pitch_passthrough_no_pre_multiply(tmp_path):
    """用户 1 八度→派发 `-p 1` 直传；不得预乘 12（AICoverGen 内部 ×12）；-12~12 钳制。"""
    import utils.gpu_policy as gp
    for cfg_pitch, expect in ((1, "1"), (12, "12"), (-3, "-3"), (99, "12"), (None, "0")):
        (tmp_path / "x.mp3").write_bytes(b"a")
        q = _queue(tmp_path, {"choose_song": {"cover_pitch": cfg_pitch,
                                              "song_path": str(tmp_path / "song")}})
        job = {"song": "x", "song_path": str(tmp_path / "x.mp3"), "cover_path": "c.wav",
               "profile_id": "p", "retries_left": 0, "started_at": None}
        cmds = {}

        def fake_popen(cmd, **kw):
            cmds["cmd"] = [str(x) for x in cmd]

            class P:
                pid = 1
                stdout = iter([])

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

            return P()

        with patch("utils.cover_worker.subprocess.Popen", fake_popen), \
             patch.object(gp, "is_background_task_allowed", lambda *a, **k: (True, "")), \
             patch.object(CoverQueue, "_prepare_rvc_model_dir", lambda self, job: None), \
             patch.object(CoverQueue, "_find_produced_wav", lambda self, job: None):
            q._process(job, {"vram_reserve_gb": 4, "job_timeout_min": 1})
        cmd = cmds["cmd"]
        i = cmd.index("-p")
        assert cmd[i + 1] == expect, f"cover_pitch={cfg_pitch}→-p {cmd[i+1]}（期望 {expect}）"
    assert not any(x == "-pall" for x in cmds["cmd"]), "不得产生 -pall（sox 未装）"


# ---------- 断言 18：档案选型契约+基根 ----------


def test_models_root_follows_aicovergen_root(tmp_path):
    """C-2 回归：_rvc_models_root 基根=aicovergen 根（写读两根不相交 bug 修复）。"""
    q = _queue(tmp_path)
    root = q._rvc_models_root()
    assert root == os.path.join(str(tmp_path / "aicovergen"), "rvc_models"), root
    assert "RVC20240604Nvidia" not in root, "不得再派生自 rvc_vc.rvc_root"


def test_share_channel_reads_profile_then_scan(tmp_path):
    """翻唱模型对来源：档案 rvc_model/rvc_index 优先→models/rvc/ 扫描兜底。"""
    q = _queue(tmp_path)
    # ① 档案字段优先（get_profile=函数内 import → patch 来源模块）
    with patch("utils.voice_profiles.get_profile",
               return_value={"rvc_model": str(tmp_path / "a.pth"),
                             "rvc_index": str(tmp_path / "a.index")}):
        (tmp_path / "a.pth").write_bytes(b"m")
        (tmp_path / "a.index").write_bytes(b"i")
        q._prepare_rvc_model_dir({"profile_id": "p"})
    shared = q._rvc_models_root()
    assert os.path.isfile(os.path.join(shared, "autolive_p", "p.pth"))
    # ② 档案空→models/rvc/ 兜底（autolive_home/models/rvc 结构）
    (tmp_path / "home" / "models" / "rvc").mkdir(parents=True)
    (tmp_path / "home" / "models" / "rvc" / "wy.pth").write_bytes(b"m")
    q2 = _queue(tmp_path, {"paths": {"aicovergen_root": str(tmp_path / "aicovergen"),
                                     "autolive_home": str(tmp_path / "home")}})
    with patch("utils.voice_profiles.get_profile", return_value=None):
        q2._prepare_rvc_model_dir({"profile_id": "q"})
    assert os.path.isfile(os.path.join(q2._rvc_models_root(), "autolive_q", "q.pth")), \
        "兜底必须命中 models/rvc/ 第一个 .pth（DX-5 预取路径）"


# ---------- 断言 16/19/20：参数链契约 ----------


def _vc(tmp_path):
    """构造 RealtimeVC（mock 掉 chdir/config 依赖，只测 update_params 逻辑）。"""
    vc = RealtimeVC.__new__(RealtimeVC)
    vc.params = {"pitch": 0, "formant": 0.0, "index_rate": 0.75, "protect": 0.33,
                 "rms_mix_rate": 0.25, "f0method": "rmvpe", "threhold": -60}
    vc._is_rtrvc = True
    vc.rvc = None
    return vc


def test_update_params_value_domain():
    vc = _vc(None)
    out = vc.update_params({"pitch": 5, "formant": -1.5, "protect": 0.5})
    assert out["pitch"] == 5 and out["formant"] == -1.5 and out["protect"] == 0.5
    with pytest.raises(ValueError):  # 超域拒绝
        vc.update_params({"pitch": 99})
    with pytest.raises(ValueError):  # 未知键拒绝（E-1 后 block_time 已是合法管线键，改用真未知键）
        vc.update_params({"no_such_key": 0.5})
    with pytest.raises(ValueError):  # enum 拒绝
        vc.update_params({"f0method": "harvest42"})


def test_update_params_maps_to_rtrvc_hot_methods():
    """pitch/formant/index_rate 必须映射 change_key/change_formant/change_index_rate
    （R-2 参数链闭合——旧 bug：前端改值服务端不消费）。"""
    vc = _vc(None)
    calls = []

    class FakeRVC:
        def change_key(self, v):
            calls.append(("key", v))

        def change_formant(self, v):
            calls.append(("formant", v))

        def change_index_rate(self, v):
            calls.append(("index_rate", v))

    vc.rvc = FakeRVC()
    vc.update_params({"pitch": 3, "formant": 0.5, "index_rate": 0.9, "protect": 0.4})
    assert ("key", 3) in calls and ("formant", 0.5) in calls and ("index_rate", 0.9) in calls
    assert not any(c[0] == "protect" for c in calls), "protect 无热更新方法（下次切换生效），不得误映射"


def test_status_reports_infer_class():
    """R-1 可观测：status 暴露 infer_class（rtrvc/legacy）——runtime 迁移验证锚点。"""
    vc = _vc(None)
    vc.stream = None
    vc.current_model = None
    vc.last_infer_time = 0
    st = vc.status()
    assert st["infer_class"] == "rtrvc"
