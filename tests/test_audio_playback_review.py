# -*- coding: UTF-8 -*-
"""
音频播放审查批次测试（/autoplan main-20261022-143149 批准实施，2026-10-22）

义务覆盖（review-record.md E1-E6/R1-R8）：
- E1：队列溢出四规则（设计四示例+主叙述逐行复现）+ 999 单实例约束 + 有界不变量
- E1/A2：变速迁移（clamp / pitch 换算公式 / 合成侧随机源边界）
- A6/R6(a)：reread type 透传（999 映射保持，签名不 TypeError）
- A10：四句一切切分（四句上限 / 无标点 60 字兜底 / 末段平衡）
- A11：门控（idle 本地音频 enable / EDTalk 推送链豁免）
- A12：svc 失败不入队
- F-2/F-3/F-5：builtin 暂停-续播状态机（pause 保持/挂起让位/弃置留痕/回退折算）
- A5/F-4：优先级单源（settings 默认表无 priority_mapping + builtin provider 消费）
"""

import asyncio
import threading
import types

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.audio.synthesis_manager import SynthesisMixin
from utils.audio_handle.tts_params import clamp_speed_factor, speed_to_edge_pitch, speed_to_edge_rate


# ---------------------------------------------------------------------------
# 公共桩
# ---------------------------------------------------------------------------

class _FakeConfig:
    """queue/synthesis 依赖的 filter/play_audio 配置桩（键元组→值）"""

    def __init__(self, priority_mapping=None, min_start_play=3, max_len=100, extra=None):
        self._data = {
            ("filter", "priority_mapping"): priority_mapping if priority_mapping is not None else {},
            ("filter", "voice_tmp_path_queue_min_start_play"): min_start_play,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("play_audio", "enable"): True,
            ("play_audio", "text_split_enable"): False,
            ("audio_random_speed", "normal", "enable"): False,
        }
        if extra:
            self._data.update(extra)

    def get(self, *keys):
        return self._data.get(keys)


class _QueueFixture(QueueMixin):
    """手工组装 QueueMixin 依赖（不拉起 Audio 全量依赖）"""

    def __init__(self, config=None):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        if config is not None:
            self.config = config


def _item(w, label):
    return {"type": w, "voice_path": f"{w}-{label}.wav"}


# ---------------------------------------------------------------------------
# E1：队列溢出四规则（设计示例逐行复现）
# ---------------------------------------------------------------------------

