"""FastAPI application — HTTP thin shell over the RealtimeService facade (E5).

Every endpoint is a thin delegate: validation lives in pydantic (schema → 422)
and the facade (semantics → 400/404/409), business logic lives in the service.
Endpoints are SYNC `def` (FastAPI threadpool) so a slow control call can never
block the event loop (E3 — /status and /audio/push must stay live during a
cold segment switch).

Error envelope (M4): semantic failures return
    {"detail": {"problem": ..., "cause": ..., "fix": ...}}

Auth (E4): when an api-token is configured, every request must carry
`Authorization: Bearer <token>` (401 otherwise). The facade surface is
token-free by construction — in-process callers need no transport auth.
"""
import base64
from typing import Optional

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from realtime_serve.service import (NotEnabledError, NotFoundError,
                                    RealtimeService, ValidationError)

# 10MB raw PCM ≈ 327s @ 16kHz int16 mono — far above any TTS sentence (E4).
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class AudioPushRequest(BaseModel):
    """Request model for audio push endpoint (chunked streaming)."""
    pcm_base64: str
    timestamp_ms: int = 0
    seq: int = 0  # Sequence number for ordering


class AudioPushFullRequest(BaseModel):
    """Full TTS audio segment (16kHz mono int16, base64-encoded)."""
    pcm_base64: str


class SegmentSwitchRequest(BaseModel):
    segment: str = Field(min_length=1, max_length=64)
    at: str = Field(default="immediate",
                    pattern=r"^(immediate|segment_end)$",
                    description="immediate=硬切(≤0.3s)；segment_end=边界帧级无缝")


class SegmentScheduleRequest(BaseModel):
    sequence: list = Field(min_length=1, max_length=64,
                           description="段名列表（顺序循环）")
    align: bool = Field(default=False,
                        description="True=立即对齐 sequence[0]；False=仅装表，边界生效")


class GazeParamsRequest(BaseModel):
    convergence: Optional[float] = Field(
        default=None, ge=-1.0, le=1.0, allow_inf_nan=False,
        description="收敛角 [-1,1]（负值发散）")
    smooth_alpha: Optional[float] = Field(
        default=None, ge=0.0, lt=1.0, allow_inf_nan=False,
        description="眼中心 EMA 平滑系数 [0,1)")
    mode: Optional[str] = Field(
        default=None, pattern=r"^(off|on|adaptive)$",
        description="gaze 门控；on→off 恢复原生眼（mask epoch 联动）")
    eye_source: Optional[str] = Field(
        default=None, pattern=r"^(ori|rendered)$",
        description="眼像素源：ori=原生眼源(默认)/rendered=legacy 行为回滚 "
                    "(GAZE_TIMING_PLAN DX 发现1)")


class RepairParamsRequest(BaseModel):
    mode: str = Field(pattern=r"^(off|on|adaptive)$",
                      description="面部修复门控；纯门控，下一帧生效")


class LipParamsRequest(BaseModel):
    mode: str = Field(pattern=r"^(on|off)$",
                      description="口型推理门控；off=画外音（跳过 Audio2Lip，"
                                  "音频照常帧锁定出声），下一帧生效")


class SegmentAddRequest(BaseModel):
    segment: str = Field(min_length=1, max_length=64,
                         description="段名（=orifra/ 下目录名，需已落盘四件套）")


class SegmentRemoveRequest(BaseModel):
    segment: str = Field(min_length=1, max_length=64,
                         description="段名；盘上文件删除由调用方负责")


def _http(problem: str, cause: str, fix: str, code: int) -> HTTPException:
    return HTTPException(status_code=code,
                         detail={"problem": problem, "cause": cause, "fix": fix})


