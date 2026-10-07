# EDTalk 实时推理服务运行手册

## 重要语义变更（2026-09-02）

`--target-fps` 现在**只控制最终向虚拟摄像头的输出帧率**，不再限制渲染生产速度。

- 旧行为：`RenderLoop` 以 target_fps 节拍生产并直接发送。
- 新行为：`RenderLoop` 全速渲染入 `FrameBuffer`；`OutputStreamer` 以 target_fps 消费并输出。
- 结果：输出更稳定，角色动作速度不再随生产抖动而变（不再"快放"）。

## Generator CUDA Graph

启动时 Generator 前向被捕获为 CUDA Graph（日志 `==> Generator CUDA Graph captured (fp32)`）。每帧推理从上百次逐层 kernel launch 变为一次图重放，消除 GPU 层间空转。捕获失败自动回退 eager 推理（日志 `capture failed`）。要求输入形状固定 1×3×256×256（本服务恒定满足）。

## 启动参数

| 参数 | 默认值 | 单位 | 说明 |
|------|--------|------|------|
| `--target-fps` | 25 | fps | 仅约束摄像头输出帧率 |
| `--buffer-frames` | 16 | 帧 | 输出缓冲容量；每帧约 11MB @ 1440×2560。加大可吸收更长尖峰串，但不能弥补平均产能不足 |
| `--preroll-frames` | 8 | 帧 | 启动/新段后需预填充的帧数；必须 ≤ buffer-frames |
| `--batch-size` | 100 | 帧 | 角色素材预加载批次 |
| `--no-face-repair` | 默认 | - | 关闭面部修复（推荐，输出最流畅） |
| `--face-repair on` | - | - | 每帧修复。已改为 **GPU 内联（IOBinding 零拷贝）**，消除了每帧两次 GPU↔CPU 往返；但 GPU 推理本身 ~6-7ms 仍在，产能紧张时仍建议关闭 |
| `--face-repair adaptive` | - | - | 自适应：仅当输出缓冲深度 ≥6 帧（产能有余量）时修复，紧张时自动跳过。GPU 内联模式下推荐此项 |
| `--gaze-correction` | off | - | 视线纠正三态：裸 flag=on（向后兼容）、off/on/adaptive。开启后 **compositor mask 扩展双眼区域**——输出眼睛变为 Generator 渲染的纠正眼（off 时为原视频原生眼）。**运行时可热切**：`POST /gaze/params`（off 启动后无法热开，409） |
| `--gaze-convergence` | 0.05 | [-1,1] | 收敛角。素材人物坐姿/朝向不同时可调整；负值发散。**运行时热更**：`POST /gaze/params {"convergence": x}` 下一帧生效（覆盖 CLI 值，重启后失效） |
| `--gaze-ckpt` | weights/current_model.ckpt | 路径 | gaze 生成器权重 |
| `--no-unitycapture` | - | - | 无虚拟摄像头时仍可运行；OutputStreamer 正常 pacing |
| `--gen-fp16` | 关闭 | - | 开启 Generator fp16 推理。**默认关闭**：实测在 RTX 4080 SUPER 上慢于 fp32+TF32（TF32 已覆盖 Ada 的大部分半精度收益，autocast 只剩 cast 开销） |
| `--segments` | 不传 | 段名列表 | **段播放调度**（见下方调度章节） |
| `--host` | 127.0.0.1 | 地址 | **默认回环绑定**（E4 安全默认）。远程消费需显式 `--host 0.0.0.0` 并强烈建议同时设 `--api-token`；从旧版本升级且依赖 0.0.0.0 直连的部署需同步改启动参数 |
| `--api-token` | 无 | 字符串 | 设置后全路由要求 `Authorization: Bearer <token>`（401）；进程内 facade 调用无需 token |

## 段播放调度（--segments）

三种模式：

