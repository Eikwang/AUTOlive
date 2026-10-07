# EMBEDDING.md — 实时推理服务嵌入契约 v0

> 冻结日期 2026-09-03（/autoplan 四声部评审 + T1 对接 spike）。
> **拓扑声明：本服务是 Windows-only 独立进程**（`ctypes.windll`、pyvirtualcam、
> UnityCapture 滤镜）。嵌入方经 HTTP 驱动（近期标准拓扑），或同环境进程内直调
> （统一环境完成后可选，见文末三拓扑矩阵）。
> 契约规则：**v0 实验契约**——方法面只加不改签名；破坏性变更必须在本文件
> "变更登记"节记录弃用与新名；首个真实嵌入方（AI-Vtuber）接入时强制
> breaking-change 评审。

## 快速开始（同环境进程内，10 行）

```python
from realtime_serve.config import Config
from realtime_serve.service import RealtimeService   # 实现中：任务 T2

config = Config(character_dir="wy", target_fps=25,
                face_repair_mode="adaptive",   # on|adaptive → 运行时可热切；off 则 409
                gaze_mode="adaptive",          # off|on|adaptive → on↔adaptive 可热切
                gaze_convergence=0.05)
svc = RealtimeService(config)
svc.start()                        # 非阻塞：渲染线程就绪、虚拟摄像头可消费
svc.switch_segment("22")           # 立即切换（≤0.3s 不连续）
svc.set_gaze_params(convergence=0.08)   # 下一帧生效
svc.get_status()                   # 与 GET /status 同构
svc.stop()                         # 幂等，join 渲染线程
```

## 方法面契约（v0）

| 方法 | 签名 | 返回 dict | 异常 | 阻塞上界 |
|---|---|---|---|---|
| start | `start() -> None` | — | — | 非阻塞（<1s 启动线程） |
| stop | `stop(timeout=5.0) -> None` | — | — | ≤timeout，幂等 |
| switch_segment | `switch_segment(segment: str, at: str = 'immediate') -> dict` | `{ok, segment, frame_count, resolution, queued}` | ValueError | immediate 冷段 ≤~1s；segment_end 立即返回 |
| set_schedule | `set_schedule(sequence: list[str], align: bool = False) -> dict` | `{ok, sequence, switched}` | ValueError | align=False <1ms；align=True ≤~1s |
| set_gaze_params | `set_gaze_params(convergence=None, mode=None, smooth_alpha=None) -> dict` | `{ok, effective:"next_frame"}` | ValueError | <1ms（帧边界槽） |
| set_repair_params | `set_repair_params(mode: str) -> dict` | `{ok, effective:"next_frame"}` | ValueError | <1ms |
| push_audio | `push_audio(pcm_int16_bytes: bytes, seq: int = 0) -> dict` | `{ok, buffer_ms}` | ValueError | <5ms |
| push_full_audio | `push_full_audio(pcm_int16_bytes: bytes) -> dict` | `{ok, duration_s, processing:"async"}` | ValueError | <5ms（推理异步） |
| get_status | `get_status() -> dict` | 与 GET /status 同构 | — | <1ms |

校验语义一律**拒绝**（ValueError / HTTP 4xx），不做 clamp；错误信息回显合法
区间与当前值。运行时 API 参数覆盖 CLI 启动值，仅存活至重启。

状态码分层：**422** = 请求体 schema（pydantic 字段/枚举约束）、**400** = 语义
校验（越界/空列表）、**401** = Bearer 鉴权失败（启用 `--api-token` 时）、
**404** = 对象不存在、**409** = 功能启动未启用、
**413** = 载荷超限（音频 10MB）、**503** = 服务未就绪。错误体统一
`{"problem", "cause", "fix"}` 三段式。

## HTTP 端点规格（进程外驱动面）

默认绑定 `127.0.0.1:8000`（远程需显式 `--host 0.0.0.0`，可选 `--api-token` Bearer）。
错误统一三段式：`HTTPException(detail={"problem","cause","fix"})`。

| 端点 | 请求体 | 200 响应 | 错误 |
|---|---|---|---|
| `POST /audio/push` | `{pcm_base64, seq}`（另有被忽略的 `timestamp_ms`） | `{ok, buffer_ms}` | 400/401 |
| `POST /audio/push_full` | `{pcm_base64}`（16kHz int16，大小上限见 413） | `{ok, duration_s, processing:"async"}` | 400/401/413 |
| `POST /segment/switch` | `{segment, at: "immediate"\|"segment_end"}` | immediate: `{ok, segment, frame_count, resolution, queued:false}`；segment_end: `{ok, at, pending_segment, current_segment}` | 404 缺段（回显 available_segments） |
| `POST /segment/schedule` | `{sequence: [段名...], align: bool=false}` | `{ok, sequence, switched}`（align=True 且当前段不在新序列时 switched=true 表示发生即时对齐切换） | 400 缺段/空/超长（≤64） |
| `POST /gaze/params` | `{convergence?, mode?, smooth_alpha?}`（均可选） | `{ok, effective:"next_frame"}` | 400 越界/NaN；409 gaze 未构造 |
| `POST /repair/params` | `{mode: "off"\|"on"\|"adaptive"}` | `{ok, effective:"next_frame"}` | 400 非法值；409 未构造 |
| `GET /status` | — | 见下 | — |

> ⚠ **死路径警示（v0.4，P1）**：`/audio/push` 流式路径当前**无消费者**（render_loop 无 audio_buffer 消费调用；PIPELINE_MAP §5 自标遗留死路径）——推送返回 200 但**不产生口型**，数据滞留 AudioBuffer 无上限。**嵌入方整句驱动一律用 `/audio/push_full`**；恢复流式消费须走 breaking-change 评审。
> ⚠ **过载语义未定义（v0.4，P1）**：`/audio/push_full` 并发/密集推送经 `_push_lock` 串行排队，队列**无界**，无丢弃/熔断——过载表现为口型延迟累积而非报错。嵌入方必须自行限流（单飞行句 + 队列上限，建议 ≤2）。

