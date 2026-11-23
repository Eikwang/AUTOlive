"""T6 让位泛化+EDTalk 任意点暂停守卫（2026-11-05 编排优化批次；
2026-11-15 批次：999 机制退役，e3 泛化让位保留为唯一让位面）。

覆盖裁决条款：
- e3：builtin 让位设施泛化——任意在播条目受理让位（判定面=_playing_weight）；
  续播期再让位=重新挂起（条目不丢——泛化引入边缘的收口）
- d12：queue_manager 让位裁决 b 触发（严格更高权重新音频，song 让位给答谢）；
  同权/更低不触发；分桶 builtin_yield_priority（2026-11-15 批次：a 触发随
  999 机制退役，裁决入口改名 _maybe_priority_yield_for——barge-in 打断即弃钩子①+让位②）
- F-A/uc2b spike 实现：OutputStreamer 任意点暂停门（暂停=消费冻结零丢帧/
  恢复=精确帧位续播）+service 端点+client 降级包装（2026-11-15 批次：999 让位
  编排退役，暂停端点保留为服务端控制面通用能力）
- F-I：新端点纳入 app 级 _auth_guard（api_token 配置时 401）
"""
import os
import sys
import threading

import pytest

from utils.audio.queue_manager import QueueMixin

# edtalk 服务模块按独立包根导入（realtime_serve 顶层——服务 standalone 布局；
# 沿 test_edtalk_segment_scheduler 的 sys.path 注入惯例，service 模块级仅
# numpy/config 轻依赖）
_EDTALK_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "edtalk")
if _EDTALK_DIR not in sys.path:
    sys.path.insert(0, _EDTALK_DIR)


# ------------------------------------------------- e3 播放器侧泛化

def _player(weight=None, suspended=None):
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p._playing_weight = weight  # e3/d12：泛化让位判定源（2026-11-15 批次 999 退役）
    p._suspended_999 = suspended
    p._interrupt_999_event = threading.Event()
    return p


def test_e3_request_yield_playing():
    """e3：在播条目受理让位（泛化核心——2026-11-15 批次 999 退役后判定面
    统一为 _playing_weight）。"""
    p = _player(weight=10)  # song 切片在播
    p.request_yield()
    assert p._interrupt_999_event.is_set()


def test_e3_yield_ignored_when_suspended():
    p = _player(weight=500, suspended={"type": "reread_top_priority"})
    p.request_yield()
    assert not p._interrupt_999_event.is_set()  # R2-A7c 排队不打断


def test_e3_yield_ignored_when_idle():
    p = _player(weight=None)
    p.request_yield()
    assert not p._interrupt_999_event.is_set()  # 空闲不置让位（防陈旧事件）


def test_e3_resume_resuspends_instead_of_dropping():
    """续播期再让位=重新登记挂起（泛化引入边缘收口：原 resume 路径仅处理
    completed——再让位会放走条目）。"""
    import time as _t
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p._playing_weight = 500
    p._current_output_rate = None
    p.pause_event = threading.Event()
    p.stream = None
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p._interrupt_999_event = threading.Event()
    p._suspended_999 = {"voice_path": "x.mp3"}
    p._suspended_play = {"wf": None, "tmp_audio_path": None}
    p._suspended_999_at = _t.monotonic()
    p._suspended_999_alarm_at = None
    p._interruptor_pending = False
    p._fire_completion = lambda item: None
    p._finish_cleanup = lambda *a, **k: None
    p._post_play_interval = lambda: None
    p._wait_loop = lambda: "suspended"  # 续播期再次让位
    p._resume_suspended_999()
    assert p._suspended_999 == {"voice_path": "x.mp3"}  # 重新挂起（条目不丢）
    assert p._interruptor_pending is True
    assert p._suspended_999_at is not None


# ------------------------------------------------- d12 队列侧 b 裁决

class _FakeConfig:
    def __init__(self, mapping):
        self._m = {"filter": {"priority_mapping": mapping}}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _YieldPlayer:
    def __init__(self, playing_weight=None):
        self.playing_weight = playing_weight
        self.yields = 0

    def request_yield(self):
        self.yields += 1

    def get_playing_weight(self):
        return self.playing_weight


