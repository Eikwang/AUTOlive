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
import json
import threading
import types

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.audio.playback_manager import PlaybackMixin
from utils.audio.synthesis_manager import SynthesisMixin
from utils.audio_handle.tts_params import clamp_speed_factor, speed_to_edge_pitch, speed_to_edge_rate
from utils.common.time_mixin import TimeMixin


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
        # T3/e3（2026-11-05）：让位入口泛化为 request_yield（触发裁决在
        # _maybe_interrupt_999_for a/b 双规则；999 active=a 触发）
        fx.audio_player = types.SimpleNamespace(
            request_yield=lambda: called.append(1))
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
        assert speed_to_edge_pitch(0.25) == "-38Hz"  # 原 -37.5Hz（小数）即 edge-tts 崩点形态，整数化后 -38

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
    """A6：reread type 透传（999 映射保持）。2026-10-30 过滤审查批次：
    reread_handle 收口为手动直通——无 enable/词表门控，内容原文直通合成。"""

    def _handler(self, captured):
        from utils.handlers.comment_handler import CommentHandler

        class _MH:
            config = _RereadConfig({
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

    def test_manual_direct_no_keyword_gate(self):
        """手动直通：词表外的任意内容同样直通（enable/词表门控已退役）。"""
        captured = []
        h = self._handler(captured)
        assert h.reread_handle({"username": "u", "content": "与词表无关"}, type="reread") is True
        assert captured[0]["content"] == "与词表无关"

    def test_missing_content_noop(self):
        """缺 content 字段（异常路径）返回 False 且不进入合成。"""
        captured = []
        h = self._handler(captured)
        assert h.reread_handle({"username": "u"}) is False
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

    async def _fake_tts(self, message):
        message["result"] = {"code": 200, "msg": "合成成功", "audio_path": "fake.wav"}
        return message



def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestGatingAndSvcFail:
    def _harness(self, enable=True, edtalk=False):
        cfg = _FakeConfig(extra={
            ("play_audio", "enable"): enable,
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

    def test_direct_enqueue_after_voice_change_retirement(self):
        """A-6 退役直通断言：变声环节退役后 TTS 产物直接入队（无 svc 失败丢弃路径）。

        Value: protects=退役后 reply/read_comment/comment 音频正常入队播放；
        fails_when=直通路径误删或队列漏入；why_new=A12 语义反转（旧测试断言
        失败不入队已随 voice_change 退役失效）；seam=none;
        no_claim=真实音频内容（mock 队列；单元级）
        """
        h = self._harness(enable=True, edtalk=False)
        try:
            assert not hasattr(h, "voice_change"), "voice_change 已退役，不得残留"
            msg = {"type": "read_comment", "tts_type": "edge_tts", "content": "hi",
                   "config": {"max_len": 100, "max_char_len": 100}}
            h.tts_handle = h._fake_tts
            _run(h.my_play_voice(msg))
            assert len(h.captured) == 1
            assert h.captured[0][0] == "待播放音频列表"
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

    def test_suspension_syncs_pause_event_for_captions(self):
        """E-4 交界缝隙（字幕批次 Eng E1）：让位挂起同置 pause_event（字幕挂起同步），
        续播解除；挂起期手动续播守卫（防双出声）"""
        p = _bare_player()
        stream = _FakeStream()
        p.stream = stream
        p._interrupt_999_event.set()
        assert p._wait_loop(is_999=True) == "suspended"
        assert p.pause_event.is_set() is True, "挂起必须同置 pause_event（get_status().paused → E-4 字幕挂起）"

        # 续播：回退→清 pause_event→start_stream
        wf = _FakeWf(pos=50000)
        p._suspended_999 = {"type": "reread_top_priority", "voice_path": "x.wav"}
        p._suspended_play = {"wf": wf, "tmp_audio_path": ""}
        p._current_output_rate = 16000
        p.stream.start_stream = lambda: None  # 保持 inactive 供 wait_loop 立即完成
        p.stream._active = False
        released = []
        p._release_999_hook = lambda: released.append(1)
        p._resume_suspended_999()
        assert p.pause_event.is_set() is False, "续播必须解除 pause_event（字幕恢复）"

        # 挂起期手动续播守卫
        p2 = _bare_player()
        p2.pause_event.set()
        p2._suspended_999 = {"type": "reread_top_priority"}
        p2.resume_stream()
        assert p2.pause_event.is_set() is True, "挂起期手动续播不得解除 pause_event"

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


# ---------------------------------------------------------------------------
# 播放间隔读侧归一化
# ---------------------------------------------------------------------------

# 2026-10-30 审查批次：TestPlayIntervalResolve 整类退役——「播放间隔min/max」
# 配置随消费回传机制退役移除（播放完成回执驱动下一条取用，无间隔等待），
# _resolve_play_interval/_coerce_interval helper 已从 playback_manager 删除，
# 归一化测试对象不复存在。间隔职责由 builtin 播放器可选
# audio_player.random_audio_interval 承接（_post_play_interval，默认 0）。


# ---------------------------------------------------------------------------
# edge-tts rate/pitch 整数化 + reload 真读盘（2026-10-24 实测修复）
# ---------------------------------------------------------------------------

class TestEdgeTtsIntegerEmission:
    """edge-tts 7.3.1 校验 ^[+-]\\d+%$ / Hz（实证 +7.88% REJECT、+8% OK；
    "+7.88Hz" REJECT）——随机语速因子的小数必须整数化，否则弹幕链合成全灭
    （"Invalid rate '+7.88%'"）。pitch 联动同源（speed_to_edge_pitch），一并锁定。
    """

    def test_random_speed_factor_emits_integer_rate(self):
        assert speed_to_edge_rate(1.0788) == "+8%"
        assert speed_to_edge_rate(0.938) == "-6%"

    def test_pitch_emits_integer_hz(self):
        assert speed_to_edge_pitch(1.0788) == "+4Hz"   # (1.0788-1)*50=3.94 → 4

    def test_pitch_clamp_boundary_still_integer(self):
        assert speed_to_edge_pitch(0.25) == "-38Hz"    # 原 -37.5Hz 即崩点形态
        assert speed_to_edge_pitch(4.0) == "+50Hz"


class TestUtilsMixinReloadReadsDisk:
    """2026-10-24 实测修复：reload_config 必须真读盘。

    缺陷现场：utils/config.py Config 构造在类级缓存非 None 时 no-op——
    UI 改 edge_tts.rate 落盘后，页面测试链的"重载配置"仍读启动快照，
    语速恒为默认 +0%（无论用户怎么设置都无效的根因）。
    """

    def test_reload_config_sees_disk_changes(self, tmp_path, monkeypatch):
        import utils.audio.utils_manager as um
        # MY_TTS 换轻量替身（避免拖起 SSL/Common 全量构造；真 Config 参与断言）
        monkeypatch.setattr(um, "MY_TTS", lambda path: types.SimpleNamespace(config=um.Config(path)))
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"edge_tts": {"rate": "+10%"}}), encoding="utf-8")
        u = object.__new__(um.UtilsMixin)
        try:
            u.reload_config(str(cfg_file))
            assert u.config.get("edge_tts", "rate") == "+10%"
            assert u.my_tts.config.get("edge_tts", "rate") == "+10%"
            # 磁盘变更后再 reload——必须看到新值（修复前恒旧快照）
            cfg_file.write_text(json.dumps({"edge_tts": {"rate": "+30%"}}), encoding="utf-8")
            u.reload_config(str(cfg_file))
            assert u.config.get("edge_tts", "rate") == "+30%"
        finally:
            # 类级缓存进程全局——复位防污染后续测试（同 test_config setup_method 口径）
            um.Config.config = None
            um.Config._config_path = None
            um.Config._is_multi_file = False
            um.Config._config_files = []


# ---------------------------------------------------------------------------
# v2.2 流程优化（2026-10-26 批次，flowchartTD |助播流程| 边）：助播条目绕过数字人
# ---------------------------------------------------------------------------

class TestAssistantPlaybackRouting:
    """routes_to_edtalk 路由判定：助播条目（assistant_anchor_text/audio）在
    EDTalk 激活时也走本地外放（无口型语义），普通条目仍按 EDTalk 激活态分流。"""

    @staticmethod
    def _config(visual_body="edtalk_realtime", enable=True):
        class _Cfg:
            def get(self, *keys):
                if keys == ("visual_body",):
                    return visual_body
                if keys == ("edtalk_realtime", "enable"):
                    return enable
                return None
        return _Cfg()

    def test_assistant_entry_bypasses_edtalk_even_when_active(self):
        from utils.audio.playback_manager import routes_to_edtalk
        cfg = self._config(visual_body="edtalk_realtime", enable=True)
        for t in ("assistant_anchor_text", "assistant_anchor_audio"):
            assert routes_to_edtalk({"type": t, "content": "助播台词"}, cfg) is False

    def test_normal_entry_routes_to_edtalk_when_active(self):
        from utils.audio.playback_manager import routes_to_edtalk
        cfg = self._config(visual_body="edtalk_realtime", enable=True)
        assert routes_to_edtalk({"type": "comment", "content": "普通回复"}, cfg) is True

    def test_normal_entry_local_when_edtalk_inactive(self):
        from utils.audio.playback_manager import routes_to_edtalk
        cfg = self._config(visual_body="edtalk_realtime", enable=False)
        assert routes_to_edtalk({"type": "comment", "content": "普通回复"}, cfg) is False

    def test_assistant_types_match_producer_side(self):
        """助播类型字面量与产出/优先级映射侧同名（防两处漂移）。"""
        import inspect
        from utils.audio import queue_manager
        from utils.audio.playback_manager import ASSISTANT_PLAYBACK_TYPES
        src = inspect.getsource(queue_manager)
        for t in ASSISTANT_PLAYBACK_TYPES:
            assert t in src, f"playback_manager 助播类型 {t} 在 queue_manager 侧无同名产出"


class TestConsumptionReceipt:
    """G2（2026-10-28 消费回传批次）：Sort 侧显式消费回执 API
    （report_consumed/get_consumption_stats——图 v2.6 回传子图的 Sort 侧落点）。"""

    def _host(self):
        from utils.audio.queue_manager import QueueMixin
        # 回执计数是 QueueMixin 类属性（跨实例共享）——每例重置防测试间污染
        QueueMixin._consumed_receipts = []
        QueueMixin._consumed_total = 0
        QueueMixin._consumed_by_type = {}
        return _QueueFixture()

    def test_receipt_ring_and_stats(self):
        host = self._host()
        host.report_consumed("song", "music/a.mp3")
        host.report_consumed("comment", "你好世界")
        stats = host.get_consumption_stats()
        assert stats["total_consumed"] == 2
        assert stats["by_type"] == {"song": 1, "comment": 1}
        assert stats["recent"][-1]["content"] == "你好世界"
        assert stats["recent"][-1]["type"] == "comment"

    def test_receipt_content_truncated(self):
        host = self._host()
        host.report_consumed("comment", "x" * 200)
        assert len(host.get_consumption_stats()["recent"][-1]["content"]) == 80

    def test_receipt_ring_window_capacity(self):
        from utils.audio.queue_manager import _CONSUMED_RECEIPT_MAX
        host = self._host()
        for i in range(_CONSUMED_RECEIPT_MAX + 10):
            host.report_consumed("comment", f"c{i}")
        stats = host.get_consumption_stats()
        assert stats["total_consumed"] == _CONSUMED_RECEIPT_MAX + 10
        assert len(stats["recent"]) == 10  # 快照只透出最近 10 条
        assert stats["recent"][-1]["content"] == f"c{_CONSUMED_RECEIPT_MAX + 9}"

    def test_get_audio_info_aggregates_builtin_backlog(self):
        """TODOS 2026-10-28 审查 #4：wait_play_audio_num 叠加 builtin 内部队列
        深度（play() 提前移交的条目脱离 voice_tmp_path_queue，观测面防低估）。"""
        host = self._host()

        class _FakeBuiltin:
            def internal_queue_len(self):
                return 7

        host.audio_player = _FakeBuiltin()
        host.voice_tmp_path_queue = [{"a": 1}, {"b": 2}]
        host.message_queue = []
        info = host.get_audio_info()
        assert info["wait_play_audio_num"] == 9  # 队列 2 + builtin 内部 7
        assert info["wait_synthesis_msg_num"] == 0

    def test_get_audio_info_without_builtin_player(self):
        """未挂播放器（pygame 模式/未构造）不叠加——保持原语义。"""
        host = self._host()
        host.voice_tmp_path_queue = [{"a": 1}]
        host.message_queue = []
        assert host.get_audio_info()["wait_play_audio_num"] == 1


# ---------------------------------------------------------------------------
# O7（2026-10-28 消费批次）：编排音轨插队（priority_override）双队列识别
# ---------------------------------------------------------------------------

class TestOverridePriority:
    """O7：条目显式 priority_override 优先于类型映射表（编排固定 500）。

    插队权重必须同时作用于两队列的排序/取代/驱逐判定——否则 500 条目被
    误按映射权重（如 gift=30）参与比较，插队失效或被误驱逐。
    """

    def _fixture(self, max_len=100):
        mapping = {t: int(t[1:]) for t in
                   ("w999", "w200", "w150", "w80", "w20", "w10", "w50", "w90", "w60", "w120", "w100", "w30", "w40", "w70")}
        return _QueueFixture(config=_FakeConfig(
            priority_mapping=mapping, max_len=max_len, min_start_play=0,
            extra={("filter", "message_queue_max_len"): 100}))

    def _override_item(self, label):
        return {"type": "w10", "voice_path": f"w10-{label}.wav", "priority_override": 500}

    def test_audio_queue_inserts_head(self):
        """音频队列：override 条目插到队首（高于全部映射权重）。"""
        fx = self._fixture()
        self._build_like(fx)
        r = fx.data_priority_insert("待播放音频列表", self._override_item("choreo"))
        assert r["code"] == 200
        assert fx.voice_tmp_path_queue[0]["voice_path"] == "w10-choreo.wav"
        assert fx.voice_tmp_path_queue[0]["priority_override"] == 500

    def _build_like(self, fx):
        for i, w in enumerate(("w120", "w90", "w80", "w50", "w30", "w10")):
            assert fx.data_priority_insert("待播放音频列表", _item(w, f"old{i}"))["code"] == 200

    def test_audio_queue_eviction_keeps_override(self):
        """音频队列满载：override 入队驱逐最低优映射条目，不驱逐既有 override。"""
        fx = self._fixture(max_len=4)
        for i, w in enumerate(("w80", "w50", "w30", "w10")):
            fx.data_priority_insert("待播放音频列表", _item(w, f"old{i}"))
        fx.data_priority_insert("待播放音频列表", self._override_item("keep"))
        assert fx.voice_tmp_path_queue[0]["voice_path"] == "w10-keep.wav"
        # 再来一个 override：同权（500）组取代组首（keep 让位），满载驱逐最低优 w10-old3
        fx.data_priority_insert("待播放音频列表", self._override_item("next"))
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w10-next.wav", "w80-old0.wav", "w50-old1.wav", "w30-old2.wav"]

    def test_message_queue_inserts_head(self):
        """消息队列：override 条目插到队首。"""
        fx = self._fixture()
        for i, w in enumerate(("w120", "w90", "w50")):
            fx.data_priority_insert("等待合成消息", {"type": w, "content": f"old{i}"})
        fx.data_priority_insert("等待合成消息",
                                {"type": "w10", "content": "choreo", "priority_override": 500})
        assert fx.message_queue[0]["content"] == "choreo"
        assert [d["type"] for d in fx.message_queue] == ["w10", "w120", "w90", "w50"]

    def test_override_unparseable_falls_back(self):
        """非法 override 值回退类型映射权重（不崩溃、不插队）。"""
        fx = self._fixture()
        for w in ("w90", "w50"):
            fx.data_priority_insert("待播放音频列表", _item(w, "old"))
        bad = {"type": "w10", "voice_path": "w10-bad.wav", "priority_override": "abc"}
        assert fx.data_priority_insert("待播放音频列表", bad)["code"] == 200
        labels = [d["voice_path"] for d in fx.voice_tmp_path_queue]
        assert labels == ["w90-old.wav", "w50-old.wav", "w10-bad.wav"]


class TestMessageQueueOverflowEvict:
    """M1/M2（消费批次）：消息队列满载高优插入=插队+驱逐队尾（净长度不变）；
    低优/未识别类型队满仍丢弃。扫描/判定/插入锁内化（M2 竞态根修）。"""

    def _fixture(self, max_len=3):
        mapping = {"w90": 90, "w50": 50, "w10": 10}
        return _QueueFixture(config=_FakeConfig(
            priority_mapping=mapping, max_len=max_len, min_start_play=0,
            extra={("filter", "message_queue_max_len"): max_len}))

    def _msg(self, t, content):
        return {"type": t, "content": content}

    def test_full_low_priority_dropped(self):
        """队满+低优插入位次在末尾 → 丢弃（原语义保持）。"""
        fx = self._fixture()
        for i, w in enumerate(("w90", "w50", "w10")):
            fx.data_priority_insert("等待合成消息", self._msg(w, f"old{i}"))
        r = fx.data_priority_insert("等待合成消息", self._msg("w10", "new"))
        assert r["code"] == 1
        assert [d["content"] for d in fx.message_queue] == ["old0", "old1", "old2"]

    def test_full_high_priority_evicts_tail(self):
        """M1：队满+高优插队 → 插队（同权组首之后）并驱逐队尾（最低优），净长度不变。"""
        fx = self._fixture()
        for i, w in enumerate(("w90", "w50", "w10")):
            fx.data_priority_insert("等待合成消息", self._msg(w, f"old{i}"))
        r = fx.data_priority_insert("等待合成消息", self._msg("w90", "new"))
        assert r["code"] == 200
        assert len(fx.message_queue) == 3  # 净长度不变（无界超长根修）
        # 插在 w90 组首（old0）之后；被驱逐的是队尾 w10-old2（最低优组 FIFO 尾）
        assert [d["content"] for d in fx.message_queue] == ["old0", "new", "old1"]

    def test_full_unknown_type_dropped(self):
        """队满+未识别类型（插入末尾）→ 丢弃。"""
        fx = self._fixture()
        for i, w in enumerate(("w90", "w50", "w10")):
            fx.data_priority_insert("等待合成消息", self._msg(w, f"old{i}"))
        r = fx.data_priority_insert("等待合成消息", {"content": "no-type"})
        assert r["code"] == 1

    def test_cancel_choreo_audio(self):
        """O8：cancel_choreo_audio 从两队列移除匹配 step_id 条目，其余保留。"""
        fx = self._fixture(max_len=100)
        fx.message_queue.extend([
            {"type": "gift", "content": "a", "choreo_step_id": "s1"},
            {"type": "gift", "content": "b"},
        ])
        fx.voice_tmp_path_queue.extend([
            {"type": "song", "voice_path": "c.wav", "choreo_step_id": "s1"},
            {"type": "song", "voice_path": "d.wav", "choreo_step_id": "s2"},
        ])
        removed = fx.cancel_choreo_audio("s1")
        assert removed == 2
        assert [d["content"] for d in fx.message_queue] == ["b"]
        assert [d["voice_path"] for d in fx.voice_tmp_path_queue] == ["d.wav"]
        assert fx.cancel_choreo_audio("") == 0  # 空步防呆


class TestPlaybackConsumptionHooks:
    """M3（消费批次）：播放路径消费回传（report_consumed+编排回调），
    EDTalk/builtin 两路共用 _notify_choreo_complete 提取。

    2026-10-30 审查批次：本地播放统一 builtin——原 外部/pygame 路径随
    「音频播放器」配置退役删除，三路收敛为两路；builtin 真实完成点经
    audio_core 注册的完成回调二次回执（G1，互斥不双报由回调注册点保证）。
    """

    def test_source_wiring(self):
        import inspect
        from utils.audio import playback_manager
        src = inspect.getsource(playback_manager)
        assert src.count("report_consumed(") >= 2, \
            "EDTalk/builtin 两路均须调用 report_consumed（M3 断链补齐）"
        assert "_notify_choreo_complete" in src
        # 两路完成回传统一走 helper（既有 mute 守卫/song 直通另有 2 处合法调用点）
        assert src.count("get_choreographer()") <= 3


# ---------------------------------------------------------------------------
# T1-批（2026-11-06 协作机制流程审查，W1 根修）：音频队列锁内化并发守卫
# ---------------------------------------------------------------------------

class TestT1ConcurrentInsert:
    """音频队列 A1 四规则判定锁内化（对齐 message_queue 分支 M1/M2 模式）——
    多生产者竞态关闭后的确定性守卫：999 单实例在任意交错下恰好 1 条、
    满载并发驱逐净长度不变且权重序保持。"""

    def _fixture(self, max_len, min_start_play=1):
        mapping = {t: int(t[1:]) for t in ("w999", "w500", "w200", "w150", "w100", "w80", "w50", "w30", "w20")}
        mapping["talk"] = 60
        cfg = _FakeConfig(priority_mapping=mapping, min_start_play=min_start_play, max_len=max_len)
        return _QueueFixture(config=cfg)

    def test_concurrent_999_single_instance(self):
        """双生产者并发 999 入队——单实例约束在任意交错下恰好放行 1 条（W1 竞态格）。
        修复前：999 检查（锁外）与插入（锁内）分离，并发双检查双通过可产生 2 条。"""
        host = self._fixture(max_len=100)
        n = 8
        barrier = threading.Barrier(n)
        results = []

        def producer(i):
            barrier.wait()
            results.append(host.data_priority_insert(
                "待播放音频列表", _item("w999", f"p{i}")))

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        n999 = sum(1 for it in host.voice_tmp_path_queue if it.get("type") == "w999")
        assert n999 == 1, f"999 单实例被并发突破：队列中 {n999} 条 999"
        assert sum(1 for r in results if r.get("code") == 200) == 1

    def test_concurrent_full_queue_eviction_invariant(self):
        """满载并发混权重插入——净长度不变 + 定义权重条目降序不变量在任意交错下保持。
        修复前：规则 2/3 组定位在锁外计算，并发下驱逐索引漂移可破坏序/错删。"""
        max_len = 12
        host = self._fixture(max_len=max_len)
        for i in range(max_len):
            host.data_priority_insert("待播放音频列表", _item("w50", f"seed{i}"))
        weights = ["w100", "w30", "w200", "w20", "w150", "w80", "w100", "w30", "w200", "w20", "w150", "w80"]
        barrier = threading.Barrier(len(weights))

        def producer(i):
            barrier.wait()
            host.data_priority_insert("待播放音频列表", _item(weights[i], f"c{i}"))

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(len(weights))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        from utils.audio.audio_priority import entry_priority
        mapping = host.config.get("filter", "priority_mapping")
        prios = [entry_priority(it, mapping) for it in host.voice_tmp_path_queue]
        assert len(host.voice_tmp_path_queue) == max_len, "满载并发插入后净长度必须不变"
        defined = [p for p in prios if p is not None]
        assert defined == sorted(defined, reverse=True), "权重降序不变量被并发破坏"

    def test_discard_and_interrupt_fire_only_on_insert(self):
        """锁外收尾语义：999 被单实例拒绝时不触发 D-22 舍弃通知与让位钩子；
        成功插入时两者都触发（插入成功才触发的触发集合不变量）。"""
        mapping = {t: int(t[1:]) for t in ("w999", "w50")}
        mapping["talk"] = 60
        cfg = _FakeConfig(priority_mapping=mapping, min_start_play=1, max_len=100,
                          extra={("talk", "continuous_talk"): True})
        host = _QueueFixture(config=cfg)
        calls = {"discard": [], "interrupt": []}
        host._continuous_talk_discard_notify = lambda count: calls["discard"].append(count)
        host._maybe_interrupt_999_for = lambda dj: calls["interrupt"].append(dj.get("type"))

        # 1) 首条 999 成功插入 → interrupt 触发、discard 不触发（非 talk）
        r1 = host.data_priority_insert("待播放音频列表", _item("w999", "first"))
        assert r1["code"] == 200
        assert calls["interrupt"] == ["w999"] and calls["discard"] == []

        # 2) 第二条 999 被单实例拒绝 → 两者都不触发
        r2 = host.data_priority_insert("待播放音频列表", _item("w999", "second"))
        assert r2["code"] == 1 and "999" in r2["msg"]
        assert calls["interrupt"] == ["w999"] and calls["discard"] == []

        # 3) talk 条目成功插入且待播 ≥2 → discard 触发（连续对话语义保留）
        host.voice_tmp_path_queue.append(_item("w50", "pending1"))
        r3 = host.data_priority_insert("待播放音频列表", {"type": "talk", "voice_path": "talk.wav"})
        assert r3["code"] == 200
        assert len(calls["discard"]) == 1 and calls["interrupt"][-1] == "talk"


# ---------------------------------------------------------------------------
# T2-批（2026-11-06 协作机制流程审查，W2 根修）：only_play_audio 999 active
# 标记 finally 化——异常逃逸不再悬挂复读专线
# ---------------------------------------------------------------------------

class _EdtalkConfig:
    """only_play_audio 依赖的最小配置桩（EDTalk 激活态）"""

    def __init__(self, max_len=100):
        self._data = {
            ("filter", "priority_mapping"): {"reread_top_priority": 999, "comment": 20},
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("filter", "voice_tmp_path_queue_min_start_play"): 1,
            ("filter", "message_queue_max_len"): 100,
            ("play_audio", "enable"): True,
            ("play_audio", "song_segment_seconds"): 0,
            ("visual_body"): "edtalk_realtime",
            ("edtalk_realtime", "enable"): True,
            ("talk", "continuous_talk"): False,
            ("webui", "ip"): "127.0.0.1",
            ("webui", "port"): 8086,
        }

    def get(self, *keys):
        if len(keys) == 1:
            return self._data.get(keys[0])
        return self._data.get(keys)


class _EdtalkClientStub:
    """edtalk_client 桩：push_full 受理成功；get_output_frames 恒 0"""

    def __init__(self):
        self.push_fired = threading.Event()
        self.flag_during_push = []

    async def push_full(self, pcm_path, content="", wait_flight=True):
        import time as _t
        self.push_fired.set()
        _t.sleep(0.05)
        return True

    def get_output_frames(self):
        return 0

    def pause_output(self):
        return {"ok": True}

    def resume_output(self):
        return {"ok": True}


class _PlaybackHost(QueueMixin, PlaybackMixin):
    """only_play_audio 最小宿主（QueueMixin+PlaybackMixin 手工组装）"""

    def __init__(self, config):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.config = config
        self.active_reread_top_999 = False
        self.edtalk_client = _EdtalkClientStub()


class TestT2NineRelease:
    """驱动拓扑对齐生产：only_play_audio 在专用线程的自有事件循环中运行
    （其空队列等待=threading.Condition.wait 阻塞调用，与 scenario 同线程
    会冻结整个循环）；主线程以阻塞轮询断言，结束时依赖 daemon 线程退场。"""

    def _wait_until(self, cond, timeout=5.0):
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            if cond():
                return True
            _t.sleep(0.01)
        return False

    def _start_worker(self, host):
        def _run():
            asyncio.new_event_loop().run_until_complete(host.only_play_audio())
        t = threading.Thread(target=_run, daemon=True, name="t2-playback-worker")
        t.start()
        return t

    def test_exception_escape_releases_999_flag(self):
        """W2：EDTalk 分支异常逃逸（push 成功后字幕推送注入失败）→ 999 active
        标记必须释放（finally）。修复前：释放点仅在正常完成路径，逃逸即悬挂，
        后续 999 全部被单实例约束拒绝=复读专线死链。"""
        host = _PlaybackHost(_EdtalkConfig())

        async def _boom(_data):
            raise RuntimeError("injected failure after push")
        host._captions_push = _boom

        self._start_worker(host)
        # 先入队（worker 已起，condition notify 正常唤醒）
        r = host.data_priority_insert("待播放音频列表", {"type": "reread_top_priority", "voice_path": "x.wav", "content": "999"})
        assert r["code"] == 200
        assert host.edtalk_client.push_fired.wait(5), "push_full 未被受理"
        assert self._wait_until(lambda: host.active_reread_top_999 is False), \
            "异常逃逸后 999 active 标记未释放（finally 缺失）"
        # 逃逸后单实例约束已解除（可直接再入队 999）
        r2 = host.data_priority_insert("待播放音频列表", {"type": "reread_top_priority", "voice_path": "y.wav", "content": "999-again"})
        assert r2["code"] == 200, "999 标记悬挂导致单实例约束误拒（W2 复现）"
        assert self._wait_until(lambda: host.active_reread_top_999 is False and host.edtalk_client.push_fired.is_set())

    def test_normal_path_releases_after_complete(self):
        """正常路径回归：push 时标记为 True（播放中持有）、完成后释放（原语义不变）。"""
        host = _PlaybackHost(_EdtalkConfig())
        flag_seen = {}

        orig_push = host.edtalk_client.push_full

        async def push_full(pcm_path, content="", wait_flight=True):
            flag_seen["during"] = host.active_reread_top_999
            return await orig_push(pcm_path, content, wait_flight)
        host.edtalk_client.push_full = push_full

        self._start_worker(host)
        r = host.data_priority_insert("待播放音频列表", {"type": "reread_top_priority", "voice_path": "x.wav", "content": "999"})
        assert r["code"] == 200
        assert host.edtalk_client.push_fired.wait(5)
        assert self._wait_until(lambda: host.active_reread_top_999 is False)
        assert flag_seen.get("during") is True, "push 受理时刻 999 active 标记应已置位"


# ---------------------------------------------------------------------------
# T3-批（2026-11-06 协作机制流程审查，W3 根修+uc1b 裁决落地）：消费回执单源
# ---------------------------------------------------------------------------

class TestT3ReceiptSingleSource:
    """本地（builtin）路径观测回执收敛至真实完成点单点——移交点不再双报。
    源级断言钉死调用点面：playback_manager 仅 EDTalk 两路（主路径+999 重推）、
    audio_core 完成回调一处。"""

    def test_local_branch_has_no_observation_receipt(self):
        import inspect
        from utils.audio import playback_manager
        src = inspect.getsource(playback_manager)
        # 模块内 report_consumed 调用点=2：EDTalk 主路径 + 999 重推条目（R5b
        # 本地外放回滚后借位走 EDTalk 主路径，无独立回执点；本地 builtin 移交
        # 分支必须为零——W3 双计根修）
        count = src.count("self.report_consumed(")
        assert count == 2, f"playback_manager report_consumed 调用点应恰 2（EDTalk 主路径+重推），实为 {count}"
        # 本地分支无观测回执（移交点仅编排实例匹配）
        local_src = src[src.index("本地整轨（用户优化）"):]
        assert "self.report_consumed(" not in local_src, "本地分支残留观测回执（双计复现）"

    def test_audio_core_completion_receipt_single_point(self):
        import inspect
        from utils.audio import audio_core
        src = inspect.getsource(audio_core)
        # audio_core 完成回调一处（G2 完成点②）——builtin 真实完成回执单点
        count = src.count("self.report_consumed(")
        assert count == 1, f"audio_core report_consumed 调用点应恰 1（完成回调），实为 {count}"

    def test_choreo_step_id_passthrough_on_rebuild(self):
        """Q-1：本地分支条目重建透传 choreo_step_id（编排实例精确匹配不再退化）。"""
        import inspect
        from utils.audio import playback_manager
        src = inspect.getsource(playback_manager)
        assert src.count('"choreo_step_id": data_json.get("choreo_step_id", "")') == 2, \
            "insert_index/常规两条重建分支均须透传 choreo_step_id"

    def test_flowchart_v211_receipt_semantics(self):
        """uc1b 裁决落图：v2.11 图头在位+回传边语义=观测回执。"""
        from tests.test_flowchart_alignment import _graph_text
        text = _graph_text()
        assert "v2.11" in text and "不驱动下一消费" in text, "图头 v2.11 回传语义注记缺失"


# ---------------------------------------------------------------------------
# T6-批（2026-11-06 协作机制流程审查，F-ENG-3 根修）：CancelAudio×合成完成
# 竞态拦截——撤销登记表 + 迟到入队丢弃
# ---------------------------------------------------------------------------

class TestT6CancelLateEnqueue:
    """撤销登记语义：cancel 后（无论扫描是否命中）TTL 内同 step_id 入队一律拦截——
    撤旧扫描与合成完成入队之间的竞态窗口孤儿音轨在任意交错下被拦截。"""

    def setup_method(self):
        QueueMixin._cancelled_choreo_steps.clear()

    def teardown_method(self):
        QueueMixin._cancelled_choreo_steps.clear()

    def _fixture(self, max_len=100):
        mapping = {"comment": 20, "reread_top_priority": 999}
        cfg = _FakeConfig(priority_mapping=mapping, min_start_play=1, max_len=max_len,
                          extra={("filter", "message_queue_max_len"): max_len})
        return _QueueFixture(config=cfg)

    def test_cancelled_step_blocks_late_enqueue_both_queues(self):
        """撤旧登记后迟到入队（音频面+消息面）一律丢弃（竞态窗口孤儿拦截）。"""
        host = self._fixture()
        removed = host.cancel_choreo_audio("s1")  # 队列为空也登记（竞态面本体）
        assert removed == 0
        r_audio = host.data_priority_insert("待播放音频列表", {
            "type": "comment", "voice_path": "late.wav", "choreo_step_id": "s1"})
        assert r_audio["code"] == 1 and "已撤销" in r_audio["msg"]
        r_msg = host.data_priority_insert("等待合成消息", {
            "type": "comment", "content": "迟到答谢", "choreo_step_id": "s1"})
        assert r_msg["code"] == 1 and "已撤销" in r_msg["msg"]

    def test_unrelated_step_and_no_step_unaffected(self):
        host = self._fixture()
        host.cancel_choreo_audio("s1")
        r_other = host.data_priority_insert("待播放音频列表", {
            "type": "comment", "voice_path": "other.wav", "choreo_step_id": "s2"})
        assert r_other["code"] == 200
        r_plain = host.data_priority_insert("待播放音频列表", {
            "type": "comment", "voice_path": "plain.wav"})
        assert r_plain["code"] == 200

    def test_ttl_expiry_allows_reuse(self):
        """TTL 过期后登记失效（同 step_id 复用不误拦——序列序号防跨序列撞车，
        TTL 兜底防御登记表无界）。"""
        import time as _t
        host = self._fixture()
        host.cancel_choreo_audio("s1")
        QueueMixin._cancelled_choreo_steps["s1"] = _t.monotonic() - QueueMixin._CANCELLED_STEP_TTL_S - 1
        r = host.data_priority_insert("待播放音频列表", {
            "type": "comment", "voice_path": "after-ttl.wav", "choreo_step_id": "s1"})
        assert r["code"] == 200

    def test_scan_still_removes_queued_entries(self):
        """既有 O8 扫描语义回归：队列内匹配条目仍被同步移除+登记。"""
        host = self._fixture()
        host.data_priority_insert("待播放音频列表", {
            "type": "comment", "voice_path": "inflight.wav", "choreo_step_id": "s1"})
        host.data_priority_insert("等待合成消息", {
            "type": "comment", "content": "queued", "choreo_step_id": "s1"})
        removed = host.cancel_choreo_audio("s1")
        assert removed == 2
        assert all(d.get("choreo_step_id") != "s1" for d in host.voice_tmp_path_queue)
        assert all(d.get("choreo_step_id") != "s1" for d in host.message_queue)
