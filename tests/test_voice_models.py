# -*- coding: utf-8 -*-
"""声音模型统一管理守卫（2026-11-07 模型管理批次）

数据层（utils/voice_models）：清单配对/引用聚合/current 保护/全链删除/档案联动；
UI 收口：tts.py 删除按钮退役（只选不删）、train_voice.py 管理区块接线。
patch 纪律（同日 GSV 教训）：patch 目标=绑定处命名空间（utils.voice_models.X）。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.voice_models import delete_voice_model, list_voice_models


def _mk(tmp_path):
    """假环境：home=GSV 权重对×2 + RVC 全链×1；返回 (home, config, profiles)。"""
    home = tmp_path / "home"
    gpt_w = home / "apps" / "gpt-sovits" / "GPT_weights_v2ProPlus"
    vits_w = home / "apps" / "gpt-sovits" / "SoVITS_weights_v2ProPlus"
    gpt_w.mkdir(parents=True)
    vits_w.mkdir(parents=True)
    (gpt_w / "WY-e20.ckpt").write_bytes(b"k" * 1024)
    (vits_w / "WY_e10_s2870.pth").write_bytes(b"v" * 1024)
    (gpt_w / "solo-e10.ckpt").write_bytes(b"s" * 1024)  # 孤儿半对（仅 ckpt）
    rvc_dir = home / "models" / "rvc"
    rvc_dir.mkdir(parents=True)
    (rvc_dir / "wy.pth").write_bytes(b"p" * 2048)
    (rvc_dir / "wy.index").write_bytes(b"i" * 2048)
    (rvc_dir / "bare.pth").write_bytes(b"b" * 512)  # 无 index 配对
    (home / "apps" / "rvc" / "assets" / "weights").mkdir(parents=True)
    (home / "apps" / "rvc" / "assets" / "weights" / "wy.pth").write_bytes(b"w" * 2048)
    (home / "apps" / "rvc" / "assets" / "indices").mkdir(parents=True)
    (home / "apps" / "rvc" / "assets" / "indices" / "added_IVF100_wy_v2.index").write_bytes(b"x" * 2048)
    cover_dir = home / "apps" / "aicovergen" / "rvc_models" / "autolive_wy"
    cover_dir.mkdir(parents=True)
    (cover_dir / "wy.pth").write_bytes(b"c" * 512)
    profiles = {
        "WY": {"id": "WY", "name": "WY", "gpt_model": str(gpt_w / "WY-e20.ckpt"),
               "sovits_model": str(vits_w / "WY_e10_s2870.pth"),
               "rvc_model": str(rvc_dir / "wy.pth"), "rvc_index": str(rvc_dir / "wy.index")},
        "bare_ref": {"id": "bare_ref", "name": "b", "gpt_model": "", "sovits_model": "",
                     "rvc_model": str(rvc_dir / "bare.pth"), "rvc_index": ""},
    }
    config = {"paths": {"autolive_home": str(home)},
              "gpt_sovits": {"params": {"gpt_model_path": str(gpt_w / "WY-e20.ckpt"),
                                        "sovits_model_path": str(vits_w / "WY_e10_s2870.pth")}}}
    return home, config, profiles


def test_list_gsv_pairs_refs_and_current(tmp_path):
    """GSV 清单：实验名配对/孤儿半对/引用聚合/params current 标记。"""
    home, config, profiles = _mk(tmp_path)
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile"):
        data = list_voice_models(config, gsv_root=str(home / "apps" / "gpt-sovits"))
    gsv = {g["exp"]: g for g in data["gsv"]}
    assert "WY" in gsv and "solo" in gsv
    assert gsv["WY"]["ckpt"].endswith("WY-e20.ckpt") and gsv["WY"]["pth"].endswith("_e10_s2870.pth")
    assert gsv["WY"]["current"] is True, "params 指向的实验须标记 current（保护面）"
    assert gsv["WY"]["ref_profiles"] == ["WY"]
    assert gsv["solo"]["pth"] == "" and gsv["solo"]["current"] is False
    rvc = {r["exp"]: r for r in data["rvc"]}
    assert rvc["wy"]["index"] != "" and rvc["bare"]["index"] == ""


def test_delete_gsv_group_and_unlink_profiles(tmp_path):
    """GSV 删除：权重对全删+引用档案字段联动清空（档案保留）。"""
    home, config, profiles = _mk(tmp_path)
    config["gpt_sovits"]["params"]["gpt_model_path"] = str(
        home / "apps" / "gpt-sovits" / "GPT_weights_v2ProPlus" / "solo-e10.ckpt")
    config["gpt_sovits"]["params"]["sovits_model_path"] = ""
    upserts = []
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile", side_effect=lambda p: upserts.append(p)):
        r = delete_voice_model("gsv", "WY", config,
                               gsv_root=str(home / "apps" / "gpt-sovits"))
    assert r["ok"] and len(r["deleted"]) == 2, r
    assert not (home / "apps" / "gpt-sovits" / "GPT_weights_v2ProPlus" / "WY-e20.ckpt").exists()
    merged = next(p for p in upserts if p["id"] == "WY")
    assert merged["gpt_model"] == "" and merged["sovits_model"] == ""
    assert merged["rvc_model"] == profiles["WY"]["rvc_model"], "GSV 删除不得碰 RVC 字段"


def test_delete_gsv_protected_when_current(tmp_path):
    """GSV 保护：params（冷启动当前）指向的实验删除被拒绝且文件不动。"""
    home, config, profiles = _mk(tmp_path)
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile"):
        r = delete_voice_model("gsv", "WY", config,
                               gsv_root=str(home / "apps" / "gpt-sovits"))
    assert r["ok"] is False and "切换" in r["error"]
    assert (home / "apps" / "gpt-sovits" / "GPT_weights_v2ProPlus" / "WY-e20.ckpt").exists()
    # 非当前实验可删
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile"):
        r2 = delete_voice_model("gsv", "solo", config,
                                gsv_root=str(home / "apps" / "gpt-sovits"))
    assert r2["ok"] and r2["deleted"], "非 current 实验正常删除"


def test_delete_rvc_full_chain(tmp_path):
    """RVC 删除：四面全链（主件/训练终态/特征索引/翻唱消费面）+档案联动。"""
    home, config, profiles = _mk(tmp_path)
    upserts = []
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile", side_effect=lambda p: upserts.append(p)):
        r = delete_voice_model("rvc", "wy", config)
    assert r["ok"] and len(r["deleted"]) == 5, r  # pth+index+weights+added_index+autolive_wy/
    for gone in (home / "models" / "rvc" / "wy.pth",
                 home / "models" / "rvc" / "wy.index",
                 home / "apps" / "rvc" / "assets" / "weights" / "wy.pth",
                 home / "apps" / "rvc" / "assets" / "indices" / "added_IVF100_wy_v2.index"):
        assert not gone.exists(), gone
    assert not (home / "apps" / "aicovergen" / "rvc_models" / "autolive_wy").exists()
    assert (home / "models" / "rvc" / "bare.pth").exists(), "其它实验不受影响"
    merged = next(p for p in upserts if p["id"] == "WY")
    assert merged["rvc_model"] == "" and merged["rvc_index"] == ""
    assert merged["gpt_model"] == profiles["WY"]["gpt_model"], "RVC 删除不得碰 GSV 字段"


def test_delete_missing_experiment_tolerant(tmp_path):
    """删除不存在的实验：文件缺失跳过（kept），不炸不误报。"""
    home, config, profiles = _mk(tmp_path)
    with patch("utils.voice_models.load_profiles", return_value=profiles), \
         patch("utils.voice_models.upsert_profile"):
        r = delete_voice_model("rvc", "ghost", config)
    assert r["ok"] is True and r["deleted"] == [] and r["errors"] == []


def test_ui_relocation_tts_retired_train_wired():
    """UI 收口守卫：tts.py 删除面退役（只选不删）；训练页管理区块接线。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tts = open(os.path.join(root, "frontend", "ui", "tabs", "tts.py"), encoding="utf-8").read()
    assert "_delete_profile" not in tts, "tts.py 删除逻辑须退役"
    assert 'icon="delete"' not in tts, "tts.py 不得再有删除按钮"
    train = open(os.path.join(root, "frontend", "ui", "tabs", "train_voice.py"),
                 encoding="utf-8").read()
    for needle in ("list_voice_models", "delete_voice_model", "_confirm_delete",
                   "_render_models", "声音模型管理"):
        assert needle in train, f"训练页管理区块缺 {needle}"


