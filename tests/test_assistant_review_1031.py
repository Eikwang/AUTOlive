# -*- coding: UTF-8 -*-
"""
助播审查批次守卫测试（2026-10-31）

覆盖（本批变更锚点；路由矩阵/音色回落/迁移等既有覆盖见
test_assistant_anchor_routing.py，此处不重复）：
1. 路由观测全链退役（用户裁决：纯内存态调试面板且原路决策从不记录，
   "所配即所行"验证核心残缺）——记录器/端点/UI 面板零残留；
   _anchor_meta 内部元数据保留（守卫锚点）
2. 十产线路由表完整性 + config 模板与 UI 勾选键一致性
3. 点歌产线只路由文案播报（歌曲音频消息不经助播路由）
4. 知识库链序音频优先于文本（D5/D6）+ 勾选门控
"""

import inspect
import json
import os
import tempfile
from unittest.mock import MagicMock

from utils.handlers import assistant_router as ar
from utils.handlers.feature_handler import FeatureHandler


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def make_anchor_config(types=("ai_chat",), text_enable=True, audio_enable=True,
                       text_path=None, audio_dir=None):
    base = {
        "assistant_anchor": {
            "enable": True,
            "username": "小助",
            "audio_synthesis_type": "edge_tts",
            "type": list(types),
            "local_qa": {
                "text": {"enable": text_enable, "file_path": text_path, "similarity": 0.6},
                "audio": {"enable": audio_enable, "type": "包含关系",
                          "file_path": audio_dir, "similarity": 0.5},
            },
        },
        "filter": {},
        "edge_tts": {"voice": "zh-CN-XiaoxiaoNeural"},
    }
    cfg = MagicMock()
    cfg._cfg = base
    cfg.get = MagicMock(side_effect=lambda *keys: _get(base, keys))
    return cfg


# ---------- ① 路由观测退役 ----------

class TestRouteObservationRetired:
    def test_recorder_symbols_gone(self):
        """记录器符号全链移除（记录器类/实例/查询函数）。"""
        assert not hasattr(ar, "_RouteRecorder")
        assert not hasattr(ar, "_recorder")
        assert not hasattr(ar, "get_route_stats")
        assert not hasattr(ar, "_record")

    def test_router_source_clean(self):
        src = open("utils/handlers/assistant_router.py", encoding="utf-8").read()
        assert "_record(" not in src
        assert "路由观测" not in src

    def test_webui_panel_removed(self):
        src = open("frontend/ui/tabs/assistant_anchor.py", encoding="utf-8").read()
        # 断言面板实体代码不存在（头注释退役说明含"路由观测"字样属正常留痕）
        assert "_refresh_stats" not in src
        assert "刷新路由观测" not in src
        assert "route-stats" not in src

    def test_web_endpoint_removed(self):
        src = open("utils/web_server.py", encoding="utf-8").read()
        assert "route-stats" not in src

    def test_meta_retained_for_guard(self):
        """_anchor_meta 内部元数据保留（路由守卫测试锚点，业务链不消费）。"""
        msg = ar.assistant_copy_message(make_anchor_config(), "内容",
                                        producer_type="gift", hit_layer="direct")
        assert msg["_anchor_meta"]["producer_type"] == "gift"


# ---------- ② 路由表与配置一致性 ----------

class TestRouteTableCompleteness:
    def test_route_table_covers_ten_types(self):
        """ROUTE_TABLE 键集=十产线锚点（十触发类型；reread 归拢聊天）。"""
        assert set(ar.ROUTE_TABLE) == {
            "llm_reply", "main_kb_hit", "reread", "read_comment",
            "gift", "entrance", "follow", "schedule", "idle_time_task",
            "integral", "song"}

    def test_example_config_types_match_ui(self):
        """config.example.json 勾选键集与 UI 十项枚举一致。"""
        cfg = json.load(open("config.example.json", encoding="utf-8"))
        assert set(cfg["assistant_anchor"]["type"]) == {
            "ai_chat", "chat", "read_comment", "gift", "entrance", "follow",
            "schedule", "idle_time_task", "integral", "song"}