class _QueueFixture(QueueMixin):
    def __init__(self, mapping, player):
        self.config = _FakeConfig(mapping)
        self.audio_player = player
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False


PM = {"comment": 20, "gift": 20, "song": 10, "reread_top_priority": 500}


def test_d12_trigger_b_higher_priority_yields_song():
    """d12 核心（2026-11-15 批次：999 退役，_maybe_interrupt_999_for 改名
    _maybe_priority_yield_for）：song(10) 播放中 comment(20) 入队 → 让位（builtin
    降级语义保留）。"""
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=10))
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 1


def test_d12_equal_or_lower_no_yield():
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=20))
    q._maybe_priority_yield_for({"type": "gift", "voice_path": "x.mp3"})  # 同权 20
    q._maybe_priority_yield_for({"type": "song", "voice_path": "x.mp3"})  # 低权 10
    assert q.audio_player.yields == 0


def test_d12_no_player_no_crash():
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=10))
    q.audio_player = None
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})  # 不抛


# ------------------------------------------------- F-A spike：OutputStreamer 暂停门

def _streamer_with_frames(n=4):
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer  # noqa: E402
    fb = FrameBuffer(capacity=8)
    for i in range(n):
        fb.put(np.zeros((2, 2), dtype="uint8"), {"audio_start": i == 0})
    sent = []
    st = OutputStreamer(frame_buffer=fb, target_fps=25, sender=sent.append,
                        audio_player=None, preroll_frames=0)
    return st, fb, sent


def test_spike_pause_freezes_consumption():
    """F-A 核心：暂停=消费冻结（缓冲零弹出）；恢复=精确帧位续播（零丢帧）。"""
    import time as _t
    st, fb, sent = _streamer_with_frames(4)
    st.set_paused(True)
    st.start()
    _t.sleep(0.25)
    assert fb.depth == 4  # 暂停期消费冻结（缓冲原样保留）
    assert len(sent) == 0
    st.set_paused(False)
    _t.sleep(0.3)
    assert fb.depth == 0  # 恢复=精确帧位续播（零丢帧）
    assert len(sent) == 4
    st.stop()


def test_spike_service_endpoints_toggle():
    """service.pause/resume 幂等切换；未装配=NotEnabledError。"""
    from realtime_serve.service import NotEnabledError, RealtimeService  # noqa: E402
    svc = RealtimeService.__new__(RealtimeService)
    svc.output_streamer = None
    with pytest.raises(NotEnabledError):
        svc.pause_output()
    stub = type("S", (), {"set_paused": staticmethod(lambda v: None),
                          "set_paused_": None})()
    calls = []
    svc.output_streamer = type("OS", (), {"set_paused": lambda self, v: calls.append(v)})()
    assert svc.pause_output() == {"paused": True}
    assert svc.resume_output() == {"paused": False}
    assert calls == [True, False]


# ------------------------------------------------- 第三批：撕裂+视线修复（2026-11-21）

def _render_loop_cls():
    """导入 RenderLoop：模块顶层依赖 cv2/torch（测试环境未装）——桩替换。"""
    import sys
    import types
    if 'cv2' not in sys.modules:
        sys.modules['cv2'] = types.ModuleType('cv2')
    if 'torch' not in sys.modules:
        torch_stub = types.ModuleType('torch')
        torch_stub.Tensor = object  # 方法签名注解在 def 时求值
        sys.modules['torch'] = torch_stub
    from realtime_serve.render_loop import RenderLoop
    return RenderLoop


