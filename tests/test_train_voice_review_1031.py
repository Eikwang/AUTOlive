# -*- coding: UTF-8 -*-
"""
训练声音审查批次守卫（2026-10-31）

审查目标（任务书）：
① 已是干声→跳过分离直接分段/预处理；未勾→MDX 三阶段分离后用干声做数据集处理
  （伴奏/混响不用于训练），干声为两管线共用
② GPT-SoVITS 管线产物→TTS 专用；RVC 管线产物→变声/翻唱专用；分开保存分别应用互不干扰
③ 训练出错/成功均推送通知（alarm_notifier.MODULE_TRAIN_VOICE）

修复项：
- V1 RVC preprocess 原传原始 dataset_dir（上游 preprocess.pipeline_mp_inp_dir 顶层
  listdir），MDX 分离干声被弃用、伴奏/混响原声直入训练——现 mdx_outputs 存在时用
  dataset_dir/mdx_vocals（与 GSV _st_slice 同款分支）
- V2 GSV 导出整记录替换硬写 rvc_model/rvc_index=""——同实验先 RVC 后 GSV 训练会
  抹掉 RVC 模型链接（变声/翻唱丢失模型）——现单写者合并更新
- V3 档案 id 双源不一致（RVC=exp_name 原文，GSV=slugify(name)）——现统一 id=exp_name
- V4 _rerun 绕过 _chain_run 报警路由——单步重跑结果不推送——现同款三态路由
- V5 GSV run_all worker 步骤 fn 无异常护栏——异常外抛杀死线程→running_stage 卡死
  →链式编排死等+报警丢失+开始按钮永不恢复——现 try/except 收失败态（对齐 RVC）
"""
import os
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class _FakePaths:
    def __init__(self, root):
        self.gpt_sovits_root = str(root)
        self.gpt_sovits_python = str(root / "python.stub") if hasattr(root, "__truediv__") else "python.stub"
        self.autolive_home = str(root)


class _FakeCfg:
    def get(self, *a, **k):
        return None


# ---------- 目标①：干声分支（V1） ----------

def test_v1_rvc_preprocess_consumes_dry_vocals(tmp_path):
    """V1：MDX 步产出干声后，RVC preprocess 输入=mdx_vocals；已是干声=原始目录。"""
    from utils.train_rvc_pipeline import TrainRvcPipeline

    p = TrainRvcPipeline(_FakeCfg(), pytest.importorskip("unittest.mock").MagicMock())
    ds = tmp_path / "ds"
    (ds / "mdx_vocals").mkdir(parents=True)
    p.rvc_root = str(tmp_path / "rvc")
    p.autolive_home = str(tmp_path)
    p.params = {"dataset_dir": str(ds), "exp_name": "t", "n_p": 10, "per": 3.7}
    captured = {}
    p._run = lambda cmd, **kw: captured.update(cmd=cmd) or True

    # MDX 已跑（mdx_outputs 存在）→ 处理分离干声目录
    p.mdx_outputs = [str(ds / "mdx_vocals" / "a.wav")]
    p._st_preprocess()
    assert captured["cmd"][2] == str(ds / "mdx_vocals"), \
        "MDX 分离后 preprocess 必须吃 mdx_vocals（伴奏/混响原声不得进训练）"

    # 已是干声（mdx SKIPPED，无 mdx_outputs）→ 处理原始目录
    del p.mdx_outputs
    p._st_preprocess()
    assert captured["cmd"][2] == str(ds)


def test_goal1_source_gsv_slice_branch_aligned():
    """①GSV 侧分支语义仍在：slice 按 mdx_outputs 选 mdx_vocals/原始目录。"""
    src = _src("utils/train_voice_pipeline.py")
    assert 'os.path.join(d, "mdx_vocals")' in src
    assert "if getattr(self, \"mdx_outputs\", None) else d" in src


def test_goal1_dry_outputs_only_inverted_track():
    """①分离产物只收反相轨干声入 mdx_vocals（伴奏/混响不落该目录）。"""
    src = _src("utils/train_process_utils.py")
    assert 'vocals_dir = os.path.join(dataset_dir, "mdx_vocals")' in src
    assert 'got = invert_path if stage["pick"] == "invert" else main_path' in src


# ---------- 目标②：两管线分开保存/应用（V2/V3） ----------