def test_ui_liveness_guards():
    """UI 存活性守卫（2026-11-07 用户实报批次）：

    - svc._poll：status 非 dict 防护（NoneType.get 实报根修）+ 页面删除后
      timer 自杀（slot deleted 异常循环根修）+ 模型段动态重绘（存在性过滤）
    - tts 档案行：模型缺失标记（悬空档案透明可见）
    - choose_song：翻唱音色下拉文件存在性过滤（选中必炸的幽灵项不再出现）
    - train_voice._poll：同款页面删除自杀防御
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svc = open(os.path.join(root, "frontend", "ui", "tabs", "svc.py"), encoding="utf-8").read()
    assert "if not isinstance(st, dict):" in svc, "svc._poll 缺 status 非 dict 防护"
    assert '"has been deleted" in str(e)' in svc, "svc._poll 缺页面删除 timer 自杀"
    assert "_render_model_section" in svc and "model_section_body.clear()" in svc, \
        "变声模型段应容器化重绘（动态刷新）"
    assert 'os.path.isfile(p["rvc_model"])' in svc, "档案引用行缺文件存在性过滤（幽灵行）"
    tts = open(os.path.join(root, "frontend", "ui", "tabs", "tts.py"), encoding="utf-8").read()
    assert 'and os.path.isfile(p["gpt_model"])' in tts, \
        "tts 声音列表应只显示可用声音（模型双件齐存过滤，用户裁决 2026-11-07）"
    assert "模型缺失" not in tts, "呈现层过滤后缺失标记分支应退役"
    song = open(os.path.join(root, "frontend", "ui", "tabs", "choose_song.py"),
                encoding="utf-8").read()
    assert 'os.path.isfile(p["rvc_model"])' in song, "翻唱音色下拉缺存在性过滤"
    train = open(os.path.join(root, "frontend", "ui", "tabs", "train_voice.py"),
                 encoding="utf-8").read()
    assert '"has been deleted" in str(e)' in train, "train_voice._poll 缺自杀防御"
