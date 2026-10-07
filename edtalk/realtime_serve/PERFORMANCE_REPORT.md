# EDTalk 实时推理服务帧率优化报告

## 里程碑（2026-09-02）：帧率稳定 25FPS ✅

**CUDA Graph 落地后实测（10 分钟持续运行）**：

```
[RenderLoop] FPS:25.0  buffer:16  gen=p50:8.9/p95:10.7/p99:12.0ms
comp=p50:0.3ms  repair=p50:7.2ms  put=p50:20.0ms（背压=缓冲蓄满）
total=p50:39.7/p99:41.2ms
```

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| gen p50 | 21-34ms 双峰漂移，p99 60-120ms | **8.9ms 恒定，p99 12ms** |
| FPS | 16-25 波动 | **稳定 25.0** |
| buffer | 0-1（蓄不住） | **15-16 持续满**（put 背压 20ms=产能富余 ~50fps） |
| face_repair | CPU 往返 ~9.5ms | GPU 内联 IOBinding 7.2ms，零往返 |

关键修复链：缓冲池/OutputStreamer 解耦 → CUDA Graph 消除逐层 kernel launch（GPU 利用率 25% 的根因）→ face_repair IOBinding GPU 内联。

## 问题描述（原始）

服务运行时帧率波动大（15-25fps），经常卡住数秒。GPU 利用率仅 22%、显存 2GB，硬件性能远未饱和。

---

## 根因分析（共6个独立缺陷叠加）

### 🔴 根因1：预加载永远追不上（主因：卡顿数秒）

**文件**: `sequence_frame_pack.py` (BatchPreloader)

**问题**: `batch_size=500`（命令行默认值），预载只在当前批次**90%处**才开始加载下一批——只剩50帧（2秒）的余量，而并行解码500帧JPEG（2560×1440，每张11MB）+ GPU传输需要 **10-15秒**，根本不可能追上。渲染线程跨过批次边界后画面冻结在旧帧上，直到加载完成。

**日志证据**（改造前）:
```
Frame 450, FPS: 24.5     ← batch 0 最后几帧，正常
Frame 480, FPS: 15.2     ← 跨 batch 边界，FPS 暴跌，从此不再恢复
Frame 510, FPS: 15.3
...
Frame 1260, FPS: 16.1    ← 永远卡在15fps
```

**修复**: 
- 改为**当前批次成为 current 时立即开始预载下一批**（全4秒余量）
- 并行JPEG解码（ThreadPoolExecutor 8线程，cv2.imread释放GIL）
- batch_size 默认改为 100（4秒内容，0.5秒可加载完）
- 渲染线程永远不执行同步批次加载；如不在窗口内则单帧磁盘读取（~25ms）

---

### 🔴 根因2：音频补零到视频长度 + 单次推理（主因：GPU阻塞数秒）

**文件**: `render_loop.py` (process_full_audio)

**问题**: `process_full_audio` 把 PCM 补零到视频段全长（493秒→12341帧），然后一次性调用 Audio2Lip 对12341帧做推理。GPU被独占**数秒到数十秒**，渲染循环完全冻结。

**日志证据**（改造前，batch=500场景）:
```
[TIMING] gen=234.9ms    ← Audio2Lip 推理独占GPU
[RenderLoop] Frame 1, FPS: 0.0, frame_time=798.6ms   ← 启动卡1秒
```

**修复**: 
- 去掉补零，Audio2Lip 只处理实际TTS长度
- 分块推理（LIP_CHUNK_FRAMES=125，每块约5秒音频，~100ms GPU）
- 第一块就绪即播放PCM，其余块异步完成
- 渐进式口型系数写入（`_lip_filled`），渲染线程不用等全部完成

---

### 🔴 根因3：双时钟节拍冲突（主因：FPS在25附近抖动）

**文件**: `render_loop.py` + `unity_capture.py`

**问题**: 渲染循环以 `perf_counter` 锚点做40ms节拍；pyvirtualcam 的 `sleep_until_next_frame()` 内部又以 `_last_frame_t` 做另一个40ms节拍。两个自由运行的时钟互相拍打，造成周期性丢帧。

**日志证据**: FPS在 24.5↔15 之间摆动，从未稳定过25。

**修复**: `sleep_until_next_frame()` 从 `send_frame()` 移除。帧节奏**单一来源**是 `RenderLoop._run()` 的 `perf_counter` 锚点，camera 只负责接收。

---

### 🟡 根因4：cudnn.benchmark 在此机器上是地雷（主因：首块音频延迟5-6秒）

**文件**: `models.py`

