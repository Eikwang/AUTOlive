# -*- coding: utf-8 -*-
"""数字人模型统一管理守卫（2026-11-09 用户设计二轮）

语义：以角色名为单位只管理微调训练后的模型（ckpt_models/{role}/）；
中间文件（中间 iter ckpt/预览图/tfevents/口型时间戳目录）训练收尾自动清
（cleanup_training_intermediates）；切换校验=服务消费面（部署数据包）；
删除=角色全链（模型+部署数据包+训练工作区+日志残余+进度），current 保护。
UI：train_streamer 只删不切（切换归画面设置 visual_body 角色下拉+服务自动重启）。
"""
import os
import sys
import _winapi

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.digital_human_models import (
    _dir_size,
    cleanup_training_intermediates,
    delete_digital_human_model,
    list_digital_human_models,
    validate_switch,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk(tmp_path):
    """假环境（两套大小写：数据包 wy / 训练产物 WY）：
    - ckpt_models/WY/checkpoint/{000000,005000,019999}.pt（2 中间+1 最终）
      + step_000.jpg/step_5000.jpg（预览图）+ log/tfevents（训练日志）
    - logs/Audio2Lip/WY/20261016_0903/checkpoint/x.pt（口型微调时间戳目录）
    - 部署数据包 edtalk/wy（checkpoint/new.pt+audio2lip.pt+junction×5）
    - 训练工作区 data/edtalk_train/WY（orifra/seg01 实体帧）
    """
    ed = tmp_path / "edtalk"
    train = tmp_path / "data" / "edtalk_train"
    # 训练结果（模型目录）——中间+最终
    ck = ed / "ckpt_models" / "WY" / "checkpoint"
    ck.mkdir(parents=True)
    (ck / "000000.pt").write_bytes(b"x" * (6 * 1024 * 1024))
    (ck / "005000.pt").write_bytes(b"x" * (6 * 1024 * 1024))
    (ck / "019999.pt").write_bytes(b"final")
    (ck / "step_000.jpg").write_bytes(b"p" * 1024)
    (ck / "step_5000.jpg").write_bytes(b"p" * 1024)
    (ed / "ckpt_models" / "WY" / "log").mkdir()
    (ed / "ckpt_models" / "WY" / "log" / "tfevents.0").write_bytes(b"t")
    # 口型微调时间戳目录（中间）
    a2l_ts = ed / "logs" / "Audio2Lip" / "WY" / "20261016_0903" / "checkpoint"
    a2l_ts.mkdir(parents=True)
    (a2l_ts / "x.pt").write_bytes(b"a" * 1024)
    # 部署数据包（checkpoint 权重+junction → 工作区）
    pkg = ed / "wy"
    (pkg / "checkpoint").mkdir(parents=True)
    (pkg / "checkpoint" / "new.pt").write_bytes(b"gen")
    (pkg / "checkpoint" / "audio2lip.pt").write_bytes(b"a2l")
    ws = train / "WY"
    for sub in ("orifra", "count", "metadata", "facemark", "gaze_data"):
        (ws / sub).mkdir(parents=True)
    (ws / "orifra" / "seg01").mkdir()
    (ws / "orifra" / "seg01" / "f.jpg").write_bytes(b"x" * 1024)
    for sub in ("orifra", "count", "metadata", "facemark", "gaze_data"):
        _winapi.CreateJunction(str(ws / sub), str(pkg / sub))
    config = {"edtalk_realtime": {"character_dir": "wy"}}
    return ed, config


def test_list_model_view_union_and_current(tmp_path):
    """清单=模型视角：角色 union 归并（ckpt_models/数据包/工作区 casefold）、
    deployed/current/中间文件量化；不含素材帧大小。"""
    ed, config = _mk(tmp_path)
    data = list_digital_human_models(config, edtalk_root=str(ed))
    assert data["current"] == "wy"
    assert len(data["models"]) == 1, data["models"]
    m = data["models"][0]
    assert m["role"].casefold() == "wy" and m["current"] is True
    assert m["deployed"] is True and m["generator_ok"] is True
    assert m["audio2lip_ok"] is False, "统一落位前 ckpt_models 无口型权重"
    assert m["cleanup_pending"] is True and m["intermediate_count"] >= 5
    assert m["workspace_exists"] is True
    assert m["model_gb"] > 0
    # 实体大小不含 junction（工作区实体不计入模型目录）
    assert _dir_size(m["model_dir"]) < 15 * 1024 * 1024


def test_cleanup_training_intermediates_keeps_final(tmp_path):
    """训练收尾清理：中间 iter/预览图/tfevents/口型时间戳目录全清，
    最终 iter 底模保留；idempotent（再跑零动作不炸）。"""
    ed, config = _mk(tmp_path)
    r = cleanup_training_intermediates("WY", edtalk_dir=str(ed))
    assert r["ok"] and r["freed_mb"] > 2, r
    ck = ed / "ckpt_models" / "WY" / "checkpoint"
    assert sorted(f.name for f in ck.iterdir()) == ["019999.pt"], \
        "角色 checkpoint 只应保留最终底模"
    assert not (ed / "ckpt_models" / "WY" / "log").exists(), "tfevents 应清"
    assert not (ed / "logs" / "Audio2Lip" / "WY").exists(), "口型时间戳目录应清"
    # 幂等
    r2 = cleanup_training_intermediates("WY", edtalk_dir=str(ed))
    assert r2["ok"] and r2["removed"] == []


def test_validate_switch_requires_deployed_package(tmp_path):
    """切换校验=服务消费面（部署数据包）；未部署角色拒绝、current 空串。"""
    ed, config = _mk(tmp_path)
    assert validate_switch(config, "wy", edtalk_root=str(ed)) == ""
    assert validate_switch(config, "WY", edtalk_root=str(ed)) == ""  # 大小写
    assert "尚未部署" in validate_switch(config, "ghost", edtalk_root=str(ed))


def test_delete_full_chain_independent_junction_target(tmp_path):
    """删除角色全链：模型目录+部署数据包（junction 先摘）+训练工作区
    （casefold wy↔WY）+口型残余+进度；current 保护拒绝。"""
    ed, config = _mk(tmp_path)
    # 保护：current（wy）拒绝
    r0 = delete_digital_human_model(config, "wy", edtalk_root=str(ed))
    assert r0["ok"] is False and "切换" in r0["error"]
    assert (ed / "wy" / "checkpoint" / "new.pt").exists()
    # 切走后可删
    config["edtalk_realtime"]["character_dir"] = "other"
    r = delete_digital_human_model(config, "wy", edtalk_root=str(ed))
    assert r["ok"], r
    assert not (ed / "wy").exists(), "部署数据包应整删"
    assert not (ed / "ckpt_models" / "WY").exists(), "模型目录应删（casefold）"
    assert not (tmp_path / "data" / "edtalk_train" / "WY").exists(), "工作区应独立删"
    assert not (ed / "logs" / "Audio2Lip" / "WY").exists(), "口型残余应删"
    assert not (ed / "train_progress" / "WY.json").exists()
    assert r["freed_gb"] > 0


def test_preprocess_progress_visible_in_pipe():
    """断联/假卡死审查守卫（2026-11-09 用户两度中止实报）：tqdm disable=None
    在非 tty（webui 管道）下自动静默=预处理全程零进度可见。crop_video_HDTF
    必须强制输出（disable=False）——训练管线按块读（\r 算行分隔）实时接住。"""
    src = open(os.path.join(ROOT, "edtalk", "data_preprocess",
                            "data_preprocess_for_train", "crop_video_HDTF.py"),
               encoding="utf-8").read()
    assert "disable=None" not in src, "tqdm disable=None 在管道下静默=零进度可见（假卡死）"
    assert "disable=False" in src


def test_ui_train_streamer_delete_only_visual_body_select(tmp_path):
    """UI 接线守卫：train_streamer 只删不切（无启用按钮）；visual_body 角色
    下拉+服务自动重启（APP_HOLDER→stop_program+重新拉起）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ts = open(os.path.join(root, "frontend", "ui", "tabs", "train_streamer.py"),
              encoding="utf-8").read()
    for needle in ("数字人模型管理", "list_digital_human_models",
                   "delete_digital_human_model", "cleanup_pending",
                   "asyncio.to_thread", "_confirm_delete"):
        assert needle in ts, f"train_streamer 区块缺 {needle}"
    assert "validate_switch" not in ts, "切换职责已移至画面设置——训练页不应有启用/校验"
    vb = open(os.path.join(root, "frontend", "ui", "tabs", "visual_body.py"),
              encoding="utf-8").read()
    for needle in ("数字人角色", "_switch_role", "validate_switch",
                   "APP_HOLDER", "stop_program", "_start_edtalk_realtime"):
        assert needle in vb, f"visual_body 缺 {needle}"
    fm = open(os.path.join(root, "frontend", "main.py"), encoding="utf-8").read()
    assert "APP_HOLDER" in fm and 'APP_HOLDER["app"] = self' in fm
    # 断联修复守卫（2026-11-09 用户实报：进训练主播页重置回初始页——构建 5.19s
    # 阻塞事件循环超 socket.io ping_timeout=2.0s）：tab 模块后台预热接线
    assert "_prewarm_tab_modules" in fm, "缺 tab 模块后台预热（进页 import 链阻塞事件循环）"
    assert "tab-prewarm" in fm, "预热线程应在启动路径挂载（daemon 线程）"
    pl = open(os.path.join(root, "utils", "train_streamer", "pipeline.py"),
              encoding="utf-8").read()
    assert "cleanup_training_intermediates" in pl, "训练收尾应接线中间文件清理"
    assert "ckpt_models", "audio2lip 统一落位" in pl or "unified" in pl
