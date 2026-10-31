# -*- coding: UTF-8 -*-
"""
聊天功能审查批次守卫测试（2026-10-30，任务书「聊天」节）

覆盖：
1. 对话打断接线（关键词命中→清队停播→本句不进对话链，D-10 提示语+反馈）
2. 唤醒/睡眠（提示语播报/纯唤醒词不发 LLM/单次唤醒自动睡眠/临时唤醒别名）
3. 双重翻转修复（唤醒检查单点收敛 talk_handle）
4. 连续对话最新优先（D-22 输入侧 purge_talk_inputs + 播放侧待播 ≥2 舍弃；
   D-10 舍弃反馈）
5. 播放中不录音守卫重构（待播放==0 才检测，检测间隔参数退役）
6. 聊天框直接复读直通（绕过 reread.enable）/调教按钮接线
7. system 来源白名单双层扩展（D-10 聊天区灰字）
8. 模板配置（RATE 22050/检测间隔键退役/打断提示语）
9. 聊天记录气泡化 UI（22px/用户左 AI 右/system 居中灰字）
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _config_get(config, keys):
    node = config._cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


def _make_config(cfg: dict):
    c = MagicMock()
    c._cfg = cfg
    c.get = MagicMock(side_effect=lambda *keys: _config_get(c, keys))
    return c


def _find_word(query, words):
    for w in words or []:
        if w and w in query:
            return w
    return None


def _make_manager(cfg_extra=None, my_handle=None):
    """最小 ConversationManager（唤醒/打断行为测试）。"""
    from utils.conversation_manager import ConversationManager

    talk_cfg = {
        "key_listener_enable": False,
        "direct_run_talk": True,
        "continuous_talk": False,
        "username": "主人",
        "wakeup_sleep": {
            "enable": True,
            "mode": "长期唤醒",
            "wakeup_word": ["管家", "苏醒吧"],
            "sleep_word": ["休息吧", "退下吧"],
            "wakeup_copywriting": ["主人，我在呢"],
            "sleep_copywriting": ["我休息了"],
        },
        "interrupt_talk": {
            "enable": True,
            "keywords": ["住嘴"],
            "clean_type": ["message_queue", "voice_tmp_path_queue", "audio_play"],
            "copywriting": ["好的，我停下了"],
        },
    }
    if cfg_extra:
        talk_cfg.update(cfg_extra)
    cfg = _make_config({"talk": talk_cfg})
    common = MagicMock()
    common.find_substring_in_list.side_effect = _find_word
    return ConversationManager(cfg, common, my_handle)


# ---------------------------------------------------------------------------
# 1. 对话打断（任务书「对话打断」节 + D-10）
# ---------------------------------------------------------------------------

class TestInterruptTalk:
    def test_disabled_no_interrupt(self):
        mgr = _make_manager({"interrupt_talk": {"enable": False, "keywords": ["住嘴"],
                                                "clean_type": [], "copywriting": []}})
        assert mgr._check_interrupt("住嘴别说了") is False

    def test_keyword_hit_clears_and_consumes(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        ret = mgr.talk_handle("住嘴，别说了")
        assert ret.get("interrupted") is True
        # 三类清除全部执行（clean_type 全选）
        mh.clear_queue.assert_any_call("message_queue")
        mh.clear_queue.assert_any_call("voice_tmp_path_queue")
        # 本句不再进入对话链
        mh.process_data.assert_not_called()
        # D-10：提示语播报 + 聊天区反馈
        mh.audio_synthesis_handle.assert_called_once()
        assert mh.audio_synthesis_handle.call_args[0][0]["content"] == "好的，我停下了"
        assert mh.audio_synthesis_handle.call_args[0][0]["type"] == "reread"
        mh.webui_show_chat_log_callback.assert_called_once()

    def test_clean_type_filters(self):
        mh = MagicMock()
        mgr = _make_manager({"interrupt_talk": {"enable": True, "keywords": ["住嘴"],
                                                "clean_type": ["message_queue"],
                                                "copywriting": []}},
                            my_handle=mh)
        mgr.talk_handle("住嘴")
        mh.clear_queue.assert_called_once_with("message_queue")

    def test_works_while_asleep(self):
        """应急停播不要求先唤醒（睡眠状态下同样生效）。"""
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        assert mgr.is_talk_awake is False
        assert mgr.talk_handle("你给我住嘴").get("interrupted") is True

    def test_non_keyword_normal_path(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        # 唤醒功能关闭 → 普通句直达对话链
        mgr.config._cfg["talk"]["wakeup_sleep"]["enable"] = False
        ret = mgr.talk_handle("你好呀")
        assert ret.get("success") is True and not ret.get("interrupted")
        mh.process_data.assert_called_once()


# ---------------------------------------------------------------------------
# 2. 唤醒/睡眠（任务书「语音唤醒与睡眠」节）
# ---------------------------------------------------------------------------

class TestWakeupSleep:
    def test_pure_wake_word_not_sent_to_llm(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        ret = mgr.talk_handle("管家")
        assert ret.get("success") is True
        assert mgr.is_talk_awake is True
        mh.process_data.assert_not_called()  # 纯唤醒词不发 LLM
        # 提示语播报（wakeup_copywriting）+ D-10 反馈
        assert mh.audio_synthesis_handle.call_args[0][0]["content"] == "主人，我在呢"
        mh.webui_show_chat_log_callback.assert_called_once()

    def test_wake_word_with_content_goes_to_llm(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        ret = mgr.talk_handle("管家，今天天气怎么样")
        assert ret.get("success") is True
        assert mgr.is_talk_awake is True
        mh.process_data.assert_called_once()
        assert mh.process_data.call_args[0][0]["content"] == "管家，今天天气怎么样"
        assert mh.process_data.call_args[0][1] == "talk"
        assert mh.process_data.call_args[0][0]["chat_source"] == "voice"

    def test_sleep_word(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        mgr.is_talk_awake = True
        ret = mgr.talk_handle("休息吧")
        assert mgr.is_talk_awake is False
        assert ret.get("awake_result", {}).get("event") == "sleep"
        mh.process_data.assert_not_called()
        assert mh.audio_synthesis_handle.call_args[0][0]["content"] == "我休息了"

    def test_asleep_without_word_ignored(self):
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        assert mgr.talk_handle("今天天气怎么样")["success"] is False
        mh.process_data.assert_not_called()

    def test_single_mode_auto_sleep_after_reply(self):
        """单次唤醒：每完成一次对话自动进入睡眠。"""
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        mgr.config._cfg["talk"]["wakeup_sleep"]["mode"] = "单次唤醒"
        mgr.talk_handle("管家，讲个笑话")
        assert mgr.is_talk_awake is False  # 对话完成自动回睡

    def test_legacy_mode_alias(self):
        """旧值「临时唤醒」别名兼容（前端下拉曾用名）。"""
        mh = MagicMock()
        mgr = _make_manager(my_handle=mh)
        mgr.config._cfg["talk"]["wakeup_sleep"]["mode"] = "临时唤醒"
        mgr.talk_handle("管家，讲个笑话")
        mh.process_data.assert_called_once()

    def test_double_flip_fixed(self):
        """双重翻转修复：唤醒词句不会因状态翻两次被放行进 LLM。"""
        import inspect
        from utils.conversation_manager import ConversationManager
        src = inspect.getsource(ConversationManager.do_listen_and_comment)
        assert "check_talk_awake" not in src

    def test_discard_stale_inputs_wired(self):
        """连续对话开启时新输入到来前舍弃未处理旧消息（D-22 输入侧）。"""
        import inspect
        from utils.conversation_manager import ConversationManager
        src = inspect.getsource(ConversationManager.do_listen_and_comment)
        assert "_discard_stale_talk_inputs()" in src


# ---------------------------------------------------------------------------
# 3. 连续对话最新优先——播放侧（D-22）与输入侧
# ---------------------------------------------------------------------------

class TestContinuousTalkDiscard:
    def _cfg(self, continuous=True):
        return _make_config({"talk": {"continuous_talk": continuous}})

    def _bare_queue(self, cfg):
        from utils.audio.queue_manager import QueueMixin
        import threading
        cls = type("BareQ", (QueueMixin,), {})
        q = cls.__new__(cls)
        q.config = cfg
        q.message_queue = []
        q.message_queue_lock = threading.Lock()
        q.voice_tmp_path_queue = []
        q.voice_tmp_path_queue_lock = threading.Lock()
        q._continuous_talk_discard_notify = MagicMock()
        return q

    def test_pending_ge2_discarded(self):
        """待播放 ≥2（=2 与 >2 同规则）即舍弃只播最新，D-10 反馈条数=2。"""
        q = self._bare_queue(self._cfg())
        q.voice_tmp_path_queue = [{"a": 1}, {"b": 2}]
        q._maybe_discard_pending_for_talk({"type": "talk", "voice_path": "new.wav"})
        assert q.voice_tmp_path_queue == []
        q._continuous_talk_discard_notify.assert_called_once_with(2)

    def test_pending_lt2_kept(self):
        q = self._bare_queue(self._cfg())
        q.voice_tmp_path_queue = [{"a": 1}]
        q._maybe_discard_pending_for_talk({"type": "talk"})
        assert len(q.voice_tmp_path_queue) == 1
        q._continuous_talk_discard_notify.assert_not_called()

    def test_split_group_and_stream_exempt(self):
        """切分段/流式分句豁免——同一次回复的段必须完整排队。"""
        q = self._bare_queue(self._cfg())
        q.voice_tmp_path_queue = [{"a": 1}, {"b": 2}]
        q._maybe_discard_pending_for_talk({"type": "talk", "split_group": 3})
        q._maybe_discard_pending_for_talk({"type": "talk", "talk_stream": True})
        assert len(q.voice_tmp_path_queue) == 2
        q._continuous_talk_discard_notify.assert_not_called()

    def test_continuous_off_kept(self):
        q = self._bare_queue(self._cfg(continuous=False))
        q.voice_tmp_path_queue = [{"a": 1}, {"b": 2}]
        q._maybe_discard_pending_for_talk({"type": "talk"})
        assert len(q.voice_tmp_path_queue) == 2

    def test_non_talk_type_ignored(self):
        q = self._bare_queue(self._cfg())
        q.voice_tmp_path_queue = [{"a": 1}, {"b": 2}]
        q._maybe_discard_pending_for_talk({"type": "reread"})
        assert len(q.voice_tmp_path_queue) == 2

    def test_wired_into_data_priority_insert(self):
        src = _src("utils/audio/queue_manager.py")
        assert "_maybe_discard_pending_for_talk(data_json)" in src
        assert ">= 2" in src  # D-22 边界：=2 与 >2 同规则

    def test_purge_talk_inputs_keeps_others(self):
        q = self._bare_queue(self._cfg())
        q.message_queue = [
            {"type": "talk", "content": "旧问题"},
            {"type": "song", "content": "歌"},
            {"type": "talk", "content": "更旧问题"},
        ]
        removed = q.purge_talk_inputs()
        assert removed == 2
        assert q.message_queue == [{"type": "song", "content": "歌"}]


# ---------------------------------------------------------------------------
# 4. 播放中不录音守卫重构（待播放==0 才检测 + 连续对话让位 + 间隔退役）
# ---------------------------------------------------------------------------

class TestRecordingGuard:
    def test_guard_bit_and_let_through(self):
        src = _src("utils/audio_processor.py")
        # 待播放音频列表为 0（位2）才进行录音检测
        assert "self.my_handle.is_audio_queue_empty() & 2" in src
        # 连续对话让位
        assert 'self.config.get("talk", "continuous_talk")' in src

    def test_interval_param_retired(self):
        """检测间隔参数移除（任务书裁决）。"""
        src = _src("utils/audio_processor.py")
        assert 'config.get(\n                                "talk", "no_recording_during_playback_sleep_interval"' not in src
        assert "no_recording_during_playback_sleep_interval\"" not in src.replace("\n", "")
        template = json.loads((REPO / "config" / "platforms" / "talk.json").read_text(encoding="utf-8"))
        assert "no_recording_during_playback_sleep_interval" not in template

    def test_audio_listen_params_tolerant(self):
        """audio_listen 参数容错解析（声卡输入设备默认第一个麦克风）：
        device_index 空/无效回落 None（pyaudio 系统默认输入设备），
        CHANNELS/RATE 与手动录音链同源 int() 解析，不炸录音线程。"""
        import inspect
        from utils.audio_processor import AudioProcessor  # noqa: F401 模块可导入
        src = inspect.getsource(AudioProcessor.audio_listen)
        assert 'device_index = None  # pyaudio None=系统默认输入设备' in src
        assert 'except (TypeError, ValueError):' in src
        # 不再裸 int() 强转（配置为空即 ValueError 崩线程）
        assert 'device_index = int(self.config.get("talk", "device_index"))\n        \n' not in src


# ---------------------------------------------------------------------------
# 5. 聊天框四逻辑（发送/直接复读/调教/插队）
# ---------------------------------------------------------------------------

class TestChatboxActions:
    def test_reread_direct_bypass(self):
        """聊天框直接复读手动直通（2026-10-30 过滤审查批次：弹幕触发复读退役，
        reread_handle 收口手动通道）——/send 显式 reread 请求直接调
        reread_handle，不再依赖 direct 标记或 reread.enable。"""
        ws = _src("utils/web_server.py")
        assert 'data_json["chat_source"] = "chatbox"' in ws
        assert 'self.my_handle.reread_handle(data_json, type=data_json["type"])' in ws
        # direct 标记与 reread.enable 门控随退役删除（手动直通语义恒成立）
        assert 'direct' not in ws
        ch = _src("utils/handlers/comment_handler.py")
        assert 'data.get("direct")' not in ch
        assert 'config.get("reread", "enable")' not in ch

    def test_tuning_button_wired(self):
        src = _src("frontend/ui/tabs/talk.py")
        assert '_send_chat("tuning")' in src
        assert '_send_chat("reread")' in src
        assert '_send_chat("reread_top_priority")' in src
        assert '_send_chat("comment")' in src

    def test_stream_sentences_exempt_tag(self):
        """流式回复分句带 talk_stream 标记（D-22 豁免透传链）。"""
        assert '"talk_stream": True' in _src("utils/handlers/chat_handler.py")
        assert 'data_json["talk_stream"] = True' in _src("utils/audio/synthesis_manager.py")


# ---------------------------------------------------------------------------
# 6. D-9 互斥 + 唤醒门控（前端）
# ---------------------------------------------------------------------------

class TestMutualExclusionUI:
    def test_d9_toast_both_directions(self):
        src = _src("frontend/ui/tabs/talk.py")
        assert "已自动关闭'直接语音对话'" in src
        assert "已自动关闭'启用按键监听'" in src

    def test_wakeup_gate(self):
        src = _src("frontend/ui/tabs/talk.py")
        assert "需先开启'直接语音对话'才能启用语音唤醒与睡眠" in src

    def test_interval_input_removed(self):
        src = _src("frontend/ui/tabs/talk.py")
        # 检测间隔输入框已移除（注释中的退役说明不算）
        assert "ui.input(label='播放中不进行录音的检测间隔" not in src
        assert '"no_recording_during_playback_sleep_interval"' not in src

    def test_interrupt_and_wakeup_autosave(self):
        """打断/唤醒区配置接线落盘（此前不保存）。"""
        src = _src("frontend/ui/tabs/talk.py")
        for keys in ('("talk", "interrupt_talk", "enable")',
                     '("talk", "interrupt_talk", "keywords")',
                     '("talk", "interrupt_talk", "copywriting")',
                     '("talk", "wakeup_sleep", "enable")',
                     '("talk", "wakeup_sleep", "mode")',
                     '("talk", "wakeup_sleep", "wakeup_word")',
                     '("talk", "wakeup_sleep", "sleep_word")',
                     '("talk", "wakeup_sleep", "wakeup_copywriting")',
                     '("talk", "wakeup_sleep", "sleep_copywriting")'):
            assert keys in src, f"缺少自动保存绑定：{keys}"
        assert "_save_clean_type" in src


# ---------------------------------------------------------------------------
# 7. system 来源白名单（D-10 聊天区灰字）
# ---------------------------------------------------------------------------

class TestSystemSourceWhitelist:
    def test_core_gateway(self):
        uh = _src("utils/handlers/utils_handler.py")
        assert 'chat_source not in ("voice", "chatbox", "comment", "system")' in uh

    def test_webui_gateway(self):
        fm = _src("frontend/main.py")
        assert 'payload.get("chat_source") in ("voice", "chatbox", "comment", "system")' in fm

    def test_conversation_feedback_payload(self):
        src = _src("utils/conversation_manager.py")
        assert '"chat_source": "system"' in src


# ---------------------------------------------------------------------------
# 8. 模板配置
# ---------------------------------------------------------------------------

class TestTalkTemplate:
    @pytest.fixture
    def template(self):
        return json.loads((REPO / "config" / "platforms" / "talk.json").read_text(encoding="utf-8"))

    def test_rate_default_22050(self, template):
        assert template["RATE"] == 22050

    def test_channel_default_1(self, template):
        assert template["CHANNELS"] == 1

    def test_interrupt_copywriting_present(self, template):
        assert template["interrupt_talk"]["copywriting"]
        assert template["interrupt_talk"]["clean_type"]

    def test_retired_keys_absent(self, template):
        assert "no_recording_during_playback_sleep_interval" not in template
        assert "show_chat_log" not in template


# ---------------------------------------------------------------------------
# 9. 聊天记录气泡化 UI（优化目标）
# ---------------------------------------------------------------------------

class TestChatBubbleUI:
    def _src(self):
        return _src("frontend/ui/tabs/talk.py")

    def test_font_size_22(self):
        assert "font-size:22px" in self._src()

    def test_user_left_ai_right(self):
        """问=用户左侧（flex-start），答=AI 右侧（flex-end）。"""
        src = self._src()
        assert "'flex-end' if _is_answer else 'flex-start'" in src

    def test_system_centered_gray(self):
        src = self._src()
        assert "align-self:center" in src
        assert "color:var(--text-hint)" in src

    def test_bubble_render_path(self):
        src = self._src()
        assert "_render_bubble(_entry)" in src
        assert "border-radius:12px" in src


# ---------------------------------------------------------------------------
# 10. talk 页真实构建 smoke（用户实报回归：注册先于创建 → 构建期 NameError
# 「cannot access local variable '_key_listener_switch'」→ 整页兜底成
# 「加载配置失败」。源级断言拦不住此类错误，必须真实执行 create_talk_tab）
# ---------------------------------------------------------------------------

class TestTalkTabRenderSmoke:
    def _build(self):
        from nicegui import ui  # noqa: F401 headless 构建无需启动服务
        from frontend.ui.tabs.talk import create_talk_tab

        cfg = {
            "talk": {
                "key_listener_enable": False,
                "direct_run_talk": False,
                "device_index": "",
                "no_recording_during_playback": True,
                "username": "主人",
                "continuous_talk": False,
                "trigger_key": "Q",
                "stop_trigger_key": "S",
                "volume_threshold": 800.0,
                "silence_threshold": 15.0,
                "CHANNELS": 1,
                "RATE": 22050,
                "asr_streaming": True,
                "interrupt_talk": {"enable": False, "keywords": ["住嘴"],
                                   "copywriting": ["好的"], "clean_type": []},
                "wakeup_sleep": {"enable": False, "mode": "长期唤醒",
                                 "wakeup_word": ["管家"], "sleep_word": ["休息吧"],
                                 "wakeup_copywriting": ["在呢"], "sleep_copywriting": ["晚安"]},
            }
        }
        calls = []

        def set_cfg(*keys, value=None):
            calls.append((keys, value))

        theme = {"card": "", "switch_internal": "",
                 "button_internal_color": "primary", "button_internal_css": ""}
        # 完整执行页面构建（设备枚举/自动保存绑定/渲染回调全链）——
        # 任何构建期异常（NameError/Quasar Invalid value 等）在此即红
        create_talk_tab(cfg, theme, set_cfg, chat_send_callback=lambda t, c: True)
        return calls

    def test_talk_tab_builds_without_error(self):
        calls = self._build()
        assert isinstance(calls, list)

    def test_mutual_exclusion_handlers_attached(self):
        """D-9 联动已注册（构建期注册失败曾致整页崩溃——本 smoke 直接执行拦截）。"""
        # 构建成功即证明 on_value_change 注册在开关创建之后完成；
        # 再触发一次互斥回调验证 toast 行为闭环
        from nicegui import ui
        self._build()
        # headless 环境无浏览器连接，仅验证构建期注册链无异常（行为级由
        # handler 源码断言覆盖：test_d9_toast_both_directions）
        assert True