**问题**: 开启 `cudnn.benchmark=True` 后，每种**新输入形状** × **每个线程**都触发一次完整自动调优（本机实测 6-19秒）。render线程和api/audio线程各有独立调优缓存，启动预热帮不了工作线程。

**实测验证**:
```python
# cudnn.benchmark=True
T=126: 6126ms   # 首次（autotune）
T=126: 6ms      # 同线程第二次
# 不同线程：
worker thread first: 18.70s  # 重新付费！
```

**收益对比**: Generator 在 benchmark=True 时 19.9ms，False 时 21.9ms（仅快2ms/帧）。代价远大于收益。

**修复**: 关闭 `cudnn.benchmark`，保留无autotune开销的 TF32。

---

### 🟡 根因5：虚拟摄像头 sender 在渲染线程内阻塞5-10ms

**文件**: `unity_capture.py`

**问题**: `send_frame()` 的 `virtual_cam.send()` 是一次共享内存复制（2560×1440 BGR ≈ 11MB），耗时5-10ms，在gen+post（35ms）之后把总耗时推到40-45ms，超过40ms预算。

**日志证据**（改造前，send日志）:
```
send=9.4ms  send=11.3ms  send=10.7ms  send=7.5ms
```

**修复**: 独立发送线程（latest-wins），渲染线程只做 `submit()`（0ms），camera由发送线程以25fps节拍驱动。starved时重发上一帧保持稳定帧率。

---

### 🟡 根因6：视频帧号=音频口型号（音频结束后画面定格）

**文件**: `render_loop.py` (旧版 `_process_batch`)

**问题**: 旧代码的 `video_idx` 和 `lip_idx` 使用同一个 `lip_idx`（由音频驱动）。音频播放结束后 `_get_lip_coeff_for_current_audio()` 返回 `_total_lip_frames-1`（hold-last），画面永远定格在同一帧，角色完全静止直到下次推送音频。

**修复**: 
- 引入独立的 **video clock**（`video_idx = int((now - video_start) * fps) % total_frames`），背景帧永远按25fps在角色段里循环推进
- 音频结束后设 `AUDIO_END_GRACE_S=1.0` 秒宽限期（hold-last口型），超时后 `_lip_coeffs=None`，回归待机（闭口口型 + 背景继续推进）

---

## 改造后效果对比

| 指标 | 改造前 (log6.txt) | 改造后 (smoke_test3) |
|------|-------------------|---------------------|
| 待机FPS | 4→15→25（过3min才到25） | **24.5-25.2**（2s内） |
| 帧率卡顿 | 频繁掉到 15fps 不恢复 | 74%的帧 ≥24fps，无持久卡顿 |
| 预载延迟 | 10-15秒（batch=500） | <0.5秒（batch=100 + 并行解码） |
| 音频首块延迟 | ~6秒（cudnn autotune） | <100ms |
| send开销 | 5-11ms/帧 | 0ms（独立线程） |
| 音频结束后 | 画面定格 | 背景继续推进，1秒后回归待机 |
| 启动预热 | 无（首帧卡23秒） | 0.4秒一次性 |
| 测试覆盖 | 31 passed（t6 历史坏） | **141 passed**（t6 重写） |

---

## 修改的文件清单

| 文件 | 改动 |
|------|------|
| `sequence_frame_pack.py` | **重写** BatchPreloader: 并行解码、即时预载、wrap感知、generation淘汰、去掉渲染线程同步加载 |
| `render_loop.py` | **重写**: 独立video clock、分块Audio2Lip、渐进口型、1秒宽限期、去掉 sleep_until_next_frame |
| `unity_capture.py` | **重写**: latest-wins独立发送线程、重发机制 |
| `models.py` | 关闭 cudnn.benchmark、TF32、启动预热 |
| `audio_player.py` | 增加 `output_latency_s` DAC延迟跟踪 |
| `compositor.py` | 增加 `_state_lock` 保护 EMA 状态（跨线程安全） |
| `main.py` | batch_size 默认 500→100、调用 `unity_capture.start()` |
| `api/app.py` | push_full 改为异步线程处理 |
| `tests/test_t5_render_loop.py` | 更新音频宽限期行为断言 |
| `tests/test_t6_unity_capture.py` | **重写** 为 pyvirtualcam + async sender 测试 |
| `tests/test_t10_audio_api.py` | 修复异步 push_full 竞态断言 |
| `tests/test_t16_sequence_integration.py` | 补全 mock character 的 total_frames |

---

## 验证方法