# ---------- ③ 点歌产线只路由文案 ----------

def _make_fh(config):
    fh = FeatureHandler.__new__(FeatureHandler)
    stub = MagicMock()
    stub.config = config
    fh.my_handle = stub
    return fh


class TestSongCopyRouting:
    def test_song_copy_checked_routes_to_assistant(self):
        """勾选 song：点歌文案走助播通道（助播消息类型+助播音色）。"""
        fh = _make_fh(make_anchor_config(types=("song",)))
        msg = fh._song_copy_message("观众A", "已为你播放xxx")
        assert msg["type"] == ar.ASSISTANT_TEXT_TYPE
        assert msg["tts_type"] == "edge_tts"
        assert msg["username"] == "小助"

    def test_song_copy_unchecked_keeps_comment(self):
        """未勾选：点歌文案走原路 comment 通道。"""
        fh = _make_fh(make_anchor_config(types=()))
        msg = fh._song_copy_message("观众A", "已为你播放xxx")
        assert msg["type"] == "comment"

    def test_song_audio_message_not_routed(self):
        """歌曲音频消息（type=song）构建点不经助播路由——歌曲始终走数字人/
        编排线（任务书语义）。"""
        src = inspect.getsource(FeatureHandler._song_play_message)
        assert "assistant_route" not in src and "assistant_copy_message" not in src


# ---------- ④ 知识库链序 ----------

class TestKbChainOrder:
    def test_audio_beats_text(self):
        """音频与文本同时命中 → 音频层优先（D5 链序：音频→文本）。"""
        tmpdir = tempfile.mkdtemp(prefix="ar1031a_")
        with open(os.path.join(tmpdir, "今天天气怎么样.mp3"), "wb") as f:
            f.write(b"x")
        fd, txtpath = tempfile.mkstemp(prefix="ar1031t_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("今天天气怎么样|文本答案\n")
        try:
            cfg = make_anchor_config(types=("ai_chat",), text_path=txtpath,
                                     audio_dir=tmpdir)
            layer, payload = ar.assistant_match_chain(cfg, "comment", "今天天气怎么样")
            assert layer == "assistant_audio"
            assert os.path.isfile(payload)
        finally:
            os.unlink(txtpath)
            os.unlink(os.path.join(tmpdir, "今天天气怎么样.mp3"))
            os.rmdir(tmpdir)

    def test_text_fallback_when_audio_miss(self):
        """音频未命中 → 文本层兜底（渲染后答案）。"""
        tmpdir = tempfile.mkdtemp(prefix="ar1031a2_")
        with open(os.path.join(tmpdir, "无关音频.mp3"), "wb") as f:
            f.write(b"x")
        fd, txtpath = tempfile.mkstemp(prefix="ar1031t2_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("今天天气怎么样|文本答案\n")
        try:
            cfg = make_anchor_config(types=("ai_chat",), text_path=txtpath,
                                     audio_dir=tmpdir)
            layer, payload = ar.assistant_match_chain(cfg, "comment", "今天天气怎么样")
            assert layer == "assistant_text"
            assert payload == "文本答案"
        finally:
            os.unlink(txtpath)
            os.unlink(os.path.join(tmpdir, "无关音频.mp3"))
            os.rmdir(tmpdir)

    def test_chain_gated_by_type_check(self):
        """未勾选 AI对话（comment 源）→ miss 直通，知识库不消费。"""
        tmpdir = tempfile.mkdtemp(prefix="ar1031a3_")
        fd, txtpath = tempfile.mkstemp(prefix="ar1031t3_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("今天天气怎么样|文本答案\n")
        try:
            cfg = make_anchor_config(types=("gift",), text_path=txtpath,
                                     audio_dir=tmpdir)
            assert ar.assistant_match_chain(cfg, "comment", "今天天气怎么样") == ("miss", None)
        finally:
            os.unlink(txtpath)
            os.rmdir(tmpdir)