def test_gaze_grace_allows_segment_start():
    """段首视线宽限（批次五：宽限至深度重建）：armed 期间深度 0 也放行且
    保持 armed（深度爬升期 ~1s 不退回未修正眼）；帧数兜底耗尽或深度达标
    后退出，回归深度门控（GPU 余量保护语义不丢）。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer
    RenderLoop = _render_loop_cls()
    rl = RenderLoop.__new__(RenderLoop)
    rl.gaze_mode = 'adaptive'
    rl.frame_buffer = FrameBuffer(capacity=16, reserve=5)  # 空（深度 0）
    rl._gaze_grace = 1
    rl._gaze_grace_max = 150
    for _ in range(20):
        assert rl._gaze_allowed() is True  # 宽限内放行（深度 0 也修正）
    assert rl._gaze_grace == 1  # 深度未达标 → 宽限保持（原固定 6 帧已耗尽）
    rl._gaze_grace_max = 1
    assert rl._gaze_allowed() is True   # 兜底最后一帧仍放行
    assert rl._gaze_grace == 0          # 兜底耗尽 → 退出宽限
    assert rl._gaze_allowed() is False  # 回归深度门控（0 < 6 拒绝）
    # 深度重建达标 → 宽限自然退出且门控通过
    rl._gaze_grace = 1
    rl._gaze_grace_max = 150
    for _ in range(RenderLoop.REPAIR_MIN_BUFFER):
        rl.frame_buffer.put(np.zeros((2, 2), dtype='uint8'))
    assert rl._gaze_allowed() is True
    assert rl._gaze_grace == 0
    assert rl._gaze_allowed() is True   # 深度门控通过


def test_frame_buffer_flush_seg_keeps_hold_tail():
    """flush_seg 仅丢弃 meta['seg'] 语音帧，hold/idle 尾帧按 FIFO 序保留
    （顺序推送时画面/音频连续拼接，卡碟/跳帧修复）。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer
    fb = FrameBuffer(capacity=16, reserve=5)
    fb.put(np.zeros((2, 2), dtype='uint8'), {'seg': True, 'audio_start': False})
    fb.put(np.zeros((2, 2), dtype='uint8'), {'seg': True, 'audio_start': False})
    fb.put(np.zeros((2, 2), dtype='uint8'), {'audio_start': False})  # hold
    fb.put(np.zeros((2, 2), dtype='uint8'), None)                    # idle
    dropped = fb.flush_seg()
    assert dropped == 2
    assert fb.depth == 2
    kept0 = fb.get(timeout=0.01)
    kept1 = fb.get(timeout=0.01)
    assert kept0[1] == {'audio_start': False}   # hold 帧（FIFO 序首位）
    assert kept1[1] is None                     # idle 帧
    assert fb.flush_seg() == 0                  # 无语音帧时零丢弃


def test_flush_for_new_audio_clears_tail_and_counts_seg():
    """flush_for_new_audio：丢弃语音尾（计数）并清空 hold/idle 尾——新句帧
    不排队（拖音修复）；FIFO 缓冲排空到 0。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer
    fb = FrameBuffer(capacity=16, reserve=5)
    fb.put(np.zeros((2, 2), dtype='uint8'), {'seg': True, 'audio_start': False})
    fb.put(np.zeros((2, 2), dtype='uint8'), {'seg': True, 'audio_start': False})
    fb.put(np.zeros((2, 2), dtype='uint8'), {'audio_start': False})  # hold
    fb.put(np.zeros((2, 2), dtype='uint8'), None)                    # idle
    dropped = fb.flush_for_new_audio()
    assert dropped == 2          # 仅语音帧计数
    assert fb.depth == 0         # 整个尾部排空（hold/idle 不保留）
    assert fb.flush_for_new_audio() == 0


def test_gaze_grace_default_off_and_deep_buffer_normal():
    """默认无宽限（__new__ 路径显式赋值验证的是 _gaze_allowed 语义）；深度
    充足时正常放行——宽限窗口不改变既有门控行为。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer
    RenderLoop = _render_loop_cls()
    rl = RenderLoop.__new__(RenderLoop)
    rl.gaze_mode = 'adaptive'
    rl.frame_buffer = FrameBuffer(capacity=16, reserve=5)
    rl._gaze_grace = 0
    for _ in range(RenderLoop.REPAIR_MIN_BUFFER):
        rl.frame_buffer.put(np.zeros((2, 2), dtype='uint8'))
    assert rl._gaze_allowed() is True
    assert rl._gaze_grace == 0  # 深度路径不触碰宽限计数