class TestQueueFourRules:
    """A1 四条权威规则（实现前经四示例+主叙述复推精化：同权重组组首取代+组内 FIFO）"""

    def _fixture(self, max_len):
        mapping = {t: int(t[1:]) for t in
                   ("w999", "w200", "w150", "w80", "w20", "w10", "w50", "w90", "w60", "w120", "w100", "w30", "w40", "w70")}
        return _QueueFixture(config=_FakeConfig(priority_mapping=mapping, max_len=max_len, min_start_play=0))

    def _build(self, fx, weights):
        for i, w in enumerate(weights):
            assert fx.data_priority_insert("待播放音频列表", _item(w, f"old{i}"))["code"] == 200

    def _types(self, fx):
        return [d["type"] for d in fx.voice_tmp_path_queue]

    def test_design_example_1_not_full(self):
        """示例1：未满——按权重序插入，同权重接组尾"""
        fx = self._fixture(10)
        self._build(fx, ["w100", "w80", "w80", "w50"])
        for i, w in enumerate(["w120", "w90", "w30", "w10"]):
            fx.data_priority_insert("待播放音频列表", _item(w, f"new{i}"))
        assert self._types(fx) == ["w120", "w100", "w90", "w80", "w80", "w50", "w30", "w10"]
        # 身份：old 80 双双保留，new 90 插在 100 后 80 前
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w120-new0.wav", "w100-old0.wav", "w90-new1.wav",
                          "w80-old1.wav", "w80-old2.wav", "w50-old3.wav", "w30-new2.wav", "w10-new3.wav"]

    def test_design_example_2_same_weight_replace_tail_group(self):
        """示例2：满+同权重——组首最老被取代，组内 FIFO（末尾保留最新 10）"""
        fx = self._fixture(6)
        self._build(fx, ["w80", "w50", "w50", "w10"])
        for i, w in enumerate(["w90", "w60", "w10", "w10"]):
            fx.data_priority_insert("待播放音频列表", _item(w, f"new{i}"))
        assert self._types(fx) == ["w90", "w80", "w60", "w50", "w50", "w10"]
        assert fx.voice_tmp_path_queue[-1]["voice_path"] == "w10-new3.wav"  # 最新 10 存活

    def test_design_example_3_mixed(self):
        """示例3：满——200/150 取代、70 插入驱逐 20、10 舍弃；150old+150new 双存"""
        fx = self._fixture(6)
        self._build(fx, ["w999", "w200", "w150", "w150", "w80", "w20"])
        for i, w in enumerate(["w200", "w150", "w70", "w10"]):
            fx.data_priority_insert("待播放音频列表", _item(w, f"new{i}"))
        assert self._types(fx) == ["w999", "w200", "w150", "w150", "w80", "w70"]
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w999-old0.wav", "w200-new0.wav", "w150-old3.wav",
                          "w150-new1.wav", "w80-old4.wav", "w70-new2.wav"]

    def test_design_example_4_double_150(self):
        """示例4：满——双 150 连发后组内全为新（组首 FIFO 取代的区分性用例）；
        80new 取代 80old；20old 保留"""
        fx = self._fixture(6)
        self._build(fx, ["w999", "w200", "w150", "w150", "w80", "w20"])
        for i, w in enumerate(["w200", "w150", "w150", "w80"]):
            fx.data_priority_insert("待播放音频列表", _item(w, f"new{i}"))
        assert self._types(fx) == ["w999", "w200", "w150", "w150", "w80", "w20"]
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w999-old0.wav", "w200-new0.wav", "w150-new1.wav",
                          "w150-new2.wav", "w80-new3.wav", "w20-old5.wav"]

    def test_main_narrative_evict_oldest_of_lowest_group(self):
        """主叙述：满后 40 插入驱逐最低优组最老条目（10old 首个），10old2+10new 双存"""
        fx = self._fixture(6)
        self._build(fx, ["w50", "w50", "w20", "w10", "w10"])
        for i, w in enumerate(["w10", "w20", "w40"]):
            fx.data_priority_insert("待播放音频列表", _item(w, f"new{i}"))
        assert self._types(fx) == ["w50", "w50", "w40", "w20", "w10", "w10"]
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w50-old0.wav", "w50-old1.wav", "w40-new2.wav",
                          "w20-new1.wav", "w10-old4.wav", "w10-new0.wav"]

    def test_bounded_invariant_under_high_priority_burst(self):
        """有界不变量：满队持续高优轰炸，队列长度恒 ≤ max_len"""
        fx = self._fixture(6)
        self._build(fx, ["w20"] * 6)
        for i in range(30):
            fx.data_priority_insert("待播放音频列表", _item("w200" if i % 2 else "w10", f"burst{i}"))
            assert len(fx.voice_tmp_path_queue) <= 6
        assert len(fx.voice_tmp_path_queue) == 6

    def test_999_single_instance_in_queue(self):
        """999 单实例：队列中已有 999 时新 999 舍弃（优先于取代/插队规则）"""
        fx = self._fixture(6)
        self._build(fx, ["w999", "w200", "w150", "w150", "w80", "w20"])
        result = fx.data_priority_insert("待播放音频列表", _item("w999", "second"))
        assert result["code"] == 1
        assert sum(1 for d in fx.voice_tmp_path_queue if d["type"] == "w999") == 1

    def test_999_single_instance_active_flag(self):
        """999 单实例：播放器持有（active 标记）时新 999 舍弃——即使队列为空"""
        fx = self._fixture(6)
        fx.active_reread_top_999 = True
        result = fx.data_priority_insert("待播放音频列表", _item("w999", "while-playing"))
        assert result["code"] == 1
        assert fx.voice_tmp_path_queue == []

    def test_999_active_cleared_allows_next(self):
        """999 释放（active 清除、队列无 999）后新 999 正常入队"""
        fx = self._fixture(6)
        fx.active_reread_top_999 = False
        result = fx.data_priority_insert("待播放音频列表", _item("w999", "fresh"))
        assert result["code"] == 200
        assert fx.voice_tmp_path_queue[0]["type"] == "w999"

    def test_interrupt_requested_for_non999_insert(self):
        """A7 情况1：非 999 新音频入队成功且 999 active → 请求 builtin 让位"""
        fx = self._fixture(6)
        fx.active_reread_top_999 = True
        called = []
        fx.audio_player = types.SimpleNamespace(
            request_999_interrupt=lambda: called.append(1))
        fx.data_priority_insert("待播放音频列表", _item("w200", "interruptor"))
        assert called == [1]

    def test_interrupt_ignored_when_active_false(self):
        """无 999 在播时不触发让位"""
        fx = self._fixture(6)
        called = []
        fx.audio_player = types.SimpleNamespace(
            request_999_interrupt=lambda: called.append(1))
        fx.data_priority_insert("待播放音频列表", _item("w200", "normal"))
        assert called == []