```bash
# 默认（不传）：第一个段无限循环（与历史行为一致）
python -m realtime_serve.main --character-dir wy

# 指定列表：按给定顺序循环（段名 = orifra/ 下目录实际名称，支持中文）
python -m realtime_serve.main --character-dir wy --segments 1 55

# 自动：全部段按自然排序循环（"6"排在"11"前，数字段按数值比较）
python -m realtime_serve.main --character-dir wy --segments auto
```

- 每段播完一遍（total_frames/fps 秒）自动切下一段；**边界切换为帧级续接**（M8：不清缓冲、不重布防 preroll，旧段尾帧按序播完接新段）。`POST /segment/switch` 默认 `at=immediate` 为硬切（flush 全弃 + preroll 重布防，≤0.3s 不连续）；`at=segment_end` 入队边界无缝切换。
- ⚠️ 已知边界语义限制（待办，见 `TODOS.md`「音频感知的段边界切换」）：当前边界按**片段循环周期**判定——音频播到一半切段会保持音频继续、画面跳新段。计划改为"音频合成+播放完毕后才切"，待音频播放功能优化后处理。
- **启动即从调度首段开播**：`--segments 22 55` 启动后直接播 '22'（不再先播角色默认段一整轮）；调度首段分辨率与默认段不同时，摄像头在启动阶段重建到新段分辨率。
- 段名区分大小写，以 `orifra/` 下目录实际名称为准（可用 `/status` 的 `available_segments` 查看）。
- 手动 `POST /segment/switch` 仍可用：切到序列内段 → 调度从该段继续循环；切到序列外段 → 调度挂起（`/status` `position: -1`），切回序列内段恢复。
- 启动校验：段不存在、orifra 目录为空、段首帧不可读 → fail-fast 并给出原因。
- 混合分辨率（各段分辨率不同）→ 仅 `[INFO]` 提示，按各段**原生分辨率直出**，见下节。
- "auto" 是保留字；真有名为 auto 的段时请重命名目录（或仅用 /segment/switch 手动切）。

### 混合分辨率直出

各段分辨率不同（如 1440x2560 与 1080x1080 混排）时不再缩放输出：

- 段切换时 `UnityCaptureSender.update_resolution()` 关闭并按新段原生尺寸重建虚拟摄像头（pyvirtualcam 帧尺寸在创建时固定，重建是唯一途径）。
- 已在传输的个别过渡帧/重建失败时按摄像头尺寸缩放兜底（限频 `[WARN]`，10s 一次）——若持续出现该告警说明重建失败，输出退化为缩放模式。
- 消费端注意：Unity Capture 滤镜（DH-Live 定制版）在**连接时**协商分辨率；不重连时滤镜按等比缩放+黑边适配新段画面（DH-Live 版滤镜每帧读取实际尺寸、接受任意分辨率）。要让摄像头以实际分辨率输出，消费端重连一次（或重启拉流端）即可。

## 控制面（API 与嵌入）

运行时控制统一走进程内 facade（`realtime_serve/service.py` 的 `RealtimeService`，嵌入契约见 `realtime_serve/EMBEDDING.md`）；HTTP 是它的薄壳。语义错误响应统一三段式 `{"problem","cause","fix"}`；状态码分层：422=请求体 schema、400=语义、404=段不存在、409=启动未启用、413=载荷超限、503=未就绪。

### 快速开始（5 命令）

```bash
# 1. 启动（默认回环绑定；日志出现 "API server running" 即就绪中）
python -m realtime_serve.main --character-dir wy --target-fps 25 --face-repair on --gaze-correction on

# 2. 看状态——就绪判据：preroll_state=complete 且 current_segment 非空
curl http://127.0.0.1:8000/status

# 3. 推一句 TTS 音频（16kHz 单声道 int16，base64）——唇形随音频动
python push_audio.py some_tts.wav     # 或 POST /audio/push_full {"pcm_base64": "..."}

# 4. 切段：立即硬切（≤0.3s）；at=segment_end 则边界帧级无缝
curl -X POST http://127.0.0.1:8000/segment/switch -H "Content-Type: application/json" -d "{\"segment\": \"22\", \"at\": \"segment_end\"}"

# 5. gaze 角度热更（下一帧生效）与修复门控热切
curl -X POST http://127.0.0.1:8000/gaze/params -H "Content-Type: application/json" -d "{\"convergence\": 0.08}"
curl -X POST http://127.0.0.1:8000/repair/params -H "Content-Type: application/json" -d "{\"mode\": \"adaptive\"}"
```

