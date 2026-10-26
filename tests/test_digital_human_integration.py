# -*- coding: utf-8 -*-
"""数字人功能优化批次守卫测试（2026-10-26 /goal 数字人优化）。

三大优化：
1. 口型推理：/lip/params 画外音总开关 + 无人脸帧直通（EDTalk 侧，源级守卫——
   realtime_serve 模块顶层 import torch，测试环境无 torch，不能行为导入）；
2. 动作编排素材管理：materials.py 增删素材（行为级）+ /edtalk_materials* 路由
   （TestClient）+ 二级菜单注册（源级）；
3. 整合方案：gaze/repair/lip 三路运行中热切（visual_body 源级守卫）。
"""
import json
import os

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.edtalk_realtime import materials


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1. EDTalk 服务端（源级——torch 顶层导入不可行为导入）
# ======================================================================

class TestEdtalkSideSourceGuards:
    def test_service_has_lip_gate(self):
        src = _src("edtalk/realtime_serve/service.py")
        assert "_VALID_LIP_MODES = ('on', 'off')" in src
        assert "def set_lip_params" in src
        # 状态回显暴露 lip_mode（前端回显数据源）
        assert "status['lip_mode']" in src

    def test_service_has_segment_hot_add_remove(self):
        src = _src("edtalk/realtime_serve/service.py")
        assert "def add_segment" in src
        assert "def remove_segment" in src
        # 未知段映射 404；调度引用/当前段拒绝
        assert "NotFoundError" in src
        assert "set_schedule" in src or "segment/schedule" in src

    def test_api_has_three_new_routes(self):
        src = _src("edtalk/realtime_serve/api/app.py")
        assert '@app.post("/lip/params")' in src
        assert '@app.post("/segment/add")' in src
        assert '@app.post("/segment/remove")' in src
        assert "class LipParamsRequest" in src
        assert "class SegmentAddRequest" in src
        assert "class SegmentRemoveRequest" in src

    def test_pack_has_face_stats_and_hot_api(self):
        src = _src("edtalk/realtime_serve/sequence_frame_pack.py")
        assert "def add_segment" in src
        assert "def remove_segment" in src
        assert "def segment_info" in src
        # 人脸统计（画外音判定依据）
        assert "face_ratio" in src
        assert "isnan" in src

    def test_render_loop_has_voiceover_passthrough(self):
        src = _src("edtalk/realtime_serve/render_loop.py")
        # 无人脸帧/口型关闭 → 原帧直出（跳过 infer 全链），音频照常帧锁定
        assert "np.isnan(facemark).any()" in src
        assert "self.lip_mode != 'on'" in src
        # process_full_audio 口型关闭分支：跳过 Audio2Lip，音频 chunk 照常
        assert "画外音" in src
        # 热切属性初始化
        assert "self.lip_mode: str = 'on'" in src


# ======================================================================
# 2. AUTOlive 素材管理（行为级）
# ======================================================================

def _make_character_dir(tmp_path):
    """伪造 character_dir 四件套（numpy 产 metadata/facemark；orifra/count 放 jpg 占位）。"""
    char = tmp_path / "char"
    seg = char / "orifra" / "seg_a"
    seg.mkdir(parents=True)
    (seg / "000000.jpg").write_bytes(b"fake")
    (char / "count" / "seg_a").mkdir(parents=True)
    (char / "count" / "seg_a" / "000000.jpg").write_bytes(b"fake")
    (char / "metadata").mkdir(parents=True)
    (char / "facemark").mkdir(parents=True)
    meta = np.zeros(1, dtype=[("frame_idx", "i4"), ("crop_x", "f4"), ("crop_y", "f4"),
                              ("crop_size", "f4"), ("scale_factor", "f4"),
                              ("face_detected", "?"), ("pad_ratio", "f4"),
                              ("face_bbox_x1", "f4"), ("face_bbox_y1", "f4"),
                              ("face_bbox_x2", "f4"), ("face_bbox_y2", "f4")])
    np.save(char / "metadata" / "seg_a.npy", meta)
    fm = np.full((1, 68, 2), np.nan, dtype=np.float32)  # 全 NaN=无人脸段
    np.save(char / "facemark" / "seg_a.npy", fm)
    return char


@pytest.fixture()
def mat_env(tmp_path, monkeypatch):
    char = _make_character_dir(tmp_path)
    monkeypatch.setattr(materials, "resolve_character_dir", lambda cfg: str(char))
    monkeypatch.setattr(materials, "service_status", lambda cfg, timeout=2.0: None)
    monkeypatch.setattr(materials, "_training_running", lambda: False)
    return char


