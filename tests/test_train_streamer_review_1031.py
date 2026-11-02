# -*- coding: utf-8 -*-
"""训练主播审查批次守卫测试（2026-10-31）

审查目标：训练出错/完成后正确推送通知 + 训练结果正确应用于数字人服务。
锁定修复：
- F1 全新全流程（含阶段 2）不被底模产物前置校验必拦；阶段 2 后动态发现+缺失即拦
- F2 部署目标与服务/素材管理同源（默认 wy，存量自定义仍生效）
- F3 跨角色部署数据联接自动重对齐（旧联接指向他角色数据→重建；真目录不动）
- F4 推送分级：部署失败=报警（非完成通知）；主动停止不发
- F6 进度文件 completed_stage 语义（done=stage 否则 stage-1）
- F7 config.example start_stage 残留键清理 + 退役登记
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.train_streamer.pipeline as pl  # noqa: E402
import utils.alarm_notifier as alarm_notifier  # noqa: E402
from utils.train_streamer.pipeline import TrainPipeline, resolve_character_dir_name  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 测试脚手架 ----------

def _make_pipeline(tmp_path, role="freshrole", stages=None, on_complete=None,
                   on_confirm=None):
    video_dir = tmp_path / "videos"
    (video_dir / "original_videos").mkdir(parents=True, exist_ok=True)
    return TrainPipeline(
        config_data={"edtalk_realtime": {"interpreter_path": sys.executable}},
        video_dir=str(video_dir), role_name=role, stages=stages,
        on_log=lambda line, stage=None: None,
        on_complete=on_complete or (lambda ok, msg: None),
        on_confirm_needed=on_confirm or (lambda: None),
    )


def _isolate_edtalk(tmp_path, monkeypatch):
    """edtalk_dir 隔离到 tmp（进度/锁/日志/ckpt 动态发现全部落 tmp）。"""
    ed = tmp_path / "edtalk"
    ed.mkdir(exist_ok=True)
    monkeypatch.setattr(pl, "get_edtalk_dir", lambda: str(ed))
    # preflight 副本存在性判据（阶段 1 子脚本占位）
    pp = ed / "data_preprocess" / "data_preprocess_for_train" / "run_preprocess.py"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("# stub", encoding="utf-8")
    return ed


def _patch_gpu_and_vram(monkeypatch):
    """GPU 冲突判据（端口探测拒连）+ 显存查询降级（不依赖 nvidia-smi）。"""
    import requests as _rq
    monkeypatch.setattr(_rq, "get",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr(TrainPipeline, "_query_vram_gb", lambda self: None)


def _capture_notify(monkeypatch):
    """拦截 notify_train_result（run() finally 调用点）与报警出口。"""
    notified = []
    monkeypatch.setattr(pl, "notify_train_result",
                        lambda *a, **k: notified.append((a, k)))
    return notified


def _run_full(monkeypatch, tmp_path, role="freshrole", with_ckpt=True,
              apply_impl=None):
    """全新全流程 {1,2,3,4}：子进程步骤 mock 成功，跑通 run() 收集断言素材。"""
    _isolate_edtalk(tmp_path, monkeypatch)
    _patch_gpu_and_vram(monkeypatch)
    monkeypatch.setattr(pl, "training_running", False)
    if with_ckpt:
        ck = tmp_path / "edtalk" / "ckpt_models" / role / "checkpoint"
        ck.mkdir(parents=True, exist_ok=True)
        (ck / "019999.pt").write_bytes(b"x")  # off-by-one：最大编号产物
    completed = []
    applied = []
    confirm_holder = {}

    def _on_confirm():
        confirm_holder["p"].confirm_continue()  # 模拟用户点"仍要继续"

    p = _make_pipeline(tmp_path, role=role, stages={1, 2, 3, 4},
                       on_complete=lambda ok, msg: completed.append((ok, msg)),
                       on_confirm=_on_confirm)
    confirm_holder["p"] = p
    monkeypatch.setattr(TrainPipeline, "_run_step",
                        lambda self, cmd, stage, cwd=None: True)
    monkeypatch.setattr(TrainPipeline, "_cleanup_after_step",
                        lambda self, proc, stage: None)

    def _fake_apply(role_name, character_dir, iter_count=30000, ckpt_rel=None):
        applied.append((role_name, character_dir, iter_count, ckpt_rel))
        if apply_impl:
            return apply_impl()
        return "mock-deploy-ok"

    monkeypatch.setattr(TrainPipeline, "apply_to_digital_human",
                        staticmethod(_fake_apply))
    notified = _capture_notify(monkeypatch)
    p.run()
    return p, completed, applied, notified


# ---------- F1：全新全流程不再被底模产物前置校验必拦 ----------

def test_fresh_full_run_reaches_deploy_and_success(tmp_path, monkeypatch):
    """一键全流程（1-4 全选，ckpt 尚不存在）必须跑完四阶段并部署成功。

    回归锁：原前置校验 `stages & {3,4}` 未排除阶段 2 同跑——全新训练在
    ckpt 未产出时即被"底模微调产物缺失"拦停（训练从未开始还推失败报警）。
    """
    p, completed, applied, notified = _run_full(monkeypatch, tmp_path)
    assert completed and completed[0][0] is True, completed
    assert "训练完成" in completed[0][1]
    # 阶段 2 后动态发现：部署消费的 ckpt_rel=最大编号产物（off-by-one）
    assert len(applied) == 1
    assert applied[0][2] == p.iter_count
    assert applied[0][3].endswith("019999.pt")
    # 成功路径推送完成通知（非报警、非部署失败）
    assert notified, "run() finally 必须推送训练结果"
    assert notified[0][0][0] is True
    assert notified[0][1].get("deploy_failed") is False


def test_fresh_run_stage2_ckpt_missing_aborts_before_eval(tmp_path, monkeypatch):
    """阶段 2 跑完仍无产物 → 动态发现即拦（不进评估静默空过）。"""
    p, completed, applied, notified = _run_full(
        monkeypatch, tmp_path, with_ckpt=False)
    assert completed[0][0] is False
    assert "底模微调产物缺失" in completed[0][1]
    assert applied == []  # 未触达部署
    assert notified[0][0][0] is False  # 失败报警
    prog = TrainPipeline.read_progress(p.role_name)
    assert prog["status"] == "failed" and prog["current_stage"] == 2


def test_resume_without_stage2_still_preflight_blocks(tmp_path, monkeypatch):
    """续训（勾 3/4 不勾 2）且产物缺失 → 前置校验仍拦截（原意图保留）。"""
    _isolate_edtalk(tmp_path, monkeypatch)
    _patch_gpu_and_vram(monkeypatch)
    monkeypatch.setattr(pl, "training_running", False)
    completed = []
    p = _make_pipeline(tmp_path, role="resumerole", stages={3, 4},
                       on_complete=lambda ok, msg: completed.append((ok, msg)))
    notified = _capture_notify(monkeypatch)
    p.run()
    assert completed[0][0] is False
    assert "底模微调产物缺失" in completed[0][1]


# ---------- F2：部署目标与服务/素材管理同源 ----------

def test_resolve_character_dir_name_defaults_wy():
    """character_dir 退役为内部量：缺省=wy（与启动参数、materials 同源）。"""
    assert resolve_character_dir_name({}) == "wy"
    assert resolve_character_dir_name({"edtalk_realtime": {}}) == "wy"
    assert resolve_character_dir_name({"edtalk_realtime": {"character_dir": ""}}) == "wy"


def test_resolve_character_dir_name_legacy_custom_value_applies():
    """存量自定义值仍生效（与 frontend/main.py 启动参数一致）。"""
    assert resolve_character_dir_name(
        {"edtalk_realtime": {"character_dir": "alice"}}) == "alice"


def test_deploy_targets_service_character_dir(tmp_path, monkeypatch):
    """部署调用收到的角色包目录名必须来自同源解析（默认 wy）。

    回归锁：原 fallback role_name——character_dir 键删除后产物部署到
    edtalk/<角色名>/，服务却加载 wy，训练结果永不生效。
    """
    _, _, applied, _ = _run_full(monkeypatch, tmp_path, role="somerole")
    assert applied[0][1] == "wy"


# ---------- F4：推送分级（部署失败=报警；主动停止不发） ----------

def test_notify_routing_success_notice(monkeypatch):
    calls = []
    monkeypatch.setattr(alarm_notifier, "alarm_notice",
                        lambda m, msg: calls.append(("notice", m, msg)))
    monkeypatch.setattr(alarm_notifier, "alarm_error",
                        lambda m, msg, fix_hint="": calls.append(("error", m, msg)))
    pl.notify_train_result(True, "训练完成（总耗时 1.0 小时）。")
    assert calls == [("notice", "train_streamer", "训练完成（总耗时 1.0 小时）。")]


def test_notify_routing_deploy_failed_is_alarm_with_fix_hint(monkeypatch):
    calls = []
    monkeypatch.setattr(alarm_notifier, "alarm_notice",
                        lambda m, msg: calls.append(("notice", m, msg)))

    def _err(m, msg, fix_hint=""):
        calls.append(("error", m, msg, fix_hint))
    monkeypatch.setattr(alarm_notifier, "alarm_error", _err)
    pl.notify_train_result(True, "训练完成。自动部署失败：x", deploy_failed=True)
    assert len(calls) == 1 and calls[0][0] == "error"
    assert calls[0][1] == "train_streamer"
    assert "兜底" in calls[0][3]  # 手动兜底指引（审查目标②）


def test_notify_routing_failure_alarm_and_stop_silent(monkeypatch):
    calls = []
    monkeypatch.setattr(alarm_notifier, "alarm_notice",
                        lambda m, msg: calls.append(("notice", m, msg)))
    monkeypatch.setattr(alarm_notifier, "alarm_error",
                        lambda m, msg, fix_hint="": calls.append(("error", m, msg)))
    pl.notify_train_result(False, "评估未过门槛已停止")
    assert calls[-1][0] == "error"
    pl.notify_train_result(True, "x", stop_requested=True)   # 主动停止不发
    pl.notify_train_result(False, "x", stop_requested=True)
    assert len(calls) == 1


def test_deploy_failure_end_to_end_pushes_deploy_failed(tmp_path, monkeypatch):
    """部署抛异常：训练完成 success=True，但推送带 deploy_failed=True。"""
    p, completed, applied, notified = _run_full(
        monkeypatch, tmp_path, apply_impl=lambda: (_ for _ in ()).throw(
            RuntimeError("mklink boom")))
    assert completed[0][0] is True  # 训练本身完成
    assert notified[0][1].get("deploy_failed") is True
    assert p._deploy_ok is False and "mklink boom" in p._deploy_msg


# ---------- F3：跨角色部署数据联接重对齐 ----------

def _mkjunction(link: str, target: str):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout


def _make_two_role_data(tmp_path):
    """构造 新角色 NEW / 旧角色 OLD 训练数据根 + 角色包骨架。"""
    data_root = tmp_path / "data" / "edtalk_train" / "NEW"
    old_root = tmp_path / "data" / "edtalk_train" / "OLD"
    for root in (data_root, old_root):
        for n in ("orifra", "count", "metadata", "facemark", "gaze_data"):
            (root / n).mkdir(parents=True, exist_ok=True)
    char_root = tmp_path / "char" / "wy"
    (char_root / "checkpoint").mkdir(parents=True, exist_ok=True)
    return char_root, data_root, old_root


def test_ensure_serve_data_realigns_cross_role_junction(tmp_path, monkeypatch):
    """旧联接指向他角色数据 → 自动重建对齐本次角色数据（新权重不配旧脸数据）。"""
    char_root, data_root, old_root = _make_two_role_data(tmp_path)
    monkeypatch.setattr(pl, "get_edtalk_dir", lambda: str(tmp_path / "edtalk"))
    # 旧部署残留：orifra 联接指向 OLD 角色数据
    _mkjunction(str(char_root / "orifra"), str(old_root / "orifra"))
    msg = TrainPipeline._ensure_serve_data(str(char_root), "NEW")
    assert "重对齐" in msg
    cur = TrainPipeline._norm_link_path(os.readlink(str(char_root / "orifra")))
    want = TrainPipeline._norm_link_path(str(data_root / "orifra"))
    assert cur == want  # 已指向本次角色数据
    # 其余子目录正常联接
    for n in ("count", "metadata", "facemark"):
        assert (char_root / n).is_dir()
    # 旧角色数据未被触碰（os.rmdir 只删联接）
    assert (old_root / "orifra").is_dir()


def test_ensure_serve_data_aligned_junction_skipped(tmp_path, monkeypatch):
    """联接已指向本次角色数据 → 跳过不动（幂等）。"""
    char_root, data_root, _ = _make_two_role_data(tmp_path)
    monkeypatch.setattr(pl, "get_edtalk_dir", lambda: str(tmp_path / "edtalk"))
    for n in ("orifra", "count", "metadata", "facemark", "gaze_data"):
        _mkjunction(str(char_root / n), str(data_root / n))
    before = os.readlink(str(char_root / "orifra"))
    msg = TrainPipeline._ensure_serve_data(str(char_root), "NEW")
    assert "已齐全" in msg
    assert os.readlink(str(char_root / "orifra")) == before


def test_ensure_serve_data_real_dir_untouched(tmp_path, monkeypatch):
    """真目录（用户手工资产）保持不动——不删不重建。"""
    char_root, _, _ = _make_two_role_data(tmp_path)
    monkeypatch.setattr(pl, "get_edtalk_dir", lambda: str(tmp_path / "edtalk"))
    marker = char_root / "orifra" / "keep.txt"
    (char_root / "orifra").mkdir(parents=True, exist_ok=True)
    marker.write_text("hand-made", encoding="utf-8")
    msg = TrainPipeline._ensure_serve_data(str(char_root), "NEW")
    assert marker.read_text(encoding="utf-8") == "hand-made"
    assert "已齐全" in msg or "已有" in msg


# ---------- F6：进度文件 completed_stage 语义 ----------

def test_progress_completed_stage_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "get_edtalk_dir", lambda: str(tmp_path))
    p = _make_pipeline(tmp_path)
    p.edtalk_dir = str(tmp_path)
    p._write_progress(2, "done")
    assert TrainPipeline.read_progress(p.role_name)["completed_stage"] == 2
    p._write_progress(3, "running")
    assert TrainPipeline.read_progress(p.role_name)["completed_stage"] == 2


# ---------- F7：退役键清理与登记（源码断言守卫） ----------

def test_guard_example_start_stage_removed():
    with open(os.path.join(_ROOT, "config.example.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    assert "start_stage" not in (cfg.get("train_streamer") or {})


def test_guard_retired_key_registered():
    with open(os.path.join(_ROOT, "frontend", "main.py"), encoding="utf-8") as f:
        src = f.read()
    assert '("train_streamer", "start_stage")' in src


def test_guard_pipeline_preflight_condition_and_same_source_deploy():
    with open(os.path.join(_ROOT, "utils", "train_streamer", "pipeline.py"),
              encoding="utf-8") as f:
        src = f.read()
    # F1：前置校验必须排除阶段 2 同跑场景
    assert "2 not in self.stages" in src
    # F2：旧 role_name fallback 移除，同源解析启用
    assert "or self.role_name" not in src
    assert "def resolve_character_dir_name" in src
    # F4：部署失败分级推送
    assert "deploy_failed" in src


def test_guard_ui_copy_retired_role_selection_removed():
    with open(os.path.join(_ROOT, "frontend", "ui", "tabs", "train_streamer.py"),
              encoding="utf-8") as f:
        src = f.read()
    # F5：画面设置页角色选择已退役，完成引导不得再指向该操作
    assert "选择该角色" not in src
    # F4：完成通知按部署结果分级
    assert "_deploy_ran" in src and "_deploy_ok" in src