### 端点一览

| 端点 | 作用 | 备注 |
|---|---|---|
| `POST /audio/push` | 流式音频分块 | 16kHz int16；10MB 上限（413） |
| `POST /audio/push_full` | 整句 TTS（推荐，唇形最稳） | 异步推理，展示时点开播 |
| `POST /segment/switch` | 切段 | `at=segment_end` 为 last-write-wins 队列；immediate 会清除挂起 |
| `POST /segment/schedule` | 运行时改播放调度 | 默认不打断当前段（边界生效）；`align=true` 立即对齐 sequence[0] |
| `POST /gaze/params` | gaze 角度/门控热更 | 启动 off 时 409（`gaze_capable=false`） |
| `POST /repair/params` | 面部修复门控热切 | 启动 off 时热开 409（`repair_capable=false`） |
| `GET /status` | 状态快照 | **只增不改**；新增字段见下表 |

### 嵌入（AI-Vtuber）

对接清单见 AI-Vtuber 仓 `specs/integration/EDTalk实时推理服务对接纪要.md`；方法面契约、三拓扑矩阵与版本规则见 `realtime_serve/EMBEDDING.md`。

### 参数换算

- `buffer-frames=16`（默认）→ 约 176MB 内存，吸收深度 0.64s。
- `buffer-frames=8` → 约 88MB，吸收深度 0.32s。
- `buffer-frames=300`（上限）→ 约 3.3GB，吸收深度 12s（不推荐：只对"平均产能 ≥ 25fps"才有意义）。

## 状态字段（GET /status）

| 字段 | 含义 |
|------|------|
| `production_fps` | 渲染生产速度 |
| `output_fps` | 摄像头输出目标帧率 |
| `fps` | `output_fps` 的兼容别名（已弃用） |
| `buffer_depth` | 当前缓冲深度 |
| `underruns` | 输出端欠载重发计数 |
| `preroll_state` | `prerolling` / `complete` |
| `buffer_full_warn` | 缓冲持续满（检查摄像头消费端） |
| `segment_sequence` | 调度信息 `{mode, position, sequence}`；`position: -1` = 手动切到序列外、调度暂停 |
| `pending_switch` | 挂起的 segment_end 切换目标（无挂起则无此字段） |
| `prefetch` | 无缝切换预取状态 `{state: idle\|loading\|ready\|discarded, segment}` |
| `gaze_params` | gaze 参数回显 `{convergence, mode, smooth_alpha}`（当前生效值） |
| `gaze_capable` / `repair_capable` | 组件是否已构造——false 时对应热切端点返回 409 |
| `repair_mode` | 面部修复当前门控（off/on/adaptive） |
| `camera_resolution` | 虚拟摄像头当前输出分辨率（段切换时自动重建） |

## 常见问题与处置

### 画面卡顿 / underruns 持续增加

1. 看 `production_fps`：
   - 若长期 < `output_fps` → 产能不足，考虑关闭 `--no-face-repair` 或降低 `--target-fps`。
   - 若 `production_fps` 正常但 `underruns` 增加 → 检查 UnityCapture / OBS 是否消费变慢。
2. 临时降级：设置 `--buffer-frames=1` 回到近旧行为。

### 启动后摄像头黑屏数秒

这是正常的 preroll 等待。默认 4 帧 ≈ 0.16s。需要更快启动可设 `--preroll-frames=0`（但会牺牲启动平滑度）。