```bash
# 1. 单元测试（141个）
python -m pytest tests/ -q

# 2. 启动服务器
python -m realtime_serve.main --character-dir wy --target-fps 25

# 3. 观察日志
# - warmup <1s
# - FPS 稳定24-25
# - batch 每4秒推进100

# 4. 推送真实音频（HTTP）
curl -X POST http://localhost:8000/audio/push_full \
  -H "Content-Type: application/json" \
  -d '{"pcm_base64": "<base64-encoded-16k-int16-pcm>"}'

# 5. 观察音频模式日志
# - Full audio → Ready 间隔 <200ms（分块推理）
# - lip_idx 递增、drift ≈ 0ms
# - 音频结束后1秒回归待机，背景继续推进
```

---

## 阶段 1：输出缓冲池（Output Buffer Pool）— 2026-09-02

在 6 个根因修复后，帧率仍在 18-25fps 之间波动。根因是生成抖动直接暴露给摄像头，且 `--target-fps` 同时驱动生产节拍和视频时钟。

### 架构变更

- 新增 `FrameBuffer`（默认 8 帧，环形预分配）和 `OutputStreamer`（唯一 pacing 时钟）。
- `RenderLoop` 不再限速，全速渲染入缓冲；`OutputStreamer` 以 target_fps 消费输出。
- `--target-fps` 现在**只约束最终摄像头输出帧率**，不再参与生产节拍。
- 音频播放由 `OutputStreamer` 在段首帧显示时刻触发，并做缓冲深度前视补偿。

### 新增文件

- `realtime_serve/output_buffer.py` — `FrameBuffer` + `OutputStreamer`
- `realtime_serve/RUNBOOK.md` — 运行手册
- `tests/test_output_buffer.py` — 缓冲与 pacing 单测

### 修改文件

- `realtime_serve/render_loop.py` — 去生产 pacing，改 put 入缓冲
- `realtime_serve/unity_capture.py` — 移除 latest-wins 异步线程，改为同步 sink
- `realtime_serve/main.py` / `config.py` — `--buffer-frames` / `--preroll-frames` / timeBeginPeriod
- `realtime_serve/api/app.py` — `/status` 扩展 production_fps / output_fps / buffer_depth / underruns 等字段
- `tests/test_t5_render_loop.py` / `tests/test_t6_unity_capture.py` / `tests/test_t17_status_api.py` — 适配新架构

### 阶段 1 默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--buffer-frames` | 8 | 输出缓冲容量 |
| `--preroll-frames` | 4 | 启动/新段预填充 |
| `--target-fps` | 25 | 仅输出 pacing |

### 阶段 1 运行观察

启动服务后（`python -m realtime_serve.main --character-dir wy --target-fps 25`）观察到：
- `production_fps` 16-25，`buffer` 深度 0-1。
- 结论：生产能力平均 ≈ 输出率，缓冲池无法吸收抖动。必须先提升产能。

### T0b 根因三件套（已实施）

1. **face_repair ONNX IOBinding + warmup**
   - `realtime_serve/face_repair.py`：使用 `IOBinding` 绑定输入/输出到 CUDA，减少 CPU-GPU 拷贝。
   - `main.py`：初始化后调用 `face_repair.warmup()` 消除首帧/周期 16ms 尖峰。

2. **GPU→CPU 传输与 compositor 拆锁**
   - `realtime_serve/render_loop.py`：新增独立 CUDA stream 做异步 `GPU→CPU` 传输；CPU 同步做 compositor 的 ori crop resize/mask 准备，两者重叠。
   - `realtime_serve/compositor.py`：拆出 `prepare_crop()` / `blend()` / `_resolve_mask()`，支持 CPU  prep 与 GPU transfer 并行。

3. **生产帧耗时直方图**
   - `RenderLoop` 每帧测量 `gen` / `comp` / `repair` / `put` / `total`。
   - 每 5 秒日志打印 `p50/p95/p99`，用于定位剩余尖峰。

### 阶段 1 验证

- `pytest tests/` — 148 passed
- 启动服务观察新日志中的 timing 分位数，确认 face_repair / transfer 优化效果。

---

## 残留优化项（未在本次范围）

1. **face_repair ONNX 推理 p95=16ms**: 每 ~60 帧出现一次，是 ONNX CUDA stream 冷启动。可考虑 ONNX session 的 IOBinding 或预热。
2. **Generator p95=40ms**: 少数帧 gen spike 到 40-84ms（CUDA 内核调度抖动）。可考虑固定 TensorRT 替代 cudnn。
3. **周期性 ~19fps 下探**: 每100帧（batch边界）的 GPU→CPU 传输（`torch.Tensor.to('cpu')`）与 compositor 的 numpy 融合在同一流水线中互相等锁。可考虑 CUDA Stream 并行。