# ---------------------------------------------------------------------------
# A2：变速迁移（clamp / pitch 换算）
# ---------------------------------------------------------------------------

class TestSpeedClampAndPitch:
    def test_clamp_valid_domain(self):
        assert clamp_speed_factor(1.2) == 1.2
        assert clamp_speed_factor(0.25) == 0.25
        assert clamp_speed_factor(4.0) == 4.0

    def test_clamp_invalid_falls_back_to_1(self):
        assert clamp_speed_factor(None) == 1.0
        assert clamp_speed_factor("abc") == 1.0
        assert clamp_speed_factor(0) == 1.0
        assert clamp_speed_factor(-1.5) == 1.0
        assert clamp_speed_factor(10.0) == 1.0

    def test_pitch_formula_matches_design(self):
        """设计数值：1.2 倍→+10Hz、0.9 倍→-5Hz（R2-A2 公式 pitch=(factor-1)*50）"""
        assert speed_to_edge_pitch(1.2) == "+10Hz"
        assert speed_to_edge_pitch(0.9) == "-5Hz"
        assert speed_to_edge_pitch(1.0) == "+0Hz"

    def test_pitch_clamped_to_50hz(self):
        assert speed_to_edge_pitch(3.0) == "+50Hz"   # (3-1)*50=100 → 截断
        assert speed_to_edge_pitch(0.25) == "-37.5Hz"

    def test_rate_roundtrip_unchanged(self):
        """P26 既有映射不回归"""
        assert speed_to_edge_rate(1.2) == "+20%"
        assert speed_to_edge_rate(0.9) == "-10%"


# ---------------------------------------------------------------------------
# A6：reread type 透传（999 映射保持）
# ---------------------------------------------------------------------------

class _RereadConfig:
    def __init__(self, data):
        self._data = data

    def get(self, *keys):
        result = self._data
        for k in keys:
            if result is None:
                break
            result = result.get(k, None) if isinstance(result, dict) else None
        return result


class TestRereadTypePassthrough:
    def _handler(self, captured):
        from utils.handlers.comment_handler import CommentHandler

        class _MH:
            config = _RereadConfig({
                "reread": {"enable": True, "keywords": ["复读"]},
                "audio_synthesis_type": "edge_tts",
                "edge_tts": {"voice": "x"},
                "filter": {},
            })

            def audio_synthesis_handle(self, message):
                captured.append(message)

        return CommentHandler(_MH())

    def test_reread_top_priority_type_preserved(self):
        """R6(a)：reread_top_priority 原样透传（999 权重映射保持），不再 TypeError"""
        captured = []
        h = self._handler(captured)
        ok = h.reread_handle({"username": "u", "content": "复读这段"}, type="reread_top_priority")
        assert ok is True
        assert captured[0]["type"] == "reread_top_priority"

    def test_plain_reread_default(self):
        captured = []
        h = self._handler(captured)
        assert h.reread_handle({"username": "u", "content": "复读这段"}) is True
        assert captured[0]["type"] == "reread"

    def test_no_keyword_noop(self):
        captured = []
        h = self._handler(captured)
        assert h.reread_handle({"username": "u", "content": "普通弹幕"}, type="reread") is False
        assert captured == []