### 推音频后嘴型延迟

- **同步机制（2026-09-03 重构：消费端帧锁定）**：音频按 640 采样/帧切片随帧出，
  OutputStreamer 在**展示该帧的时刻**推给播放器——音频位置 ≡ 展示帧位置 ≡ 唇形
  位置（按构造锁定）。口型领先量 = DAC 延迟（~20-40ms ≤ 100ms），**绝不滞后**。
- 推理未就绪时音频自动等待（HOLD：闭嘴、不分配音频块），推理补上后从断点继续
  推进——不跳帧、不重复。
- 旧墙钟追音频机制（elapsed + `_buffer_latency_s`/`_output_latency` 补偿）已删除。
- 若仍感觉不同步：检查音频输出设备延迟是否异常（`AudioPlayer.output_latency_s`
  平滑值，正常 < 0.1s）；underrun 重发帧不推音频属预期（音画同步停顿）。
- 音频感知的段边界切换（播完当前音频再切）为待办，见 `TODOS.md`。

### 无摄像头模式测试

```bash
python -m realtime_serve.main --character-dir test_data/wy --no-unitycapture
```

输出 pacing 和音频触发仍正常工作，只是不向摄像头送帧。

### 视线纠正（gaze）排查

开启命令：

```bash
python -m realtime_serve.main --character-dir wy --target-fps 25 --gaze-correction on
```

| 现象 | 诊断 | 对策 |
|------|------|------|
| 启动报 "gaze checkpoint not found" | 权重路径错误 | 检查 `--gaze-ckpt` 路径，或去掉 flag 用默认 off |
| 眼睛无变化且 `gaze_frames.nan_skips` 持续增长 | 资产 facemark 全 NaN（Windows safe_mode 预处理产物） | 不带 `--safe_mode` 重跑预处理步骤 10 |
| 眼睛偶尔不修正（`blink_skips` 增长） | 正常眨眼旁路 | 无需处理；素材本身眨眼少则该计数低 |
| `gaze_graph: eager` | CUDA Graph 捕获失败（已回退 eager，略慢 ~1ms） | 无需处理；性能敏感时检查驱动 |
| 开 gaze 后 repair 触发率下降 | 两者共享 buffer 余量门控（adaptive） | 对比 `gaze_frames.corrected` 与 `/status` repair 指标；必要时 gaze 用 on、repair 用 off 明确优先级 |

架构说明：纠正作用于 **Generator 输出**（rendered 256，GPU 内联）——输入端纠正会被 Generator 重绘
抹除（probe 实测 1.8x 噪声 vs rendered 端 29x）。mask 双眼区域随 `--gaze-correction` 开关自动启用/回退。

### 快速开始（跳过 preroll 的测试捷径）

```bash
python -m realtime_serve.main --character-dir test_data/wy --preroll-frames=0
```

## 单角色微调（Generator）操作手册（2026-09-04，wy2 验证通过）

> 完整计划与根因分析见 `FINE_TUNE_FIX_PLAN.md`。wy1（旧配置）失败的教训与 wy2 成功的配方都在这里。

### 成功配方（wy2 实测，全指标过评估门槛）

```bash
python train_fine_tune.py --datapath wy --exp_name wy2 --only_fine_tune_dec --iter 30000
# 新默认已内置: lr 1e-4 / batch 4 / 四路采样(near±64 60%+self 10%+far 30%) /
# held-out 末10%剔除 / 嘴区加权 vgg·l1 ×4 / GAN 全图 / ortho 0
```

**选型**：训练后用评估定版，不要默认取最后一个 ckpt —— D 过 14k 步即饱和，20k-40k 指标几乎持平，wy2 定版 `030000.pt`（40k 略差）。

### 语义备忘（为什么是这些参数）

