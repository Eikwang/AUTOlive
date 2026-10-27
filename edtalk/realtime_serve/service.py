"""RealtimeService — 进程内控制面 facade（嵌入契约 v0，见 realtime_serve/EMBEDDING.md）。

架构（/autoplan 方案A）：
    main.py（CLI 装配）──▶ RealtimeService ──▶ RenderLoop / CharacterDataPack /
    嵌入方（import 直调）      │                  GazeCorrector / UnityCaptureSender / ...
                              └── api/app.py（HTTP 薄壳，E5 改为构造注入）

线程模型：所有方法走既有锁路径（_segment_lock / _switch_lock / _cam_lock），
不新增旁路（CEO 0E HOUR-1 约束）。HTTP 线程与嵌入线程共用同一方法面。

生命周期：start() 非阻塞（streamer 先于渲染线程，M12）；stop() 幂等且终结
（stop 后 start 抛 RuntimeError——重新构造实例）。重复 start 为 no-op。

参数热切语义（M2/M23/M18）：
- gaze: convergence/smooth_alpha/mode(on↔adaptive↔off) —— corrector 已构造时
  全矩阵可热切；经帧边界槽应用（无跨流 copy_/replay 竞态）；mode 切换联动
  眼区 mask（on→off 恢复原生眼，off→on 恢复纠正眼）。
- repair: mode(off↔on↔adaptive) 直接生效（纯门控，每帧读取，无 GPU 状态）。
- corrector 未构造（启动 off）→ RuntimeError（HTTP 层映射 409）；off→on 懒
  初始化在 TODOS。
"""
import math
import threading
from typing import Optional

import numpy as np

from realtime_serve.config import Config

_VALID_GAZE_MODES = ('off', 'on', 'adaptive')
_VALID_REPAIR_MODES = ('off', 'on', 'adaptive')
_VALID_LIP_MODES = ('on', 'off')
_MAX_SCHEDULE_LEN = 64

_COMPONENT_KEYS = ('model_manager', 'render_loop', 'output_streamer',
                   'frame_buffer', 'unity_capture', 'audio_buffer',
                   'audio_processor', 'compositor', 'face_repair',
                   'audio_player', 'character_data', 'gaze_corrector')


class ControlPlaneError(Exception):
    """Base for control-plane errors — the HTTP layer maps subclasses to
    status codes (404/400/409/503)."""


class NotFoundError(ControlPlaneError, ValueError):
    """Unknown segment/requested object — HTTP 404."""


class ValidationError(ControlPlaneError, ValueError):
    """Semantically invalid request — HTTP 400 (schema violations are 422
    at the pydantic layer)."""


class NotEnabledError(ControlPlaneError, RuntimeError):
    """Feature not constructed at startup (gaze/repair off) — HTTP 409."""