# ---------------------------------------------------------------------------
# A10：四句一切切分
# ---------------------------------------------------------------------------

class TestFourSentenceSplit:
    def _split(self, text):
        from utils.common.text_mixin import TextMixin
        return TextMixin.split_sentences(object.__new__(TextMixin), text)

    def test_four_sentence_cap(self):
        text = "。" .join(["第一句内容呀", "第二句内容呀", "第三句内容呀", "第四句内容呀",
                          "第五句内容呀", "第六句内容呀", "第七句内容呀", "第八句内容呀", "第九句"]) + "。"
        segs = self._split(text)
        assert len(segs) == 3
        for seg in segs:
            assert sum(seg.count(c) for c in "。！？!?") <= 4

    def test_no_punct_long_runon_fallback(self):
        """无句末标点长文（150 字逗号连排）→ 各片段 ≤60 字兜底"""
        body = "这是一段没有句末标点的超长文本，" * 10
        segs = self._split(body)
        assert len(segs) >= 2
        assert all(len(s) <= 61 for s in segs)  # 60 字窗口（含切点标点 ≤61）
        assert "".join(segs).replace("\n", "") == body

    def test_short_tail_balanced(self):
        """末段过短（<10 字）从相邻段回拉一句（不破坏四句上限）"""
        text = "第一句内容比较长一些。" * 1 + "第二句也比较长哦。" + "第三句依旧长。" + "第四句。" + "短句"
        text = "第一句内容比较长一些。第二句也比较长哦。第三句依旧长。第四句。短句"
        segs = self._split(text)
        assert len(segs) == 2
        assert len(segs[-1]) >= 10 - 4  # 回拉后末段不再极端短

    def test_empty_text(self):
        assert self._split("") == []
        assert self._split(None) == []


# ---------------------------------------------------------------------------
# A11/A12：门控与变声失败（synthesis 链，桩化）
# ---------------------------------------------------------------------------

class _SynthHarness(SynthesisMixin):
    """SynthesisMixin 桩：继承真 Mixin（my_play_voice 真逻辑），仅桩掉外设依赖"""

    def __init__(self, config, edtalk_active=False):
        import utils.audio.synthesis_manager as sm
        self.config = config
        self.captured = []
        self._edtalk_active = edtalk_active
        self._orig_edtalk = sm.is_edtalk_active
        sm.is_edtalk_active = lambda cfg: self._edtalk_active
        self._sm_module = sm

        class _Common:
            def remove_extra_words(_s, content, max_len, max_char_len):
                return content

            def convert_digits_to_chinese(_s, u):
                return u

        self.common = _Common()

    def close(self):
        self._sm_module.is_edtalk_active = self._orig_edtalk

    def data_priority_insert(self, type, data_json=None):
        self.captured.append((type, data_json))
        return {"code": 200, "msg": "ok"}

    def abnormal_alarm_handle(self, key):
        pass

    async def _fake_tts(self, message):
        message["result"] = {"code": 200, "msg": "合成成功", "audio_path": "fake.wav"}
        return message