def test_streamer_seg_displayed_counting():
    """chunk 帧 meta seg 标 → 显示计数 → stats 汇出（wait_flight 精确判定源；
    hold/idle 帧不打 seg 标不计入）。"""
    import time as _t
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer
    fb = FrameBuffer(capacity=8)
    fb.put(np.zeros((2, 2), dtype='uint8'), {'seg': True, 'audio_start': False})
    fb.put(np.zeros((2, 2), dtype='uint8'), {'audio_start': False})  # hold/idle 帧
    sent = []
    st = OutputStreamer(frame_buffer=fb, target_fps=25, sender=_bool_sender(sent),
                        audio_player=None, preroll_frames=0)
    st.start()
    _t.sleep(0.25)
    st.stop()
    assert len(sent) >= 2
    assert st.stats()['seg_displayed'] == 1  # 仅 seg 帧计入


def test_reset_seg_displayed_clears_counter():
    """段切换基线：reset_seg_displayed 清零当前段显示计数。"""
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer
    fb = FrameBuffer(capacity=4)
    st = OutputStreamer(frame_buffer=fb, target_fps=25, sender=lambda f: True,
                        audio_player=None, preroll_frames=0)
    st._seg_displayed = 11
    st.reset_seg_displayed()
    assert st.stats()['seg_displayed'] == 0


def test_two_segments_reset_displayed_counter():
    """段切换必须清零显示计数：第二段 stats.seg_displayed 只含本段，
    不得累计第一段。否则 /status 三字段基线不一致 → 客户端 wait_flight
    的 displayed>=produced 在第二段起恒成立 → 段刚产出完即放行 → 重叠
    推送 → flush 丢未显示尾帧 → 跳帧/尾音截断（2026-11-21 修复批次）。"""
    import time as _t
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer
    fb = FrameBuffer(capacity=8)
    sent = []
    st = OutputStreamer(frame_buffer=fb, target_fps=25, sender=_bool_sender(sent),
                        audio_player=None, preroll_frames=0)
    st.start()
    frame = np.zeros((2, 2), dtype='uint8')
    for _ in range(3):  # 第一段 3 个 seg 帧
        fb.put(frame, {'seg': True, 'audio_start': False})
    _t.sleep(0.3)
    assert st.stats()['seg_displayed'] == 3
    # 模拟 push 第二段：process_full_audio 调 reset_seg_displayed
    st.reset_seg_displayed()
    assert st.stats()['seg_displayed'] == 0
    for _ in range(2):  # 第二段 2 个 seg 帧
        fb.put(frame, {'seg': True, 'audio_start': False})
    _t.sleep(0.3)
    st.stop()
    # 只含第二段，不得累计为 3+2=5
    assert st.stats()['seg_displayed'] == 2


def test_streamer_underrun_pushes_silence_to_audio_player():
    """buffer 欠载（推理抖动致 frame_buffer 短暂排空）时也喂一帧静音，
    防 AudioPlayer 缓冲跑空 → WASAPI 补零 → 音节中插静音（拖音）。
    生产环境 GPU 有争用，此路径比探针环境（缓冲满载）更易触发。"""
    import time as _t
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer

    class FakePlayer:
        def __init__(self):
            self.pushes = []
        def push_pcm(self, chunk):
            self.pushes.append(np.asarray(chunk).copy())
        def reset(self):
            pass

    fb = FrameBuffer(capacity=2)   # 空缓冲 → 消费循环直接欠载
    ap = FakePlayer()
    st = OutputStreamer(frame_buffer=fb, target_fps=25, sender=lambda f: True,
                        audio_player=ap, preroll_frames=0)
    st.start()
    _t.sleep(0.3)  # 25fps → ~7 个欠载 tick
    st.stop()
    assert len(ap.pushes) > 0, 'underrun 期间未向 player 喂静音'
    for p in ap.pushes:
        assert p.shape == (640,), p.shape          # 25fps → 640 采样/帧
        assert not np.any(p)                       # 全零静音