- **`--only_fine_tune_dec` 是主模式**：冻结 enc/fc/direction 保住 Audio2Lip 接口与 pose 解耦。`lip_space/pose_space` 余弦损失在冻结配置下梯度为零（死代码）— 别照搬底模 step2 的损失清单。
- **同帧自对 10% 是解耦的关键**：推理时 pose alpha 来自 source 自身（`render_loop.py:727`），训练必须显式覆盖这个恒等条件，否则 dec 的 pose 响应被重写 → 姿势漂移 → 贴回错位。
- **GAN 不加权是结构决定的**：全图 Discriminator 单 logit 输出，无空间维度可加权；嘴区裁剪送 D 会破坏底模 dis 感受野。
- **背景权重固定 1.0**：背景锚定压制 direction 膨胀；降背景权重会反噬背景 SSIM 门槛。

### 评估流程（训练完必须跑，别凭目测定版）

```bash
python tools/eval_finetune.py --cand-ckpt ckpt_models/<exp>/checkpoint/<iter>.pt --num-frames 100
```
门槛（底模校准值见 `FINE_TUNE_FIX_PLAN.md` 第 4 节）：姿势 MAE ≤ 11.1px、贴回代理 ≤ 3.6px、背景 SSIM 降幅 ≤ 0.02、开合比 0.8-1.25。基线噪声底 ~0.3px。

### 训练环境判读

- **显存悬崖**：step 耗时突然 ×3 阶跃（如 782ms→2535ms）= WDDM 溢出共享内存指纹，不是线性变慢。batch≤4-6 安全区（batch4 实测 5.79GB only_dec / 12.03GB 三路编码），batch=8 禁区（15.05GB 贴 16GB 上限，大概率是历史崩溃的真凶）。
- **D 饱和**：d loss 趋零（<0.001）从某步持续 → 对抗项失效，训练进入平台期，更长训练无收益。
- **只加载自产 ckpt**：`torch.load(weights_only=False)`（ckpt 内嵌 args Namespace）是 pickle RCE 面 — 不要加载外部来源的 ckpt。
- runtime312 环境的 tensorboard 曾出现"dist-info 在、包目录失"的半安装 — 修复：`python -m pip install --force-reinstall --no-deps tensorboard`。

### 时间盒

batch4 + 4080S：50k iter 计划约 5-6h；D 饱和后（~15-20k）进入平台期，30k 是经济截断点。若评估不过门槛：先查 `--gan_weight 0` 消融（D 过拟合干扰），仍不过再启动 cross_recon 锚定（计划 T6，需 `data_preprocess/mouth_transplant.py` 嘴部移植管线）。

## 回滚

本阶段改动集中在 `realtime_serve/output_buffer.py` 和渲染管线。若需要回退，可用 git revert 最近一次提交。


## 视线纠正眼源切换（GAZE_TIMING_PLAN，2026-09-21）

- `--gaze-eye-source ori|rendered`（默认 ori）：两种模式都在面部修复**之前**
  贴回（最终门裁决 2：全脸统一修复——纠正后的眼睛随整脸进 GPEN，眼与嘴鼻
  同一遍锐化）；ori=眼源为 orifra 原生眼，rendered=legacy Route C' 自纠正
  （行为回滚路径 D，逐位复现旧管线）。
- 运行时热切：`POST /gaze/params {"eye_source": "rendered"}`（无需重启）。
- bypass 不变式：gaze on（ori）时眨眼/门关帧贴未修正原生眼——最终眼永远原生源。
- 观测：`GET /status` → `gaze_params.eye_source`、`gaze_frames.pristine_fallback`
  （预载窗口未命中、眼源走就地降级的帧数；持续增长 = 预载追不上，检查磁盘/并发）。
- 排查：ori 模式下眼色不对 → 先查 pristine 通道序（单测 U10）；接缝可见 →
  `eye_source=rendered` 复测分流定位；均不达标 → 回退 D（rendered）或 off。
- 质量验收协议：对比视频必须在生产混合域出片（T6），不得用离线 probe 域替代批准。