def create_app(service: RealtimeService,
               api_token: Optional[str] = None) -> FastAPI:
    """Build the HTTP shell around a RealtimeService (constructor injection).

    api_token: when set, all routes require `Authorization: Bearer <token>`.
    """
    dependencies = []
    if api_token:
        def _auth_guard(authorization: str = Header(default="")):
            if authorization != f"Bearer {api_token}":
                raise _http("未授权", "缺少或错误的 Bearer token",
                            "请求头携带 Authorization: Bearer <api-token>", 401)
        dependencies.append(Depends(_auth_guard))

    app = FastAPI(title="EDTalk Realtime Streaming Inference Service",
                  dependencies=dependencies)

    def _decode_pcm(pcm_base64: str) -> bytes:
        try:
            pcm = base64.b64decode(pcm_base64)
        except Exception as e:
            raise _http("音频数据解码失败", str(e),
                        "发送标准 base64 编码的 16kHz 单声道 int16 PCM", 400)
        if len(pcm) > _MAX_AUDIO_BYTES:
            raise _http("音频载荷过大",
                        f"{len(pcm)} 字节超过上限 {_MAX_AUDIO_BYTES}",
                        "拆分推送或使用 /audio/push 流式分块", 413)
        return pcm

    def _map_control_errors(e: Exception, fix: str):
        if isinstance(e, NotFoundError):
            raise _http("请求对象不存在", str(e), fix, 404)
        if isinstance(e, NotEnabledError):
            raise _http("功能未启用", str(e), fix, 409)
        if isinstance(e, ValidationError):
            raise _http("请求参数非法", str(e), fix, 400)
        if isinstance(e, ValueError):
            raise _http("请求参数非法", str(e), fix, 400)
        raise _http("服务未就绪或内部错误", str(e),
                    "查看服务日志；确认服务已 start() 且装配完整", 503)

    # ------------------------------------------------------------------
    # Audio（数据面）
    # ------------------------------------------------------------------

    @app.post("/audio/push")
    def push_audio(request: AudioPushRequest):
        """Push a streaming audio chunk (16kHz mono int16 PCM, base64)."""
        pcm = _decode_pcm(request.pcm_base64)
        try:
            return service.push_audio(pcm, seq=request.seq)
        except Exception as e:
            _map_control_errors(e, "确认 audio_buffer 已初始化")

    @app.post("/audio/push_full")
    def push_audio_full(request: AudioPushFullRequest):
        """Push a full TTS segment; Audio2Lip runs async, playback starts at
        display time (gapless vs chunked push)."""
        pcm = _decode_pcm(request.pcm_base64)
        try:
            return service.push_full_audio(pcm)
        except Exception as e:
            _map_control_errors(e, "确认服务已 start() 且 render_loop 就绪")

    # ------------------------------------------------------------------
    # Segment control（控制面）
    # ------------------------------------------------------------------

    @app.post("/segment/switch")
    def switch_segment(request: SegmentSwitchRequest):
        """Switch segment now (immediate) or queue a seamless boundary
        continuation (segment_end, last-write-wins)."""
        try:
            return service.switch_segment(request.segment, at=request.at)
        except Exception as e:
            _map_control_errors(
                e, "用 GET /status 的 available_segments 查看段名；"
                   "at 取值 immediate|segment_end")

    @app.post("/segment/schedule")
    def set_schedule(request: SegmentScheduleRequest):
        """Replace the playback schedule; default does NOT interrupt the
        current segment (takes effect at the next boundary)."""
        try:
            return service.set_schedule(request.sequence, align=request.align)
        except Exception as e:
            _map_control_errors(
                e, "用 GET /status 的 available_segments 查看段名；"
                   "align=true 会立即切到 sequence[0]")

    # ------------------------------------------------------------------
    # Runtime params（控制面）
    # ------------------------------------------------------------------

    @app.post("/gaze/params")
    def set_gaze_params(request: GazeParamsRequest):
        """Hot-update gaze params (next-frame effective). 409 when the
        corrector was not constructed at startup (gaze_capable=false)."""
        try:
            return service.set_gaze_params(
                convergence=request.convergence,
                smooth_alpha=request.smooth_alpha,
                mode=request.mode,
                eye_source=request.eye_source)
        except Exception as e:
            _map_control_errors(
                e, "先读 GET /status 的 gaze_capable；convergence ∈ [-1,1]，"
                   "smooth_alpha ∈ [0,1)，mode ∈ off|on|adaptive，"
                   "eye_source ∈ ori|rendered")

    @app.post("/repair/params")
    def set_repair_params(request: RepairParamsRequest):
        """Hot-switch face repair mode (next-frame effective). 409 when the
        model was not constructed at startup (repair_capable=false)."""
        try:
            return service.set_repair_params(request.mode)
        except Exception as e:
            _map_control_errors(
                e, "先读 GET /status 的 repair_capable；mode ∈ off|on|adaptive")

    @app.post("/lip/params")
    def set_lip_params(request: LipParamsRequest):
        """Hot-switch lip-sync inference (next-frame effective). off=画外音：
        跳过口型推理与口型渲染，音频照常帧锁定播放。任意时刻可双向热切。"""
        try:
            return service.set_lip_params(request.mode)
        except Exception as e:
            _map_control_errors(
                e, "mode ∈ on|off；先读 GET /status 的 lip_mode 查看当前值")

    @app.post("/segment/add")
    def add_segment(request: SegmentAddRequest):
        """Register + lazily load an on-disk segment (idempotent; 素材热上线).

        404 when the segment's four-piece set (orifra/count/metadata/facemark)
        is missing on disk. New segments do NOT auto-join the playback
        schedule — reinstall it via /segment/schedule if needed."""
        try:
            return service.add_segment(request.segment)
        except Exception as e:
            _map_control_errors(
                e, "先由素材管理预处理落盘四件套（orifra/<seg>/ 等），"
                   "再用 GET /status 的 available_segments 查看段名")

    @app.post("/segment/remove")
    def remove_segment(request: SegmentRemoveRequest):
        """Unload a segment from memory (400=当前渲染段/调度引用保护；404=未知段).

        Disk files are NOT deleted — the embedder (素材管理) owns the
        character_dir lifecycle."""
        try:
            return service.remove_segment(request.segment)
        except Exception as e:
            _map_control_errors(
                e, "先切换到其他段并更新 /segment/schedule（移除引用）后重试；"
                   "盘上目录由调用方删除")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @app.get("/status")
    def get_status():
        """Service snapshot — the single builder lives on the facade; this
        response is ADDITIVE-ONLY by contract (v0)."""
        try:
            return service.get_status()
        except Exception as e:
            _map_control_errors(e, "查看服务日志")

    return app