def test_hold_silent_block_and_seg_marker_structure():
    """hold 帧静音块喂 DAC（撕裂修复 b：hold 期零推送致 DAC 断流爆音）+
    chunk 帧 seg 标/produced 计数（撕裂修复 a：句段完成度计数）。_process_frame
    深耦合 GPU 管线无法轻量行为隔离，以结构断言锁定修复面。"""
    import inspect
    RenderLoop = _render_loop_cls()
    src = inspect.getsource(RenderLoop._process_frame)
    # hold 分支：零音频块、不标 seg、不推进音频时间线
    assert "np.zeros(16000 // self.target_fps" in src
    assert "'audio_start': False" in src
    # chunk 分支：seg 标（仅有效语音帧，tail 静音不打标——2026-11-23 拖音
    # 修复）+ produced 计数（wait_flight 精确判定基线）
    assert "'seg': is_voice" in src
    assert "is_voice = _lip_idx < self._seg_chunk_total" in src
    assert "self._seg_chunk_produced += 1" in src


def test_flush_paths_arm_grace_and_seg_baseline():
    """flush 双路径（_activate_segment / switch_segment）均重臂视线宽限（宽限
    至深度重建语义）；激活时归零 seg_chunk 基线（total=T-tail / produced=0）
    且新句拼接仅丢弃语音帧（flush_for_new_audio）。

    2026-11-23 pending 自动衔接重构：process_full_audio 只做 pending 决策 +
    调 _activate_segment；flush/arm_grace/seg 基线集中在 _activate_segment
    （pending 预推理路径与直接激活路径共用）。

    2026-11-23 显示序时钟重构：flush 改为帧头 staged 执行（_flush_buffer 置
    位 → _process_frame 帧头 _do_frame_flush 执行 flush_for_new_audio 并把
    last_flush_total 计入 _flushed_frames），消除 in-flight 帧 idx 竞态、保证
    体态位置与实际显示严格一致。"""
    import inspect
    RenderLoop = _render_loop_cls()
    src_pf = inspect.getsource(RenderLoop.process_full_audio)
    src_act = inspect.getsource(RenderLoop._activate_segment)
    src_ss = inspect.getsource(RenderLoop.switch_segment)
    src_flush = inspect.getsource(RenderLoop._do_frame_flush)
    for src in (src_act, src_ss):
        assert "self._arm_gaze_grace()" in src
    # 2026-11-23 拖音修复：total 只计有效语音（T - tail_frames），使客户端/
    # 渲染线程精确判定在有效语音播完即放行，不必等 tail 静音显示完 → 段间零间隙。
    assert "self._seg_chunk_total = max(0, T - tail_frames)" in src_act
    assert "self._seg_chunk_produced = 0" in src_act
    # 批次六：激活路径排空整个尾部（语音尾截断 + idle/hold 尾清空），
    # 新句不排队 → 消除首音延迟拖音（详见 flush_for_new_audio 注释）。
    # 显示序时钟：激活/切换路径经 staged 标志，帧头统一执行 flush。
    assert "self._flush_buffer()" in src_act
    assert "self._flush_buffer()" in src_ss
    assert "flush_for_new_audio()" in src_flush
    # 显示序记账：被丢弃帧永不显示 → 计入 _flushed_frames（体态零跳变）。
    assert "last_flush_total" in src_flush
    assert "self._flushed_frames +=" in src_flush
    # 显示序时钟本体：位置 = 产出 − 丢弃 − 段基线（不再 wall-clock）。
    src_vidx = inspect.getsource(RenderLoop._video_pos)
    assert ("self._frame_index - self._flushed_frames - self._video_base"
            in src_vidx)
    # process_full_audio 必须委托推理与激活（pending 与直接路径都走 _activate_segment）
    assert "_infer_segment" in src_pf
    assert "_activate_segment" in src_pf


