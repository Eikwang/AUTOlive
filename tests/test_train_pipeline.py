# -*- coding: utf-8 -*-
"""训练编排单测（Eng 任务 T2：4 条新 codepath）

覆盖：preflight 校验链 / 步骤编排失败即停 / 进度文件读写容错 / 评估门槛判定
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.train_streamer.pipeline import (  # noqa: E402
    AUDIO2MOUTH_DEFAULTS,
    build_audio2mouth_args,
    validate_role_name,
    TrainPipeline,
)


def _make_pipeline(tmp_path, role="testrole"):
    video_dir = tmp_path / "videos"
    (video_dir / "original_videos").mkdir(parents=True, exist_ok=True)
    return TrainPipeline(
        config_data={"edtalk_realtime": {"interpreter_path": str(tmp_path / "python.exe")},
                     "train_streamer": {"fine_tune_iter": 30000}},
        video_dir=str(video_dir),
        role_name=role,
        on_log=lambda line, stage=None: None,
        on_complete=lambda ok, msg: None,
    )


# ---------- 1. preflight 校验链 ----------


def test_preflight_missing_dir(tmp_path):
    """目录不存在 → 报错。"""
    p = TrainPipeline(
        config_data={"edtalk_realtime": {}}, video_dir=str(tmp_path / "nope"),
        role_name="x",
    )
    assert "不存在" in p.preflight()


def test_preflight_missing_original_videos(tmp_path):
    """缺 original_videos 子目录 → 目录结构不符提示（problem/cause/fix 三段）。"""
    d = tmp_path / "empty"
    d.mkdir()
    p = TrainPipeline(
        config_data={"edtalk_realtime": {}}, video_dir=str(d), role_name="x",
    )
    msg = p.preflight()
    assert "original_videos" in msg and "problem=" in msg


def test_preflight_missing_interpreter(tmp_path):
    """解释器缺失 → 三段式含修复动作（D7 义务+F3 报错验证）。"""
    video_dir = tmp_path / "videos"
    (video_dir / "original_videos").mkdir(parents=True)
    p = TrainPipeline(
        config_data={"edtalk_realtime": {"interpreter_path": str(tmp_path / "nope.exe")}},
        video_dir=str(video_dir), role_name="x",
    )
    msg = p.preflight()
    assert "problem=" in msg and "cause=" in msg and "fix=" in msg  # 三段式（D7）
    assert "解释器" in msg and ("解释器路径" in msg or "interpreter_path" in msg)


# ---------- 2. 步骤编排：失败即停 ----------


def test_run_step_failure_stops(tmp_path, monkeypatch):
    """子进程非零退出 → _run_step 返回 False 且记录 exit code。"""
    p = _make_pipeline(tmp_path)
    logs = []
    p.on_log = lambda line, stage=None: logs.append(line)

    class FakeProc:
        def __init__(self, *a, **kw):
            self.stdout = iter([])
            self.pid = 123

        def wait(self):
            return 1  # 非零退出

    import utils.train_streamer.pipeline as mod
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: FakeProc())
    ok = p._run_step(["fake-cmd"], 1)
    assert ok is False
    assert any("exit 1" in line for line in logs)


def test_run_step_user_stop(tmp_path, monkeypatch):
    """用户停止标志置位 → 步骤不再执行。"""
    p = _make_pipeline(tmp_path)
    p._stop_requested = True
    ok = p._run_step(["fake-cmd"], 1)
    assert ok is False


# ---------- 3. 进度文件读写容错 ----------


def test_progress_roundtrip(tmp_path, monkeypatch):
    """进度文件写入后可读回。"""
    import utils.train_streamer.pipeline as mod
    monkeypatch.setattr(mod, "get_edtalk_dir", lambda: str(tmp_path))
    p = _make_pipeline(tmp_path)
    p.edtalk_dir = str(tmp_path)
    p._write_progress(2, "running")
    prog = TrainPipeline.read_progress("testrole")
    assert prog is not None
    assert prog["current_stage"] == 2
    assert prog["status"] == "running"


def test_progress_corrupt_returns_none(tmp_path, monkeypatch):
    """进度文件损坏 → 返回 None（native F9：按无进度处理）。"""
    import utils.train_streamer.pipeline as mod
    monkeypatch.setattr(mod, "get_edtalk_dir", lambda: str(tmp_path))
    p = _make_pipeline(tmp_path)
    p.edtalk_dir = str(tmp_path)
    prog_dir = tmp_path / "train_progress"
    prog_dir.mkdir(exist_ok=True)
    (prog_dir / "testrole.json").write_text("{broken json!!", encoding="utf-8")
    assert TrainPipeline.read_progress("testrole") is None
    # 不存在的角色同样返回 None
    assert TrainPipeline.read_progress("nobody") is None


# ---------- 4. 评估门槛判定 ----------


def test_judge_eval_pass():
    """实测字段（jsonl metrics 嵌套）全过门槛 → None（通过）。"""
    pipeline = TrainPipeline(
        config_data={"edtalk_realtime": {}}, video_dir=".", role_name="x",
    )
    parsed = {
        "base": {"bg_ssim": 0.9489},
        "candidate": {"pose_keep_mae_px": 7.0, "paste_proxy_mae_px": 1.8,
                      "bg_ssim": 0.9676, "lip_open_ratio_vs_ref": 0.98},
    }
    assert pipeline._judge_eval(parsed) is None


def test_judge_eval_fail_reasons():
    """超门槛原因聚合（姿势MAE 超限 + SSIM 降幅超限）。"""
    pipeline = TrainPipeline(
        config_data={"edtalk_realtime": {}}, video_dir=".", role_name="x",
    )
    parsed = {
        "base": {"bg_ssim": 0.99},
        "candidate": {"pose_keep_mae_px": 15.0, "bg_ssim": 0.90,
                      "lip_open_ratio_vs_ref": 0.98},
    }
    reasons = pipeline._judge_eval(parsed)
    assert reasons is not None
    assert "MAE" in reasons and "SSIM" in reasons


def test_judge_eval_unparseable_defers_to_human():
    """无指标 → 交人工判断（返回提示字符串而非 None）。"""
    pipeline = TrainPipeline(
        config_data={"edtalk_realtime": {}}, video_dir=".", role_name="x",
    )
    assert pipeline._judge_eval({"base": {}, "candidate": {}}) is not None


# ---------- 5. argv 签名映射 ----------


def test_audio2mouth_args_match_official_recipe():
    """口型微调默认配方参数与 EDTalk 记忆一致（防回归；终门 uc2b：epoch 默认 2）。"""
    args = " ".join(build_audio2mouth_args({}))
    assert "--lip_dim_weight 5.0" in args
    assert "--mouth_vgg_weight 0.2" in args
    assert "--smooth_weight 0.2" in args
    assert "--epoch 2" in args
    assert "--audio_encoder_unfreeze_from 9" in args
    assert "--preload_features" in args


# ---------- 高级参数与校验（2026-10-15 义务块新增） ----------


def test_default_epoch_is_2_uc2b():
    """终门 uc2b：epoch 默认 2（用户裁决，覆盖实践命令 3）。"""
    assert AUDIO2MOUTH_DEFAULTS["epoch"] == 2
    args = build_audio2mouth_args({})
    assert "--epoch 2" in " ".join(args)


def test_form_override_epoch_3():
    """表单覆盖：epoch=3 进命令行（dict(DEFAULTS) 合并语义）。"""
    args = " ".join(build_audio2mouth_args({"epoch": 3}))
    assert "--epoch 3" in args


def test_param_range_reject():
    """Eng F-E8：范围校验拒绝非法值。"""
    import pytest
    with pytest.raises(ValueError):
        build_audio2mouth_args({"epoch": 99})
    with pytest.raises(ValueError):
        build_audio2mouth_args({"batch_size": 0})


def test_role_name_validation():
    """design D4：角色名校验（目录名安全）。"""
    assert validate_role_name("wy") is None
    assert validate_role_name("wy_v2") is None
    assert validate_role_name("") is not None
    assert validate_role_name("张三") is not None
    assert validate_role_name("a/b") is not None
    assert validate_role_name("CON") is not None
    assert validate_role_name("x" * 33) is not None


# ---------- 5. 防重入自锁回归（2026-10-15 实跑修复） ----------


def test_run_preflight_fail_keeps_running_flag_false(tmp_path, monkeypatch):
    """preflight 失败 → training_running 不被置位 + on_complete 仅一次。

    防重入自锁修复：原先 run() 先置 training_running=True 再 preflight，
    而 preflight 首条判据就是 training_running——每次启动训练必被自己
    拦住"已有训练任务在进行中"（实跑必现）。
    """
    import utils.train_streamer.pipeline as pl
    p = _make_pipeline(tmp_path)
    calls = []
    monkeypatch.setattr(TrainPipeline, "preflight",
                        lambda self: "problem=模拟前置失败；cause=测试；fix=忽略")
    monkeypatch.setattr(p, "on_complete", lambda ok, msg: calls.append((ok, msg)),
                        raising=False)
    monkeypatch.setattr(pl, "training_running", False)
    p.run()
    assert pl.training_running is False
    # 单次回调且带前置校验原因（原先 preflight 失败被回调两次：err+"训练未完成"）
    assert calls == [(False, "problem=模拟前置失败；cause=测试；fix=忽略")]


def test_preflight_blocks_when_running_flag_true(tmp_path, monkeypatch):
    """training_running=True（真实并发场景）→ preflight 防重入判据本身有效。"""
    import utils.train_streamer.pipeline as pl
    monkeypatch.setattr(pl, "training_running", True)
    p = _make_pipeline(tmp_path)
    assert "已有训练任务在进行中" in p.preflight()