class TestMaterialRefs:
    def test_collect_references_from_choreography_node(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "choreography": {
                "default_segments": ["pool_1"],
                "rules": [{"rule_id": "r1", "sequence": {
                    "enter": {"segment": "e_seg"}, "main": {"segment": "m_seg"},
                    "exit": {}},
                    "emotion_tiers": [{"max_count": 3, "segment_overrides": {"enter": "t_seg"}}]},
                ],
            },
            "edtalk_realtime": {"segments": "explicit_seg"},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        refs = materials.collect_references({})
        assert refs["pool_1"] == ["编排默认画面池"]
        assert refs["e_seg"] == ["编排规则 r1·enter"]
        assert refs["m_seg"] == ["编排规则 r1·main"]
        assert refs["t_seg"] == ["编排规则 r1·情绪分级覆盖"]
        assert refs["explicit_seg"] == ["画面设置·段播放调度表"]


class TestMaterialPublish:
    def test_invalid_segment_name_rejected(self, mat_env):
        with pytest.raises(materials.MaterialError, match="段名非法"):
            materials.publish_material({}, "a.mp4", "bad name/../x")

    def test_missing_video_rejected(self, mat_env):
        with pytest.raises(materials.MaterialError, match="视频文件不存在"):
            materials.publish_material({}, str(mat_env.parent / "nope.mp4"), "seg_b")

    def test_bad_ext_rejected(self, mat_env, tmp_path):
        v = tmp_path / "v.txt"
        v.write_text("x", encoding="utf-8")
        with pytest.raises(materials.MaterialError, match="不支持的文件类型"):
            materials.publish_material({}, str(v), "seg_b")

    def test_duplicate_segment_rejected(self, mat_env, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"fake")
        with pytest.raises(materials.MaterialError, match="已存在同名素材"):
            materials.publish_material({}, str(v), "seg_a")

    def test_training_running_rejected(self, mat_env, tmp_path, monkeypatch):
        monkeypatch.setattr(materials, "_training_running", lambda: True)
        v = tmp_path / "v.mp4"
        v.write_bytes(b"fake")
        with pytest.raises(materials.MaterialError, match="训练正在进行中"):
            materials.publish_material({}, str(v), "seg_b")

    def test_busy_task_rejected(self, mat_env, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"fake")
        materials._set_task(state="running", segment="other")
        try:
            with pytest.raises(materials.MaterialError, match="任务进行中"):
                materials.publish_material({}, str(v), "seg_b")
        finally:
            materials._set_task(state="idle", segment="", ok=None)


class TestMaterialDelete:
    def test_delete_unreferenced(self, mat_env, monkeypatch):
        monkeypatch.setattr(materials, "collect_references", lambda cfg: {})
        out = materials.delete_material({}, "seg_a")
        assert out["ok"] is True
        assert not (mat_env / "orifra" / "seg_a").exists()
        assert not (mat_env / "facemark" / "seg_a.npy").exists()

    def test_delete_referenced_refused(self, mat_env, monkeypatch):
        monkeypatch.setattr(materials, "collect_references",
                            lambda cfg: {"seg_a": ["编排默认画面池"]})
        with pytest.raises(materials.MaterialError, match="被保护"):
            materials.delete_material({}, "seg_a")
        assert (mat_env / "orifra" / "seg_a").exists()  # 未动盘

    def test_delete_current_segment_refused(self, mat_env, monkeypatch):
        monkeypatch.setattr(materials, "collect_references", lambda cfg: {})
        monkeypatch.setattr(materials, "service_status",
                            lambda cfg, timeout=2.0: {"current_segment": "seg_a"})
        with pytest.raises(materials.MaterialError, match="正在渲染"):
            materials.delete_material({}, "seg_a")


class TestMaterialList:
    def test_list_disk_scan_voiceover_flag(self, mat_env):
        out = materials.list_materials({})
        assert out["service_online"] is False
        items = out["items"]
        assert [i["segment"] for i in items] == ["seg_a"]
        it = items[0]
        assert it["total_frames"] == 1
        assert it["face_ratio"] == 0.0
        assert it["voiceover_only"] is True  # 全 NaN facemark → 画外音素材
        assert it["complete"] is True


class TestMaterialRoutes:
    def _client(self, monkeypatch, cfg=None):
        app = FastAPI()
        materials.register_material_routes(app, cfg or {})
        return TestClient(app, raise_server_exceptions=False)

    def test_get_list_ok(self, mat_env, monkeypatch):
        client = self._client(monkeypatch)
        resp = client.get("/edtalk_materials")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["segment"] == "seg_a"

    def test_post_loopback_guard_403(self, mat_env, monkeypatch):
        client = self._client(monkeypatch)
        assert client.post("/edtalk_materials/publish",
                           json={"video_path": "x.mp4", "segment": "s"}).status_code == 403
        assert client.post("/edtalk_materials/delete",
                           json={"segment": "seg_a"}).status_code == 403

    def test_delete_via_route(self, mat_env, monkeypatch):
        from utils.orchestration.api import _require_loopback
        client = self._client(monkeypatch)
        client.app.dependency_overrides[_require_loopback] = lambda: None
        monkeypatch.setattr(materials, "collect_references", lambda cfg: {})
        resp = client.post("/edtalk_materials/delete", json={"segment": "seg_a"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_referenced_422_with_message(self, mat_env, monkeypatch):
        from utils.orchestration.api import _require_loopback
        client = self._client(monkeypatch)
        client.app.dependency_overrides[_require_loopback] = lambda: None
        monkeypatch.setattr(materials, "collect_references",
                            lambda cfg: {"seg_a": ["编排默认画面池"]})
        resp = client.post("/edtalk_materials/delete", json={"segment": "seg_a"})
        assert resp.status_code == 422
        assert "被保护" in resp.json()["detail"]["message"]


# ======================================================================
# 3. 前端整合（源级）
# ======================================================================

class TestFrontendIntegration:
    def test_visual_body_has_lip_hotswitch(self):
        src = _src("frontend/ui/tabs/visual_body.py")
        assert "/lip/params" in src
        # 2026-10-26 推理合并卡：单入口 _apply_inference（热推三路+保存初值）
        assert "_apply_inference" in src
        assert "lip_select" in src
        # 口型门控状态回显
        assert 'status.get("lip_mode")' in src

    def test_visual_body_choreo_card_migrated_out(self):
        src = _src("frontend/ui/tabs/visual_body.py")
        assert "卡片 3.5：动作编排管理" not in src
        assert "_render_rules" not in src
        # F7 手动切段互锁状态源保留
        assert "_poll_choreo_active" in src

    def test_choreography_tab_module(self):
        src = _src("frontend/ui/tabs/choreography.py")
        assert "def create_choreography_tab" in src
        # 素材管理三端点 + 编排规则端点
        assert "/edtalk_materials/publish" in src
        assert "/edtalk_materials/delete" in src
        assert "/edtalk_materials/task" in src
        assert "/choreography/rules" in src
        # 画外音标注（步骤下拉）
        assert "画外音" in src

    def test_layout_registers_choreography(self):
        src = _src("frontend/ui/layout.py")
        assert "train_voice, choreography" in src
        assert "'choreography': lambda: choreography.create_choreography_tab" in src

    @pytest.mark.parametrize("cfg_file", ["config.json", "config.example.json"])
    def test_nav_has_choreography_entry(self, cfg_file):
        with open(os.path.join(REPO, cfg_file), encoding="utf-8") as f:
            cfg = json.load(f)
        groups = cfg["webui"]["function_groups"]
        dh = next(g for g in groups if g["name"] == "数字人")
        keys = [fn["tab_key"] for fn in dh["functions"]]
        assert "choreography" in keys
        assert "visual_body" in keys

    def test_web_server_registers_material_routes(self):
        src = _src("utils/web_server.py")
        assert "register_material_routes" in src
        assert "edtalk_realtime.materials" in src

    def test_config_page_clear_cancels_timers(self):
        """页内 Timer 随导航 clear 泄漏修复（2026-10-26 用户实测报障）。

        本环境 NiceGUI Timer._should_stop 不检查元素删除——每次导航
        clear() 后旧页定时器继续打 API 并操作已删元素（"An element has
        been deleted but is still being used" 警告，编排页三轮询首报）。
        守卫：清除入口统一先 cancel 页内 Timer 再 clear，三处 clear 全收口。
        """
        src = _src("frontend/ui/layout.py")
        assert "def _clear_config_page" in src
        assert "from nicegui.timer import Timer" in src
        assert "isinstance(el, Timer)" in src
        assert "el.cancel()" in src
        # 三处直接 config_page.clear() 必须全部收口到 _clear_config_page
        body = src.split("def _clear_config_page", 1)[1]
        assert "config_page.clear()" not in body.replace(
            "self.config_page.clear()", "", 1), "存在绕过 _clear_config_page 的裸 clear"

    def test_choreography_material_list_friendly_error(self):
        """清单获取失败文案分类（2026-10-26 用户实测报障）：连接拒绝=系统未启动
        常态提示（warning 色、带地址与恢复指引），非连接类异常保留简短信息。"""
        src = _src("frontend/ui/tabs/choreography.py")
        assert "isinstance(e, _rq.exceptions.ConnectionError)" in src
        assert "运行系统未启动" in src
        assert "启动系统后自动恢复" in src


class TestEdtalkConfigPageCleanup:
    """EDTalk 实时推理配置页清理（2026-10-26）：死项退役 + 分组重组。

    reaction_segment（弹幕触发切段）已被 choreography 编排规则取代，
    逻辑消费方为零——UI 控件/默认表/示例配置三处清除 + 退役键一次性告知。
    """

    def test_reaction_segment_removed_from_ui(self):
        src = _src("frontend/ui/tabs/visual_body.py")
        assert "reaction_segment" not in src
        assert "弹幕触发切段" not in src

    def test_reaction_segment_removed_from_defaults(self):
        src = _src("frontend/config/settings.py")
        assert "reaction_segment" not in src

    def test_reaction_segment_removed_from_example_config(self):
        with open(os.path.join(REPO, "config.example.json"), encoding="utf-8") as f:
            raw = f.read()
        assert "reaction_segment" not in raw
        assert "_deprecated_note" not in raw

    def test_retired_keys_notice_registered(self):
        src = _src("frontend/main.py")
        assert '("edtalk_realtime", "reaction_segment")' in src

    def test_config_page_grouped_sections(self):
        """配置页收敛后结构：基础配置（无内部量控件）+ 推理配置合并卡。"""
        src = _src("frontend/ui/tabs/visual_body.py")
        assert "数字人基础配置" in src
        assert "推理配置（立即生效" in src
        # 内部量控件已移除（端口/解释器/段调度）
        assert "服务端口" not in src
        assert "解释器路径" not in src
        assert '段播放调度（auto' not in src
        assert "_fetch_segments" not in src
        assert "服务与网络" not in src
        # 推理合并卡：单入口应用（热推+保存初值）
        assert "_apply_inference" in src
        assert "_apply_gaze" not in src and "_apply_repair" not in src \
            and "_apply_lip" not in src

    def test_runtime_module_and_consumers(self):
        """端口/解释器内部量：runtime 模块存在，四类消费方统一解析。"""
        rt = _src("utils/edtalk_realtime/runtime.py")
        for fn in ("def resolve_api_base", "def resolve_interpreter",
                   "def find_free_port", "def probe_identity", "def write_runtime",
                   "def read_runtime"):
            assert fn in rt, fn
        assert "resolve_api_base" in _src("utils/edtalk_realtime/edtalk_client.py")
        mat = _src("utils/edtalk_realtime/materials.py")
        assert "resolve_api_base" in mat and "resolve_interpreter" in mat
        assert "resolve_api_base" in _src("frontend/ui/tabs/visual_body.py")
        assert "resolve_api_base" in _src("frontend/ui/tabs/choreography.py")
        # 拉起端：占用自动切换 + 运行时落盘 + 固定 --segments auto
        main_src = _src("frontend/main.py")
        assert "probe_identity" in main_src
        assert "find_free_port" in main_src
        assert "write_runtime" in main_src
        assert 'cmd += ["--segments", "auto"]' in main_src

    def test_host_and_token_retired(self):
        """控制面收敛纯本机回环：host/api_token UI 写入点清除 + 退役告知 + 启动映射固定回环。

        注：visual_body._auth_headers 保留对存量 api_token 键的只读降级
        （缺键→不发头），属优雅兼容非死项——故只断言"无写入点与控件"。
        """
        ui_src = _src("frontend/ui/tabs/visual_body.py")
        assert "绑定地址 host" not in ui_src
        assert "API Token（可选）" not in ui_src
        assert 'set_config_callback("edtalk_realtime", "host"' not in ui_src
        assert 'set_config_callback("edtalk_realtime", "api_token"' not in ui_src
        settings_src = _src("frontend/config/settings.py")
        internal_keys = ("host", "api_token", "reaction_segment",
                         "port", "api_ip_port", "interpreter_path", "segments")
        for key in internal_keys:
            assert f'"{key}":' not in settings_src.split("_EDTALK_REALTIME_DEFAULTS")[1].split("}")[0], key
        main_src = _src("frontend/main.py")
        # 启动映射固定回环、不再传 token
        assert 'cmd += ["--host", "127.0.0.1"]' in main_src
        assert "--api-token" not in main_src.split("_start_edtalk_realtime")[1].split("def ")[0]
        # 退役键告知注册（7 键全量）
        for key in ("reaction_segment", "host", "api_token",
                    "port", "api_ip_port", "interpreter_path", "segments"):
            assert f'("edtalk_realtime", "{key}")' in main_src, key

    def test_all_remaining_keys_consumed(self):
        """配置页其余 8 键均有消费方（启动映射）——防再次死项。"""
        main_src = _src("frontend/main.py")
        for key in ("enable", "character_dir", "target_fps", "buffer_frames",
                    "preroll_frames", "face_repair", "gaze_correction",
                    "gaze_convergence"):
            assert f'ed_cfg.get("{key}"' in main_src or f'"{key}"' in main_src, key