`GET /status` 只增不改。v0 新增字段：`gaze_params{convergence,mode,smooth_alpha}`、
`repair_mode`、`gaze_capable`、`repair_capable`、`camera_resolution`、
`pending_switch`、`prefetch(idle|loading|ready|discarded)`、
`segment_sequence{mode,position,sequence}`（已有）。
**条件性字段（v0.4 注记）**：`pending_switch` 与 `prefetch` 仅在存在挂起切换/
预取活动时出现，缺席=无该状态，前端按缺省处理。

## 行为语义（验收口径）

- **切换**：
  - `at=immediate`：flush 旧段缓冲 + preroll 重布防——**量化承诺 ≤~0.3s 不连续，
    无旧段帧闪现**。不要在前端按"无缝"设计等待动画以外的预期。
  - `at=segment_end`：边界帧级无缝（无重复帧、无 underrun 增量、无 preroll 门）。
    挂起后被 immediate 覆盖；到当前段自身 = 从帧 0 重播；段名请求时校验。
  - 调度器 auto-advance 与 segment_end 同语义（边界续接）。
- **幂等**：switch 到当前段 = reset + 从帧 0 重播。不要用重复调用当健康检查
  （健康检查用 GET /status）。
- **gaze/repair 热切矩阵**：启动已构造 → on↔adaptive↔off 自由切（下一帧生效）；
  启动 off → 组件未构造 → 409，`{mode}_capable=false`（先探测再渲染 UI 开关）。
  off→on 懒初始化在 TODOS（见主计划）。
- **音频**：16kHz 单声道 int16 PCM。push_full 为 TTS 整句**唯一有效通道**
  （全序列推理，唇形最稳；v0.4 死路径警示见上）；push 端点已死，勿对接。
  **过载语义未定义**（v0.4）——嵌入方自行限流。
- **音画同步（帧锁定）**：音频按 640 采样/帧切片随帧出，展示该帧时才推给
  播放器——音频位置 ≡ 展示帧位置 ≡ 唇形位置（按构造锁定）。口型领先量仅
  DAC 延迟（≤ ~40ms，满足"领先 ≤100ms、绝不滞后"）；推理未就绪时音频自动
  等待（HOLD），补上后从断点继续，不跳帧不重复。

## 三拓扑矩阵

| 拓扑 | 前提 | 取舍 |
|---|---|---|
| 同机 HTTP（**近期标准**，AI-Vtuber 采用） | 无（默认回环） | 崩溃域隔离；音频本就是数据面 |
| 同解释器进程内（统一环境完成后可选） | constraints 钉版 + sys.path 注入 | import 即集成；共享 GIL/CUDA，崩溃域耦合 |
| 跨机 HTTP | 网络可达 + `--host 0.0.0.0` | 最解耦；注意 token 与带宽（25fps 视频走虚拟摄像头不走网络） |

## v0 已知缺口（登记处）

多角色 `load_character`（切换为秒级停顿，前端不得按无缝预期设计）、
per-segment gaze/repair 参数（v0 为全局值）、事件推送、参数持久化、
gaze/repair off→on 懒初始化、CHANGELOG。详见仓库 `TODOS.md`。

## 变更登记

- v0（2026-09-03）：初版契约冻结（M1 签名表 + M23 repair 热切 + M4 错误包络）。
- v0.1（2026-09-03，实现定稿）：/gaze/params、/repair/params、/segment/schedule
  上线；/segment/switch 增 `at=`；默认绑定回环 + 可选 Bearer 鉴权；422/413
  状态码注记。方法面与 v0 无破坏性变更（快照测试守护）。
- v0.2（2026-09-03）：音频-口型同步重构——消费端帧锁定（音频跟随口型，
  领先 ≤~40ms 绝不滞后；推理未就绪时音频等待）。方法面无变更。
- v0.3（2026-09-21，GAZE_TIMING_PLAN 最终门 A）：视线纠正眼源切换。
  `set_gaze_params` 增补可选参数 `eye_source`（'ori' 默认 | 'rendered'
  legacy 行为回滚——additive，快照测试已更新）；/status `gaze_params`
  增 `eye_source`、`gaze_frames` 增 `pristine_fallback`（additive）。
  HTTP 面新增 `/gaze/params` 的 `eye_source` 字段（422 校验）。语义变更：
  gaze on（ori 模式）时最终帧眼睛一律源自 orifra 原生眼（bypass 帧贴未
  修正原生眼）——blink 帧从"生成眼"变为"原生眼"，属产品行为变更。
- v0.4（2026-09-22，/autoplan 整合准备 T6 契约回写，方法面零变更）：
  **两条 P1 行为级警示**——① `/audio/push` 流式路径无消费者（遗留死路径，
  推送 200 但不产生口型；整句驱动一律 `/audio/push_full`）；② push_full
  过载语义未定义（_push_lock 串行排队、队列无界、无丢弃/熔断，嵌入方
  自行限流单飞行句+队列≤2）。additive 登记：`AudioPushRequest.timestamp_ms`
  字段存在但被忽略；`/segment/schedule` 响应增 `current_segment` 键；
  401（Bearer 失败）补入状态码分层；/status `pending_switch`/`prefetch`
  为条件性字段。实现备注（非契约）：`--cap-num` 参数断链不生效
  （main.py:362-367 未传入 sender，多摄像头选设备暂不可用）；
  `Config.face_repair_enabled` 与 mode 双轨，装配只看
  `face_repair_mode`（main.py:313）——进程内直调只设 mode。