def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestGatingAndSvcFail:
    def _harness(self, enable=True, edtalk=False):
        cfg = _FakeConfig(extra={
            ("play_audio", "enable"): enable,
            ("read_comment", "voice_change"): False,
        })
        return _SynthHarness(cfg, edtalk_active=edtalk)

    def test_edtalk_exempts_enable_for_tts_chain(self):
        """A11/R2-A11：EDTalk 激活时 TTS 链豁免 enable（数字人音频不受影响）"""
        h = self._harness(enable=False, edtalk=True)
        try:
            msg = {"type": "comment", "tts_type": "edge_tts", "content": "hi",
                   "config": {"max_len": 100, "max_char_len": 100}}
            h.tts_handle = h._fake_tts
            _run(h.my_play_voice(msg))
            assert len(h.captured) == 1
        finally:
            h.close()

    def test_enable_false_blocks_local_playback_insert(self):
        """enable=false 且非 EDTalk：TTS 产物不入本地播放队列"""
        h = self._harness(enable=False, edtalk=False)
        try:
            msg = {"type": "comment", "tts_type": "edge_tts", "content": "hi",
                   "config": {"max_len": 100, "max_char_len": 100}}
            h.tts_handle = h._fake_tts
            _run(h.my_play_voice(msg))
            assert h.captured == []
        finally:
            h.close()

    def test_enable_true_inserts(self):
        h = self._harness(enable=True, edtalk=False)
        try:
            msg = {"type": "comment", "tts_type": "edge_tts", "content": "hi",
                   "config": {"max_len": 100, "max_char_len": 100}}
            h.tts_handle = h._fake_tts
            _run(h.my_play_voice(msg))
            assert len(h.captured) == 1
            assert h.captured[0][0] == "待播放音频列表"
        finally:
            h.close()

    def test_svc_fail_not_enqueued(self):
        """A12/E1：voice_change 返回 None（svc 失败）→ 不入队（报警在 voice_change 内已触发）"""
        h = self._harness(enable=True, edtalk=False)
        try:
            async def _failing_change(path):
                return None
            h.voice_change = _failing_change
            msg = {"type": "comment", "tts_type": "edge_tts", "content": "hi",
                   "config": {"max_len": 100, "max_char_len": 100}}
            h.tts_handle = h._fake_tts
            _run(h.my_play_voice(msg))
            assert h.captured == []
        finally:
            h.close()


# ---------------------------------------------------------------------------
# F-2/F-3/F-5：builtin 暂停-续播状态机（不经 PyAudio）
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self):
        self.stopped = False
        self.started = False
        self._active_calls = 0
        self._active = True

    def stop_stream(self):
        self.stopped = True
        self._active = False

    def start_stream(self):
        self.started = True
        self._active = True

    def is_active(self):
        self._active_calls += 1
        return self._active

    def close(self):
        pass


class _FakeWf:
    def __init__(self, pos=100000):
        self._pos = pos
        self.setpos_calls = []

    def tell(self):
        return self._pos

    def setpos(self, pos):
        self.setpos_calls.append(pos)
        self._pos = pos

    def close(self):
        pass


def _bare_player():
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p.config_data = {}
    p.config_path = "config.json"
    p.stream = None
    p.pause_event = threading.Event()
    p.audio_data_event = threading.Event()
    p._done_event = threading.Event()
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p.audio_json_list = []
    p.list_lock = threading.Lock()
    p._current = None
    p.last_error = None
    p._error_seq = 0
    p._suspended_999 = None
    p._suspended_play = None
    p._playing_is_999 = False
    p._current_output_rate = 0
    p._interrupt_999_event = threading.Event()
    p._release_999_hook = None
    p._priority_provider = None
    p._completion_cb = None
    return p