def test_status_reports_seg_completion_fields():
    """/status 汇出三字段：seg_chunk_displayed（streamer.stats）、
    seg_chunk_total/produced（render_loop 基线计数）——客户端精确判定面。"""
    from realtime_serve.service import RealtimeService
    svc = RealtimeService.__new__(RealtimeService)
    svc._started = True
    svc._stopped = False  # running property 读取这两个私有字段
    svc.render_loop = type('RL', (), {'actual_fps': 25.0, 'frame_index': 0,
                                      '_seg_chunk_total': 75,
                                      '_seg_chunk_produced': 75,
                                      'gaze_mode': 'off',
                                      'face_repair_mode': 'off',
                                      'lip_mode': 'on'})()
    svc.output_streamer = type('OS', (), {'stats': staticmethod(lambda: {
        'target_fps': 25, 'underruns': 0, 'preroll_state': 'ok',
        'seg_displayed': 75})})()
    svc.frame_buffer = type('FB', (), {'stats': staticmethod(lambda: {
        'depth': 5, 'capacity': 16})})()
    svc.gaze_corrector = None
    svc.face_repair = None
    svc.character_data = None  # get_status 尾部引用
    svc.audio_buffer = None  # get_status 尾部音频队列统计引用
    svc.unity_capture = None  # get_status 尾部虚拟相机统计引用
    status = svc.get_status()
    assert status['seg_chunk_total'] == 75
    assert status['seg_chunk_produced'] == 75
    assert status['seg_chunk_displayed'] == 75
    assert status['buffer_depth'] == 5  # 回归：深度非恒 0（前轮疑云复核）


# ------------------------------------------------- P3：静音填充闭口帧喂屏（2026-11-21 批次）

def _bool_sender(sent):
    """sender 桩：记录帧并返回 True（模拟真实 sink 成功回执）。"""
    return lambda f: (sent.append(f) or True)