class RealtimeService:
    """Single control-plane object for the realtime inference service.

    The method surface IS the embedding contract (v0): additive-only until
    the first real embedder (AI-Vtuber) walks through T1; breaking changes
    must be registered in EMBEDDING.md 变更登记.
    """

    def __init__(self, config: Config, segment_sequence_spec=None,
                 components: Optional[dict] = None, assemble: bool = True):
        """Assemble (or accept injected) components and wire the control plane.

        Args:
            config: startup configuration (also the source of runtime defaults).
            segment_sequence_spec: optional ('auto'|'list', items) schedule
                installed at startup (CLI parity); embedders usually call
                set_schedule() instead.
            components: optional component overrides (M5) — keys of
                _COMPONENT_KEYS; used by tests and exotic embedders.
            assemble: False skips service assembly entirely (tests must
                then supply ALL components).
        """
        if assemble:
            # Deferred import: main.py imports this module (CLI assembly).
            from realtime_serve.main import setup_service
            result = setup_service(
                config,
                no_unitycapture=not config.unitycapture_enabled,
                segment_sequence_spec=segment_sequence_spec)
            values = dict(zip(_COMPONENT_KEYS, result))
        else:
            missing = [k for k in _COMPONENT_KEYS
                       if k in ('render_loop', 'output_streamer', 'frame_buffer',
                                'audio_buffer')
                       and (components or {}).get(k) is None]
            if missing:
                raise ValueError(f"components 缺少必需键: {missing}")
            values = {k: (components or {}).get(k) for k in _COMPONENT_KEYS}

        self.config = config
        for key in _COMPONENT_KEYS:
            setattr(self, key, values[key])

        # Render-loop attribute injection — the wiring main.py used to do
        # after setup_service returned. The facade owns it now. (CHAOS test
        # E6 caught this missing when mocks couldn't: without it the producer
        # never sees frame_buffer and silently produces nothing.)
        rl = self.render_loop
        if assemble:
            rl.model_manager = self.model_manager
            rl.frame_buffer = self.frame_buffer
            rl.output_streamer = self.output_streamer
            rl.audio_buffer = self.audio_buffer
            rl.audio_processor = self.audio_processor
            rl.compositor = self.compositor
            rl.face_repair = self.face_repair
            rl.face_repair_mode = config.face_repair_mode
            rl.gaze_corrector = self.gaze_corrector
            rl.gaze_mode = config.gaze_mode
            rl.audio_player = self.audio_player
        else:
            # Components path (tests / exotic embedders): fill in ONLY the
            # attributes the caller left unset — explicit wiring wins (M5).
            for attr, value in (('model_manager', self.model_manager),
                                ('frame_buffer', self.frame_buffer),
                                ('output_streamer', self.output_streamer),
                                ('audio_buffer', self.audio_buffer),
                                ('audio_processor', self.audio_processor),
                                ('compositor', self.compositor),
                                ('face_repair', self.face_repair),
                                ('gaze_corrector', self.gaze_corrector),
                                ('audio_player', self.audio_player)):
                if getattr(rl, attr, None) is None and value is not None:
                    setattr(rl, attr, value)

        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._wire_audio_player()

    # ------------------------------------------------------------------
    # Lifecycle（M12：次序复刻 main.py——streamer 先于渲染线程）
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError("service 已停止——请重新构造 RealtimeService")
            if self._started:
                return  # 重复 start 为 no-op
            print("\nStarting output streamer...")
            self.output_streamer.start()
            print("Starting render loop...")
            self.render_loop.start()
            self._started = True

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return  # 幂等
            self._stopped = True
        print("\nShutting down service...")
        # 次序即契约：先停生产者并关闭缓冲（唤醒 put/get 阻塞），再停消费者，
        # 最后释放音频与虚拟摄像头（与 main.py signal_handler 一致）。
        self.render_loop.stop()
        self.output_streamer.stop(timeout=timeout)
        if self.audio_player is not None:
            self.audio_player.close()
        if self.unity_capture is not None:
            self.unity_capture.close()
        print("Service stopped.")

    @property
    def running(self) -> bool:
        return self._started and not self._stopped

    # ------------------------------------------------------------------
    # Segment control（R2）
    # ------------------------------------------------------------------

    def switch_segment(self, segment: str, at: str = 'immediate') -> dict:
        """Switch segments; at='segment_end' queues a boundary continuation.

        Semantics per EMBEDDING.md: immediate keeps the ≤~0.3s hard cut
        (E2 flush + preroll); segment_end is frame-level seamless (M8, no
        flush) and may be superseded by a later switch (last-write-wins).
        """
        if at not in ('immediate', 'segment_end'):
            raise ValidationError(
                f"at 必须为 'immediate' 或 'segment_end'，got '{at}'")
        cd = self._require_character_data()
        if segment not in cd.segments:
            raise NotFoundError(
                f"Segment '{segment}' not found. Available: {cd.segments}")

        if at == 'segment_end':
            cd.prefetch_segment(segment)  # 尽力而为；就绪与否边界时再取（M15）
            self.render_loop.queue_segment_switch(segment)
            return {'ok': True, 'at': 'segment_end',
                    'pending_segment': segment,
                    'current_segment': cd.current_segment}

        prefetched = cd.pop_prefetch(segment)  # 就绪则零冷启动；永不等待（M15）
        # E3/E4: 响应快照来自切换事务内部（render_loop 在 _segment_lock 内
        # 构建），并发切换不会串台。
        snapshot = self.render_loop.switch_segment(
            segment, source='manual', seamless=False, prefetched=prefetched)
        if snapshot is None:
            raise RuntimeError(f"切换 '{segment}' 失败（详见服务日志）")
        return {'ok': True, 'queued': False, **snapshot}

    def set_schedule(self, sequence, align: bool = False, loop: str = 'forward') -> dict:
        """Replace the playback schedule at runtime (M10).

        align=False (default): install the order only — the current segment
        keeps playing, the new order takes effect at the next boundary.
        align=True: additionally switch to sequence[0] when it differs
        (immediate hard cut) — response `switched` reports whether that
        happened.
        loop: 'forward'=顺序循环 | 'pingpong'=首尾往返（编排默认画面单段模式）。
        """
        cd = self._require_character_data()
        if not isinstance(sequence, (list, tuple)) or not sequence:
            raise ValidationError("sequence 必须为非空段名列表")
        if len(sequence) > _MAX_SCHEDULE_LEN:
            raise ValidationError(f"sequence 长度上限 {_MAX_SCHEDULE_LEN}")
        if loop not in ('forward', 'pingpong'):
            raise ValidationError(f"loop 值域外: {loop!r}（仅 forward|pingpong）")
        sequence = list(sequence)
        bad = [s for s in sequence if s not in cd.segments]
        if bad:
            raise ValidationError(
                f"指定的段不存在: {bad}；可用段: {cd.segments}"
                f"（段名区分大小写，以 orifra/ 下目录实际名称为准）")

        before = cd.current_segment
        self.render_loop.set_segment_sequence(sequence, mode='list', align=align,
                                              loop=loop)
        switched = bool(align) and before != sequence[0] and \
            cd.current_segment == sequence[0]
        return {'ok': True, 'sequence': sequence, 'loop': loop,
                'switched': switched, 'current_segment': cd.current_segment}

    # ------------------------------------------------------------------
    # Runtime params（R1 + M23）
    # ------------------------------------------------------------------

    def set_gaze_params(self, convergence: Optional[float] = None,
                        mode: Optional[str] = None,
                        smooth_alpha: Optional[float] = None,
                        eye_source: Optional[str] = None) -> dict:
        """Hot-update gaze params (next-frame effective).

        REJECTIVE validation (never clamps). With the corrector constructed,
        the full mode matrix works (on↔adaptive↔off — the off gate and the
        eye-mask epoch make on→off restore native eyes). Without it
        (startup --gaze-correction off) every call raises RuntimeError
        (HTTP 409); lazy off→on init is a registered TODO.
        eye_source (GAZE_TIMING_PLAN DX 发现1): 'ori' | 'rendered' — the
        runtime rollback selector; 'rendered' restores the legacy Route C'
        behavior bitwise (D, U11 contract).
        """
        if (convergence is None and mode is None and smooth_alpha is None
                and eye_source is None):
            raise ValidationError("至少提供 convergence / mode / smooth_alpha / "
                                  "eye_source 之一")
        if self.gaze_corrector is None:
            raise NotEnabledError(
                "gaze 校正未构造——需以 --gaze-correction on|adaptive 启动"
                "（gaze_capable=false；懒初始化见 TODOS）")
        if convergence is not None:
            v = float(convergence)
            if not math.isfinite(v) or not (-1.0 <= v <= 1.0):
                raise ValidationError(
                    f"convergence 必须为 [-1, 1] 内的有限数值，got {convergence}"
                    f"（当前值 {self.gaze_corrector.convergence_h}）")
        if smooth_alpha is not None:
            a = float(smooth_alpha)
            if not math.isfinite(a) or not (0.0 <= a < 1.0):
                raise ValidationError(
                    f"smooth_alpha 必须为 [0, 1) 内的有限数值，got {smooth_alpha}"
                    f"（当前值 {self.gaze_corrector.smooth_alpha}）")
        if mode is not None and mode not in _VALID_GAZE_MODES:
            raise ValidationError(
                f"mode 必须为 {_VALID_GAZE_MODES} 之一，got '{mode}'"
                f"（当前值 {self.render_loop.gaze_mode}）")
        if eye_source is not None and eye_source not in ('ori', 'rendered'):
            raise ValidationError(
                f"eye_source 必须为 ('ori', 'rendered') 之一，got '{eye_source}'"
                f"（当前值 {self.gaze_corrector.eye_source_mode}）")
        self.render_loop.queue_gaze_params(convergence=convergence,
                                           smooth_alpha=smooth_alpha,
                                           mode=mode,
                                           eye_source=eye_source)
        return {'ok': True, 'effective': 'next_frame'}

    def set_repair_params(self, mode: str) -> dict:
        """Hot-switch face repair mode (M23). Pure per-frame gate — no GPU
        state, direct effect on the next frame."""
        if mode not in _VALID_REPAIR_MODES:
            raise ValidationError(
                f"mode 必须为 {_VALID_REPAIR_MODES} 之一，got '{mode}'"
                f"（当前值 {self.render_loop.face_repair_mode}）")
        if mode != 'off' and self.face_repair is None:
            raise NotEnabledError(
                "面部修复未构造——需以 --face-repair on|adaptive 启动"
                "（repair_capable=false；懒初始化见 TODOS）")
        self.render_loop.face_repair_mode = mode
        return {'ok': True, 'effective': 'next_frame'}

    def set_lip_params(self, mode: str) -> dict:
        """Hot-switch lip-sync inference gate（画外音总开关）。

        'off': 跳过 Audio2Lip GPU 推理与画面口型渲染（无人脸帧同样直通），
        音频 chunk 照常帧锁定出声——纯逐帧门控，无 GPU 状态，下一帧生效。
        与 gaze/repair 不同，lip 门控不依赖启动参数，任意时刻可双向热切。
        """
        if mode not in _VALID_LIP_MODES:
            raise ValidationError(
                f"mode 必须为 {_VALID_LIP_MODES} 之一，got '{mode}'"
                f"（当前值 {self.render_loop.lip_mode}）")
        self.render_loop.lip_mode = mode
        return {'ok': True, 'effective': 'next_frame'}

    # ------------------------------------------------------------------
    # Segment hot add/remove（素材管理：嵌入方落盘四件套后热注册/卸载）
    # ------------------------------------------------------------------

    def add_segment(self, segment: str) -> dict:
        """Register + lazily load an on-disk segment (idempotent).

        FileNotFoundError（四件套缺失）映射 404；数据损坏映射 503。
        新段不自动进入播放调度——需要参与默认轮播时由嵌入方调用
        set_schedule 重装序列。
        """
        cd = self._require_character_data()
        try:
            info = cd.add_segment(segment)
        except FileNotFoundError as e:
            raise NotFoundError(str(e))
        return {'ok': True, **info}

    def remove_segment(self, segment: str) -> dict:
        """Unload a segment from memory (disk deletion is the embedder's job).

        拒绝语义（HTTP 409/400 由 _map_control_errors 按异常类型映射）：
        - 当前渲染段 / 播放调度引用 → ValidationError（400，附修复动作）；
        - 未知段 → NotFoundError（404）。
        若存在指向该段的 segment_end 待执行切换则一并清除（避免边界触发
        必败切换进入日志风暴重锚路径）。
        """
        cd = self._require_character_data()
        sched = getattr(self.render_loop, '_scheduler', None)
        if sched is not None and segment in sched.sequence:
            raise ValidationError(
                f"段 '{segment}' 在当前播放调度中，不能删除；先用 set_schedule"
                f"（/segment/schedule）装一个不含该段的序列")
        pending = getattr(self.render_loop, 'pending_switch', None)
        cleared = False
        if pending == segment:
            self.render_loop.clear_pending_switch()
            cleared = True
        try:
            info = cd.remove_segment(segment)
        except ValueError as e:
            # 当前渲染段保护（pack 层）→ 语义冲突；未知段 → 404
            if 'not found' in str(e) or 'not found.' in str(e):
                raise NotFoundError(str(e))
            raise ValidationError(str(e))
        return {'ok': True, 'cleared_pending_switch': cleared, **info}

    # ------------------------------------------------------------------
    # Audio（嵌入方主动态数据面——facade 必须收编，CEO F4/DX-2）
    # ------------------------------------------------------------------

    def push_audio(self, pcm_int16_bytes: bytes, seq: int = 0) -> dict:
        """Push a streaming audio chunk (16kHz mono int16 PCM)."""
        if self.audio_buffer is None:
            raise RuntimeError("audio_buffer 未初始化")
        self.audio_buffer.push(pcm_int16_bytes, seq=seq)
        return {'ok': True,
                'buffer_ms': round(self.audio_buffer.buffer_duration_ms())}

    def push_full_audio(self, pcm_int16_bytes: bytes) -> dict:
        """Push a full TTS segment (16kHz mono int16 PCM); inference runs
        asynchronously, playback starts at display time."""
        if self.render_loop is None:
            raise RuntimeError("render_loop 未初始化")
        pcm_float = np.frombuffer(pcm_int16_bytes,
                                  dtype=np.int16).astype(np.float32) / 32768.0
        worker = threading.Thread(target=self.render_loop.process_full_audio,
                                  args=(pcm_float,), daemon=True)
        worker.start()
        return {'ok': True,
                'duration_s': round(len(pcm_float) / 16000.0, 2),
                'processing': 'async'}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Snapshot identical in shape to GET /status (single builder — the
        HTTP endpoint delegates here; E5 removes the app.py duplicate)."""
        status = {
            'state': 'running' if self.running else 'stopped',
            'fps': 0.0,  # deprecated alias for output_fps
            'production_fps': 0.0,
            'output_fps': 0.0,
            'buffer_depth': 0,
            'buffer_ms': 0.0,
            'underruns': 0,
            'preroll_state': 'unknown',
            'buffer_full_warn': False,
            'current_segment': None,
            'frame_index': 0,
            'resolution': None,
            'available_segments': [],
        }

        rl = self.render_loop
        if rl is not None:
            status['production_fps'] = round(rl.actual_fps, 1)
            status['frame_index'] = rl.frame_index
            status['last_error'] = getattr(rl, 'last_error', '') or None
            sched = getattr(rl, '_scheduler', None)
            if sched is not None:
                status['segment_sequence'] = {
                    'mode': sched.mode,
                    'position': sched.position,
                    'sequence': sched.sequence,
                }
            pending = getattr(rl, 'pending_switch', None)
            if pending is not None:
                status['pending_switch'] = pending

        os_ = self.output_streamer
        if os_ is not None:
            s = os_.stats()
            status['output_fps'] = s.get('target_fps', 0)
            status['underruns'] = s.get('underruns', 0)
            status['preroll_state'] = s.get('preroll_state', 'unknown')
        status['fps'] = status['output_fps']

        gc = self.gaze_corrector
        status['gaze_capable'] = gc is not None
        if gc is not None and rl is not None:
            status['gaze_state'] = rl.gaze_mode
            status['gaze_graph'] = 'captured' if gc._graph_ready else 'eager'
            status['gaze_frames'] = {
                'corrected': gc.corrected_frames,
                'blink_skips': gc.blink_skips,
                'nan_skips': gc.nan_skips,
                'error_skips': gc.error_skips,
                'pristine_fallback': getattr(self.character_data,
                                             'pristine_fallbacks', 0),
            }
            status['gaze_params'] = {
                'convergence': gc.convergence_h,
                'mode': rl.gaze_mode,
                'smooth_alpha': gc.smooth_alpha,
                'eye_source': gc.eye_source_mode,
            }
        else:
            status['gaze_state'] = 'off'

        status['repair_mode'] = rl.face_repair_mode if rl is not None else 'off'
        status['repair_capable'] = self.face_repair is not None
        status['lip_mode'] = rl.lip_mode if rl is not None else 'on'
        # 统一画布合成层计数回显（2026-10-27 画面输出分叉，R-eng11④ 可观测面）
        comp = getattr(rl, 'canvas_compositor', None)
        if comp is not None:
            status['composite'] = comp.stats()

        fb = self.frame_buffer
        if fb is not None:
            s = fb.stats()
            status['buffer_depth'] = s.get('depth', 0)
            status['buffer_full_warn'] = (
                s.get('depth', 0) >= s.get('capacity', 1))

        cd = self.character_data
        if cd is not None:
            status['current_segment'] = cd.current_segment
            status['resolution'] = list(cd.resolution)
            status['available_segments'] = cd.segments
            # 逐段元信息（帧数/人脸比例/分辨率；素材管理页与编排画外音提示共用）。
            # dict-guard：测试替身可能只暴露 segments 属性——失败降级为名字表。
            try:
                details = {}
                for seg in list(cd.segments or []):
                    try:
                        details[seg] = cd.segment_info(seg)
                    except Exception as e:
                        details[seg] = {'segment': seg, 'error': str(e)}
                status['segment_details'] = details
            except Exception:
                pass
            # dict-guard: test doubles expose the attr as a MagicMock
            if hasattr(cd, 'prefetch_status') and \
                    isinstance(cd.prefetch_status, dict):
                status['prefetch'] = cd.prefetch_status

        ab = self.audio_buffer
        if ab is not None:
            status['buffer_ms'] = round(ab.buffer_duration_ms(), 1)

        uc = self.unity_capture
        if uc is not None:
            status['camera_resolution'] = [uc.width, uc.height]

        return status

    # ------------------------------------------------------------------
    # Optional HTTP shell
    # ------------------------------------------------------------------

    def run_api(self, host: Optional[str] = None,
                port: Optional[int] = None) -> None:
        """Blocking uvicorn server for the HTTP face (embedder-optional;
        the CLI path in main.py manages its own server)."""
        import uvicorn
        from realtime_serve.api.app import create_app
        token = getattr(self.config, 'api_token', None) if self.config else None
        uvicorn.run(create_app(self, api_token=token),
                    host=host or '127.0.0.1', port=port or 8000)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_character_data(self):
        cd = self.character_data
        if cd is None:
            raise RuntimeError("character_data 未初始化")
        return cd

    def _wire_audio_player(self) -> None:
        """Frame-locked audio sink wiring (E-sync): the OutputStreamer pushes
        each displayed frame's 640-sample chunk to the player at display
        time — audio position ≡ displayed lip position (lead = DAC latency
        only, ≤ ~40ms; the audio waits for the lips, never the reverse)."""
        self.output_streamer.audio_player = self.audio_player