class TestBuiltinPauseResume:
    def test_pause_holds_loop_not_completed(self):
        """F-2：手动暂停（pause_event 置位）保持等待，不误报完成"""
        p = _bare_player()
        stream = _FakeStream()
        p.stream = stream
        p.pause_event.set()
        status_holder = {}

        def _hold_then_complete():
            # 暂停两个轮询周期后解除暂停并排空流 → completed
            import time as _t
            _t.sleep(0.15)
            p.pause_event.clear()
            stream._active = False

        threading.Thread(target=_hold_then_complete, daemon=True).start()
        status_holder["status"] = p._wait_loop(is_999=False)
        assert status_holder["status"] == "completed"
        assert stream.stopped is False  # 未被 wait_loop 主动停流

    def test_999_interrupt_suspends(self):
        """A7 情况1：999 播放中让位事件 → 返回 suspended + 停流"""
        p = _bare_player()
        stream = _FakeStream()
        p.stream = stream
        p._interrupt_999_event.set()
        status = p._wait_loop(is_999=True)
        assert status == "suspended"
        assert stream.stopped is True
        assert p._interrupt_999_event.is_set() is False

    def test_skip_returns_skipped(self):
        p = _bare_player()
        p.stream = _FakeStream()
        p._skip_event.set()
        assert p._wait_loop(is_999=False) == "skipped"

    def test_resume_rewinds_one_second_output_rate(self):
        """F-5/E2：回退 1 秒 = 输出采样率帧数（speed≠1 用输出速率折算）"""
        p = _bare_player()
        wf = _FakeWf(pos=100000)
        p.stream = _FakeStream()
        p._current_output_rate = 44100 * 2  # speed=2 的输出采样率
        p._suspended_999 = {"type": "reread_top_priority", "voice_path": "x.wav"}
        p._suspended_play = {"wf": wf, "tmp_audio_path": ""}
        released = []
        p._release_999_hook = lambda: released.append(1)

        # 让 wait_loop 立即完成：续播后保持流 inactive（start_stream 覆写为 no-op）
        p.stream._active = False
        p.stream.start_stream = lambda: None
        p._resume_suspended_999()
        assert wf.setpos_calls == [max(0, 100000 - 88200)]
        assert released == [1]
        assert p._suspended_999 is None and p._suspended_play is None

    def test_discard_suspended_releases_and_cleans(self):
        """F-3：skip/clear/stop_all 弃置挂起条目（释放钩子+现场清理，不静默）"""
        p = _bare_player()
        wf = _FakeWf()
        p._suspended_999 = {"type": "reread_top_priority", "voice_path": "x.wav"}
        p._suspended_play = {"wf": wf, "tmp_audio_path": ""}
        released = []
        p._release_999_hook = lambda: released.append(1)
        p._discard_suspended("清空队列")
        assert released == [1]
        assert p._suspended_999 is None
        assert wf.setpos_calls == []  # 未续播

    def test_request_interrupt_gated_on_playing_999(self):
        """让位请求仅在 999 播放中且未挂起时生效"""
        p = _bare_player()
        p.request_999_interrupt()
        assert p._interrupt_999_event.is_set() is False  # 非 999 播放中
        p._playing_is_999 = True
        p.request_999_interrupt()
        assert p._interrupt_999_event.is_set() is True
        p._interrupt_999_event.clear()
        p._suspended_999 = {"type": "reread_top_priority"}
        p.request_999_interrupt()
        assert p._interrupt_999_event.is_set() is False  # 已挂起 → 忽略（情况c）


# ---------------------------------------------------------------------------
# A5/F-4：优先级单源
# ---------------------------------------------------------------------------

class TestPrioritySingleSource:
    def test_settings_defaults_no_priority_mapping(self):
        from frontend.config import settings as settings_mod
        assert "priority_mapping" not in settings_mod._AUDIO_PLAYER_DEFAULTS

    def test_builtin_provider_wins_over_file(self, tmp_path):
        from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
        import json as _json
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(_json.dumps({"filter": {"priority_mapping": {"comment": 20}}}), encoding="utf-8")
        p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
        p.config_data = {"priority_mapping": {"comment": 30}}  # 存量旧表
        p.config_path = str(cfg_file)
        p._priority_provider = None
        assert p._item_priority({"type": "comment"}) == 20  # 文件 filter 表胜过旧表

        p._priority_provider = lambda: {"comment": 25}  # 内存合并视图最优先
        assert p._item_priority({"type": "comment"}) == 25

    def test_merge_legacy_moves_unique_and_conflicts(self):
        """F-4/F-6：独有键（thanks）迁入 + 冲突键 audio_player 值胜 + 告警非静默"""
        from utils.audio.audio_core import Audio
        a = object.__new__(Audio)

        class _Cfg:
            def __init__(self, data):
                self.config = data

            def get(self, *keys):
                result = self.config
                for k in keys:
                    if result is None:
                        break
                    result = result.get(k, None) if isinstance(result, dict) else None
                return result

        a.config = _Cfg({
            "filter": {"priority_mapping": {"comment": 20, "gift": 20}},
            "audio_player": {"priority_mapping": {"comment": 30, "thanks": 20}},
        })
        a._merge_legacy_priority_mapping()
        fm = a.config.get("filter", "priority_mapping")
        assert fm["comment"] == 30   # 冲突 → audio_player 值胜
        assert fm["thanks"] == 20    # 独有键迁入
        assert fm["gift"] == 20      # 原有不动