def test_v2_v3_gsv_export_merges_and_preserves_rvc(tmp_path, monkeypatch):
    """V2/V3：GSV 导出合并更新——rvc 字段保留、id=exp_name、GSV 字段覆写。"""
    from utils.train_voice_pipeline import TrainVoicePipeline

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    gsv_w = tmp_path / "GPT_weights_v2ProPlus"
    gsv_w.mkdir()
    (gsv_w / "t_e20.ckpt").write_bytes(b"g")
    vits_w = tmp_path / "SoVITS_weights_v2ProPlus"
    vits_w.mkdir()
    (vits_w / "t_e20.pth").write_bytes(b"s")
    sliced = tmp_path / "ds" / "sliced"
    sliced.mkdir(parents=True)
    (sliced / "a.wav").write_bytes(b"x")
    p.gsv_root = str(tmp_path)
    p.params = {"exp_name": "t", "dataset_dir": str(tmp_path / "ds")}

    saved = {}
    monkeypatch.setattr(
        "utils.voice_profiles.get_profile",
        lambda pid, config_path=None: {"rvc_model": "m.pth", "rvc_index": "i.index",
                                       "prompt_text": "旧文本", "name": "旧名"})
    monkeypatch.setattr(
        "utils.voice_profiles.upsert_profile",
        lambda prof, config_path=None: saved.update(prof) or "t")

    assert p._st_export() is True
    assert saved["rvc_model"] == "m.pth" and saved["rvc_index"] == "i.index", \
        "GSV 导出不得抹除 RVC 管线挂接的模型链接（两管线互不干扰）"
    assert saved["id"] == "t", "档案 id 须与 RVC 导出同键（exp_name），防 slug 差异分裂档案"
    assert saved["gpt_model"].endswith("t_e20.ckpt") and saved["sovits_model"].endswith("t_e20.pth")
    assert saved["ref_audio"].endswith("a.wav")
    assert saved["name"] == "旧名" and saved["prompt_text"] == "旧文本"  # 既有字段不覆盖为空


def test_goal2_consumers_filter_by_pipeline():
    """②三端应用隔离：TTS 只看 gpt/sovits 字段，翻唱/变声只看 rvc 字段。"""
    tts_src = _src("frontend/ui/tabs/tts.py")
    assert 'if p.get("gpt_model") or p.get("sovits_model")}' in tts_src
    song_src = _src("frontend/ui/tabs/choose_song.py")
    assert 'if p.get("rvc_model")}' in song_src
    rvc_svc_src = _src("utils/rvc_service.py")
    assert 'prof.get("rvc_model")' in rvc_svc_src  # 变声默认模型 RVC 档案优先（2026-11-07 公共化）
    # 产物保存隔离：GSV 权重目录 vs models/rvc
    gsv_src = _src("utils/train_voice_pipeline.py")
    assert "GPT_weights_v2ProPlus" in gsv_src and "SoVITS_weights_v2ProPlus" in gsv_src
    rvc_src = _src("utils/train_rvc_pipeline.py")
    assert '"models", "rvc"' in rvc_src


def test_v2_rvc_export_merge_semantics_kept():
    """②RVC 导出单写者合并语义未被回退。"""
    rvc_src = _src("utils/train_rvc_pipeline.py")
    assert 'merged.update({"id": exp_name, "name": merged.get("name") or exp_name,' in rvc_src


# ---------- 目标③：报警推送（V4/V5） ----------

def test_goal3_alarm_module_registered():
    """③train_voice 模块登记：notifier 常量+config.example 开关+页面接线。"""
    from utils.alarm_notifier import MODULE_LABELS, MODULE_TRAIN_VOICE

    assert MODULE_LABELS[MODULE_TRAIN_VOICE] == "训练声音"
    example = _src("config.example.json")
    assert '"train_voice": true' in example
    ui_src = _src("frontend/ui/tabs/train_voice.py")
    assert "alarm_notifier.MODULE_TRAIN_VOICE" in ui_src
    assert "alarm_notifier.alarm_error(" in ui_src
    assert "alarm_notifier.alarm_notice(" in ui_src


def test_v4_rerun_routes_outcome_notification():
    """V4：_rerun 与 _chain_run 走同一结果推送出口。"""
    ui_src = _src("frontend/ui/tabs/train_voice.py")
    assert "_notify_outcome(p, _pipe_name(p))" in ui_src  # _rerun watcher 调用
    assert "_notify_outcome(p, name)" in ui_src           # _chain_run 调用
    assert "def _notify_outcome(p, name: str):" in ui_src


def test_v5_gsv_worker_stage_exception_caught(tmp_path):
    """V5：GSV 步骤异常被护栏捕获收失败态（线程不挂死、可收尾推报警）。"""
    from utils.train_voice_pipeline import FAILED, TrainVoicePipeline

    p = TrainVoicePipeline(_FakeCfg(), _FakePaths(tmp_path))
    p.params = {"dataset_dir": str(tmp_path), "exp_name": "t", "already_dry": True}
    p.asset_issues = []
    p._states = {}

    def _boom():
        raise RuntimeError("boom")

    p._st_slice = _boom  # mdx SKIPPED 后第一个真实步骤
    p.run_all()
    deadline = time.time() + 5
    while p.running_stage is not None and time.time() < deadline:
        time.sleep(0.05)
    assert p.running_stage is None, "步骤异常不得杀死 worker 线程（running_stage 卡死=链式编排死等）"
    assert p.stage_states()["slice"] == FAILED
    assert p.stage_states()["mdx"] == "skipped"