def test_idle_frame_provider_send_and_track():
    """闭口帧供给方：喂屏成功后同步 _last_sent（underrun 回退基线更新）。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer  # noqa: E402
    idle = np.full((2, 2), 7, dtype="uint8")
    sent = []
    st = OutputStreamer(frame_buffer=FrameBuffer(capacity=8), target_fps=25,
                        sender=_bool_sender(sent), audio_player=None, preroll_frames=0)
    st.set_idle_frame_provider(lambda: idle)
    st._send_idle_frame()
    assert len(sent) == 1
    assert st._last_sent is idle


def test_idle_frame_provider_none_noop():
    """供给方返回 None（音频段在播）或未注册：不动作（维持重发语义）。"""
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer  # noqa: E402
    sent = []
    st = OutputStreamer(frame_buffer=FrameBuffer(capacity=8), target_fps=25,
                        sender=_bool_sender(sent), audio_player=None, preroll_frames=0)
    st.set_idle_frame_provider(lambda: None)
    st._send_idle_frame()
    st.set_idle_frame_provider(None)
    st._send_idle_frame()
    assert sent == []


def test_preroll_wait_no_idle_frame_sending():
    """2026-11-21 卡顿修复回归：preroll 等待期不发送静态缓存帧——缓存帧重复
    + 20Hz 轮询节奏与 25fps 不齐曾造成画面静止/跳帧抽搐。间隙连贯性由中流
    re-arm 阈值 1 保证（生产者实时 idle 帧原生节奏流出）。"""
    import numpy as np
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer  # noqa: E402
    idle = np.full((2, 2), 7, dtype="uint8")
    fb = FrameBuffer(capacity=8)
    sent = []
    st = OutputStreamer(frame_buffer=fb, target_fps=25,
                        sender=lambda f: (sent.append(f) or True),
                        audio_player=None, preroll_frames=0)
    st.set_idle_frame_provider(lambda: idle)
    st._running = True
    assert st._wait_preroll() is True  # depth 0 >= 阈值 0：立即通过
    assert sent == []  # 等待路径零静态帧发送
    st._running = False


def test_reset_preroll_midstream_threshold():
    """中流 flush 后 re-arm 阈值 1：恢复历史静默无门控行为（消除 ~0.2-0.4s
    冻结间隙——张嘴/卡顿共同根源），流启动首帧门控不受影响。"""
    from realtime_serve.output_buffer import FrameBuffer, OutputStreamer  # noqa: E402
    st = OutputStreamer(frame_buffer=FrameBuffer(capacity=8), target_fps=25,
                        sender=lambda f: True, audio_player=None, preroll_frames=8)
    assert st.preroll_frames == 8  # 流启动门控（构造值）
    st.reset_preroll(1)            # 中流 re-arm（process_full_audio flush 后）
    assert st.preroll_frames == 1
    st.reset_preroll()             # 缺省重臂：cap min(当前,4)——当前 1 保持 1
    assert st.preroll_frames == 1
    st2 = OutputStreamer(frame_buffer=FrameBuffer(capacity=8), target_fps=25,
                         sender=lambda f: True, audio_player=None, preroll_frames=8)
    st2.reset_preroll()            # 缺省重臂（历史路径）：8 → cap 4
    assert st2.preroll_frames == 4


def test_fi_endpoints_require_auth_token():
    """F-I：新端点纳入 app 级 _auth_guard（api_token 配置时无 token=401）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from realtime_serve.api.app import create_app  # noqa: E402

    class _Svc:
        def pause_output(self):
            return {"paused": True}

        def resume_output(self):
            return {"paused": False}

    app = create_app(service=_Svc(), api_token="secret")
    client = TestClient(app)
    assert client.post("/output/pause").status_code == 401  # 无 token
    ok = client.post("/output/pause", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200 and ok.json() == {"paused": True}
    ok2 = client.post("/output/resume", headers={"Authorization": "Bearer secret"})
    assert ok2.status_code == 200


def test_client_wrapper_degrades_on_failure(monkeypatch):
    """client 包装失败降级 {"ok": False}（d17 fallback：运行时降级=维持排序，
    不阻塞入队主链）。"""
    from utils.edtalk_realtime.edtalk_client import EDTalkClient
    c = EDTalkClient.__new__(EDTalkClient)
    c.api_ip_port = "http://127.0.0.1:1"
    # 2026-11-17 热切批次：__new__ 绕过 __init__，手工补热切实例态
    # （_config_obj=None 维持旧签名不门控；api_base 走 TTL 命中不触发现读）
    c._config_obj = None
    import time as _time
    c._api_base_cache = c.api_ip_port
    c._api_base_at = _time.monotonic()
    c._auth_headers = lambda: {}
    import requests as _rq
    def _boom(*a, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(_rq, "post", _boom)
    assert c.pause_output() == {"ok": False, "error": "refused"}
    assert c.resume_output()["ok"] is False


def test_client_pause_success_normalizes_ok(monkeypatch):
    """2026-11-14 实测修复回归：服务端成功体 {"paused": true}（无 ok 键）须
    归一 ok=True——否则调用方 .get("ok", False)（2026-11-15 批次前为
    _on_edtalk_yield 999 让位编排）把真实暂停成功误判为失败 → 走 d17 降级
    分支且永不 resume → 服务端输出永久冻结（画面停住、口型音频不消费）。"""
    from utils.edtalk_realtime.edtalk_client import EDTalkClient
    c = EDTalkClient.__new__(EDTalkClient)
    c.api_ip_port = "http://127.0.0.1:1"
    # 2026-11-17 热切批次：同上补热切实例态（TTL 命中走缓存基址）
    c._config_obj = None
    import time as _time
    c._api_base_cache = c.api_ip_port
    c._api_base_at = _time.monotonic()
    c._auth_headers = lambda: {}
    import requests as _rq

    class _Resp:
        def __init__(self, body):
            self.status_code = 200
            self._body = body

        def json(self):
            return self._body

    bodies = {"pause": {"paused": True}, "resume": {"paused": False}}

    def _fake_post(url, **k):
        key = "pause" if url.endswith("/output/pause") else "resume"
        return _Resp(bodies[key])

    monkeypatch.setattr(_rq, "post", _fake_post)
    assert c.pause_output() == {"ok": True, "paused": True}
    assert c.resume_output() == {"ok": True, "paused": False}
