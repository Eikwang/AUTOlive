# realtime_serve 功能链络流程图（管线地图）

> 由代码审读生成（main / service / render_loop / gaze_correction / compositor /
> output_buffer / models / audio_* / unity_capture / api/app + 三份 PLAN 文档），
> 作为视线纠正优化需求的输入底图。2026-09-21。

## 0. 一图总览

```
                ┌────────────────── 控制面（HTTP，FastAPI 线程池，同步端点）──────────────────┐
TTS / 上游系统 ──▶ POST /audio/push_full  /audio/push  /segment/switch  /segment/schedule   │
                │ POST /gaze/params  /repair/params                 GET /status              │
                └──────┬──────────────────────────────┬───────────────────────────────────────┘
                       ▼                              ▼
                RealtimeService facade（校验 / 锁 / 生命周期 / 嵌入契约 EMBEDDING.md）
                       │
      ┌────────────────┼─────────────────────────────┬──────────────────────┐
      ▼                ▼                             ▼                      ▼
 音频链(数据面)   RenderLoop 生产者线程          段调度/参数热更          状态查询
 (push_full→     逐帧管线（跑满产能~50fps）      (帧头应用/边界触发)
  Audio2Lip)          │ 产出帧+音频块 meta
                      ▼
                FrameBuffer（16 帧环形，满则 put 阻塞 = 背压）
                      │ get()
                      ▼
                OutputStreamer 消费者线程 ——★唯一节拍钟 25fps
                      ├─▶ AudioPlayer（声卡，帧锁定音频：音频等嘴唇）
                      └─▶ UnityCaptureSender → pyvirtualcam → 直播/Unity
```

## 1. 线程与缓冲清单

| 线程 | 职责 | 关键约束 |
|---|---|---|
| RenderLoop 生产者 | 逐帧管线（§3），**不节拍**，跑满 GPU 产能 ~50fps | 单帧异常熔断（750 帧放弃） |
| OutputStreamer 消费者 | 唯一节拍钟 25fps；显示时刻推音频块 | underrun 重发上帧 |
| BatchPreloader 预载 | 滑窗 2×100 帧双槽；8 线程 JPEG 解码池；mask 预计算 | 永不阻塞渲染线程 |
| PrefetchLoader | 段切换单飞预取 batch 0（token 取消） | 尽力而为 |
| push_full 工作线程 | Audio2Lip 渐进推理（每次 push 一个，_push_lock 串行） | 块间 sleep 4ms 让路 |
| sounddevice 回调 | 声卡 20ms 块消费 | |
| FastAPI 线程池 | HTTP 同步端点（慢调用不堵事件循环） | |

缓冲：FrameBuffer（16 帧 ×~11MB，preroll 8）／`_lip_coeffs` 渐进口型系数／预载双槽窗口／prefetch 独立缓冲。

## 2. 启动装配链（main.py → setup_service，顺序即依赖）

```
1  CharacterDataPack     段发现 / metadata+facemark+gaze_data 载入 / 首批同步预载
1b 段序列校验            --segments fail-fast（慢模型加载之前）
2  ModelManager          Audio2Lip + Generator；warmup；Generator CUDA Graph 捕获
3  AudioBuffer + AudioProcessor（noice.wav 空闲噪声载入）
4  FaceRepair?           --face-repair on|adaptive；ONNX GPEN-256；warmup
5  MouthCompositor       blur7/EMA0.3；set_compositor → mask 预计算管线打通
5b GazeCorrector?        --gaze-correction on|adaptive；ckpt 校验；CUDA Graph 捕获；
                         set_eyes_enabled(True)（mask 加眼区）
6  FrameBuffer           capacity=buffer_frames(16), reserve≤5
7  UnityCaptureSender?   首段原生分辨率开摄像头
   AudioPlayer + RenderLoop + OutputStreamer（facade start: streamer 先于渲染线程）
```

## 3. 生产者逐帧管线（★ = 视线纠正位置）

```
RenderLoop._run() ─ 每迭代 ─▶ _check_segment_schedule() → _process_frame()
═════════════════════════ _process_frame 逐帧管线 ═════════════════════════
 ① 段边界检查    pending_switch(边界无缝续播) 优先 → scheduler.advance() 循环推进
 ② 参数帧头生效  /gaze/params 排队的 convergence/smooth_alpha/mode 在此应用
                 （mode on↔off 联动 eyes_enabled + mask epoch 失效）
 ③ 视频位置      _video_idx = (墙钟-锚点)×fps % 段总帧数（循环播放）
 ④ 口型系数      _next_lip_frame():
                   ├ 无音频流          → idle: 闭嘴系数（noice.wav→mel→Audio2Lip, 有缓存）
                   ├ 推理落后(hold)    → 闭嘴系数，不分配音频块（音频等嘴唇）
                   └ 正常(play)        → coeff[n] + 640 样本音频块 挂帧 meta
 ⑤ 取帧          ensure_loaded(idx) → get_frame(idx):
                   orifra 全帧(CPU) / cropped 256² GPU [-1,1] / meta 裁剪坐标 /
                   facemark 68点(256空间) / 预计算 mask(嘴[+眼])
 ⑥ Generator     CUDA Graph replay: (cropped, lip20, cropped) → rendered 256² [-1,1]
                 [gen p50 8.9ms；CUDA graph 静态缓冲，输出须当帧消费]
 ⑦ ★视线纠正     单调用点，repair 之前（GAZE_TIMING_PLAN 最终门裁决 2:
   眼源分流         全脸统一修复）。差异只剩眼源张量:
                    'rendered'(legacy C')：自纠正，_gaze_allowed 整体门控，
                      逐位复现旧行为（U11 回归）
                    'ori'(默认)：correct(eye_source=pristine原生眼,
                      canvas=rendered)——调用不受门控（ENG F2），
                      生成段受 _gaze_allowed；纠正后的整脸随后进 GPEN
                    位置不变式：输入端修正会被 Generator 重绘抹除
                    （probe 实测 1.1x 噪声，2026-09-21 复验）
 ⑧ 量化          [-1,1] → uint8 CHW BGR（GPU）
 ⑨ CPU 准备      mask 解析（预载 mask 优先 / compositor 回退生成）
                 + prepare_crop（ori 裁剪区域预缩放 256²，供混合）
 ⑧' 面部修复     if repair≠off 且 _repair_allowed()（adaptive: buffer≥6）:
   GPEN-256         ORT IOBinding GPU 内联，α=0.7 [repair p50 7.2ms]
                    ⚠ 整脸统一处理（最终门裁决 2）：视线纠正后的眼睛随整脸
                    进 GPEN——眼与嘴鼻同一遍修复锐化（GPEN 保结构，
                    legacy 模式数周生产验证纠正方向经修复保留）
 ⑪ 下载          唯一一次 GPU→CPU（256² 渲染结果）
 ⑫ 贴回合成      compositor.blend: rendered×mask + ori_crop×(1-mask) → 贴回全帧
                 mask = 嘴部 hull(α=1) → 下颌+鼻翼(α=0) 平滑过渡；
                 eyes_enabled 时并上眼区（1.2x 内圈 / 1.7x 外圈过渡带）
 ⑬ 入队          frame_buffer.put(全帧, meta=音频块) —— 满则阻塞
                 [put p50 20ms = 背压 = 产能富余的实时度量]
```

## 4. 视线纠正内部流程（Route C' 展开，本次优化目标）

```
GazeCorrector.correct(eye_source 256² GPU, canvas 256² GPU, facemark_256,
                      gaze_frame, generate) —— 双 tensor 契约（眼从
                      eye_source 读、贴进 canvas、返回新张量，均不修改）
──────────────────────────────────────────────────────────────────────────
 门控/旁路（零成本路径，逐帧计数）:
   gaze_frame 存在（预处理 gaze_data/<段>.npy，首选路径）:
     state=1 眨眼       → 贴未修正原生眼（ori 模式）/ 整帧旁路（rendered 模式）
     state=2 检测失败   → 不贴回（几何不可信）+ 清生成历史（ENG F3）
     state=0            → 眼中心/eye_size 全部来自预计算（离线时序，最稳）
   bypass 不变式（ori 模式）：gaze on ⇒ 最终眼一律原生源——眨眼/门关帧
   贴未修正原生眼，消除 Generator 眼/原生眼交替闪烁（GAZE_TIMING_PLAN）
   gaze_frame 为 None（该段无 gaze_data）→ 实时 facemark 回退路径（更噪）:
     facemark NaN       → 旁路 (nan_skips++)
     EMA 眼中心平滑（α=0.4，>20px 大位移降权 0.3）
     eye_size 首帧锁定（1.5×眼眶外接，clamp 16..128）
     EAR 眨眼状态机（CLOSE 0.12 / OPEN 0.16 / 确认 3 帧 / 上限 26 帧）
     → 眨眼中           → 旁路 (blink_skips++)
 纠正执行（两段式，ori 模式；rendered 模式仅第二段且逐位=旧行为）:
   第一段（不受门控, ENG F2）: 原生眼贴回——eye_source 眼区 × 圆形mask
     混入 canvas（眨眼/门关帧到此为止）
   第二段（受 _gaze_allowed 门控）: clone canvas（新 tensor，禁 in-place）
   每眼: 从 eye_source slice 方框(eye_size) → F.interpolate↑64² (antialias)
   → batch [2,3,64,64] → gaze Generator 前向
       CUDA Graph replay（_static_in/_static_angles/_static_out）或 eager 回退
       angles = [[-conv, 0, -1], [+conv, 0, 1]]   conv=convergence_h（热更）
       ⚠ 角度第 2 分量恒 0.0（无垂直/俯仰分量暴露）；±1 为左右眼方向位
   → 时序平滑: gen = 0.7×新帧 + 0.3×上帧（_temporal_alpha 写死，无热旋钮）
       连续跳过 >3 帧 → 清历史防鬼影；convergence 变更 >0.2 → 清历史
   → 逐通道 μ/σ 颜色迁移（参照=eye_source 眼区——眼须像"本人"的原眼；
     rendered 模式下 source==canvas 故与旧行为逐位一致）→ clamp[-1,1]
   → F.interpolate↓回方框 → 圆形 mask(64² GPU 常量, resize 匹配) blend 写回
 错误兜底: 域守卫（形状/设备/[-1,1]）+ 任何异常 → 原样返回 canvas
   (error_skips++，首次打栈，不杀渲染循环)；eye_source 校验在 owner 层（F9）
 热切: set_eye_source('ori'|'rendered')——拒绝式校验，切换清生成历史
 错误兜底: 任何异常 → 原样返回 (error_skips++，首次打栈，不杀渲染循环)
 状态: switch_segment → reset()（眼中心/eye_size/blink/平滑历史/计数全清）
 观测: /status → gaze_state(on|off|adaptive) + gaze_graph(captured|eager)
       + gaze_frames{corrected, blink_skips, nan_skips, error_skips,
         pristine_fallback(预载未命中→就地降级计数)} + gaze_params{...,eye_source}
       + 渲染 timing 直方图 gaze 键（p50/p95/p99，=上传+转换+correct+贴回全程）
```

**与 mask 的耦合（关键）**：纠正后的眼睛能否出现在最终帧，取决于 compositor mask 是否
含眼区——`eyes_enabled`（gaze≠off 时启动置 True；运行时 on↔off 切换经 mask epoch
失效 + 重算，M18）。gaze=off 时 mask 回退嘴-only，输出眼睛 = orifra 原生眼。

## 5. 音频链（数据面）

```
POST /audio/push_full（16kHz int16 b64 整句）
  → facade.push_full_audio → 新工作线程 process_full_audio:
    ├ flush 输出缓冲（keep=0 全丢）+ preroll 重臂（4 帧 ~160ms）
    ├ pcm→mel: 80×16 / 帧, hop200, 滑窗(i-2 .. i+13)        [AudioProcessor]
    ├ Audio2Lip 分块推理: 125 帧/块(+halo, pad 到 128 固定批)
    │   → conv_feat 高斯平滑(k3, σ1) → 渐进发布 _lip_coeffs/_lip_filled
    │   [每块 ~100-200ms GPU；块间 sleep 4ms 让路渲染线程]
    └ 每帧预切 640 样本 PCM 块（16000/25）→ _audio_chunks
  渲染线程 _next_lip_frame 每产出 1 帧消费 1 组 (coeff, chunk)   ← 帧锁定
  OutputStreamer 显示时刻 → audio_player.reset()(audio_start 首帧) + push_pcm(chunk)
  sounddevice 20ms 块回调 → 声卡
  口型领先音频 = 仅 DAC 延迟（≤~40ms）；推理落后时音频等待嘴唇（hold 不分配 chunk）
  语音结束后闭口保持 1.0s（AUDIO_END_GRACE_S=25 帧, 无音频块）→ 回 idle

POST /audio/push（流式分块, seq 排序）→ AudioBuffer.push → ⚠ 无任何消费者
  （render_loop 已不读 audio_buffer：帧锁定改造后流式口型路径成为遗留死路径，
   仅 /status 的 buffer_ms 还在回显其存量）
```

## 6. 消费端

```
OutputStreamer._do_stream_loop（消费者线程，唯一节拍钟 target_fps=25）
  ├ preroll 门: 蓄够 8 帧（启动）/ 4 帧（flush 后重臂）才开闸
  ├ 40ms 节拍: sleep 80% + 忙等尾段；落后时限速 2× 追帧（防突发）
  ├ frame_buffer.get(10ms 超时)
  │   ├ 有帧: audio_start → player.reset()；chunk → player.push_pcm()（显示时刻）
  │   │       sender(frame) → UnityCaptureSender
  │   └ 空帧: underrun++ → 重发上一帧（保摄像头喂帧）
UnityCaptureSender.send: 原生分辨率直出（无管线缩放）；段切换时
  update_resolution 关闭并按新尺寸重开摄像头（过渡帧缩放 + 限频警告）
  → pyvirtualcam(unitycapture 后端) → DH-Live Unity Capture filter → 直播/Unity
```

## 7. 控制面（HTTP → facade → 渲染线程）

```
POST /segment/switch   immediate: pop_prefetch(就绪则零冷启动) → switch_segment(硬切):
                         pack 原子切换（_load_segment → preloader.reset →
                         publish_prefetch → ensure_loaded → current LAST 发布）
                         + flush 全部旧帧 + preroll 重臂 + gaze.reset()
                         + 摄像头分辨率重开 + scheduler.sync_to
                       segment_end: prefetch_segment(尽力) → queue_segment_switch
                         （渲染线程帧头检查 elapsed≥总长 → seamless 续播，last-write-wins）
POST /segment/schedule 装表（list|auto；align=false 只改顺序、边界生效；align=true 立即对齐）
POST /gaze/params      facade 拒绝式校验（NaN/inf/越界一律 400）→ queue_gaze_params
                         → ②帧头应用；启动 off 时 corrector 未构造 → 409
                         （off→on 懒初始化 = TODOS，当前需重启）
POST /repair/params    纯门控热切（模型常驻，直接改 face_repair_mode）
GET  /status           facade 单一构建器: production_fps/output_fps/buffer/underruns/
                       preroll/段序列/prefetch/gaze_*/repair_*/camera_resolution
```

## 8. 数据资产与预处理来源

```
<character_dir>/                          预处理产物（data_preprocess_for_train/）
 ├ orifra/<段>/      全帧 JPEG            ← 最终输出底帧（贴回目标）
 ├ count/<段>/       256² crop JPEG       ← Generator 唯一消费者（非嘴部输出被 mask 丢弃）
 ├ metadata/<段>.npy crop_x/y/size        ← 贴回坐标
 ├ facemark/<段>.npy (N,68,2) 256 空间     ← mask 生成 + gaze 实时回退路径
 ├ gaze_data/<段>.npy                     ← prepare_gaze_data.py step 11（可选）
 │   state(0正常/1眨眼/2检测失败) + l_center/r_center(逐帧,256空间) + eye_size(段级标量)
 └ checkpoint/ audio2lip.pt + new.pt
    realtime_serve/weights/: current_model.ckpt(gaze) + facefix256.onnx(repair)
```

## 9. 现状性能锚点（PERFORMANCE_REPORT.md，CUDA Graph 后 10 分钟实测）

| 阶段 | p50 | 说明 |
|---|---|---|
| gen | 8.9ms | CUDA Graph + fp32/TF32（fp16 实测更慢已禁用） |
| gaze | ~2-3ms（方案预算） | 实测看 timing 直方图 gaze 键 |
| repair | 7.2ms | GPU 内联 IOBinding |
| comp | 0.3ms | |
| put | 20ms | 背压 = 缓冲蓄满 = 产能富余 |
| total | 39.7ms | 产能 ≈ 50fps，输出 25fps，余量约 2× |

## 10. 与视线纠正优化相关的现存约束 / 抓手清单

> 仅列事实与位置，不做方案裁决——供制定优化需求时圈定范围。

1. **修正生效域 = Generator 输出 256²**（gaze_correction.py 模块头：Route C' 定案）。
   眼纹理先经 256 渲染再上采样贴回；ENG 审查有"贴片感/纹理失配"案底（最终帧眼区是
   256 round-trip 软化像素，生成眼偏锐）。画质类优化的主战场在眼区生成模型/分辨率。
2. **角度自由度受限**：angles 第 2 分量恒 0.0（无垂直分量暴露），仅水平 `convergence_h`
   单值热更（`/gaze/params`），无逐眼独立、无眼动时序序列。（2026-09-21 眼源切换
   已落地：约束 1/5/9 由路线 B 处置——详见 GAZE_TIMING_PLAN.md）
3. **时序平滑 α=0.7 写死**（`_temporal_alpha`），无热旋钮（`smooth_alpha` 热更的是眼中心
   EMA，不是生成结果平滑）；连续跳过 >3 帧即清历史（一次 6 帧的眨眼就足以清空）。
4. **EAR 阈值 0.12/0.16 是离线 1080p 域参数**，256 空间未校准（TODOS 在案；G6 修正）。
5. **检测失败语义 = 旁路**：gaze_frame state=2 / facemark NaN → 露出未纠正眼；计划 E5 的
   "hold 上一帧中心继续修正"未落地。两种失效态（眨眼 vs 检测失败）当前视觉表现相同。
6. **adaptive 门控与 face_repair 共用 REPAIR_MIN_BUFFER=6**（render_loop.py:815-830）：
   两个画质功能竞争同一产能信号，无独立旋钮（`--gaze-min-buffer` 在 DX 审查 F5 提过，未实现）。
7. **gaze_data 覆盖率不可观测**：/status 不暴露当前段是否带 gaze_data；缺数据的段静默走
   实时 facemark 回退（噪声大）。`has_gaze_data()` 存在但无消费方。
8. **off→on 运行时热启用未实现**：启动 `--gaze-correction off` → /gaze/params 返回 409，
   需重启服务才能启用（懒初始化 = TODOS）。
9. **无眼区运动门控**：眼中心几乎不动时仍每帧全量重推理（GAN 帧间抖动的来源，时序平滑是
   对策而非消除）。
10. 顺带发现（非视线域）：`/audio/push` 流式路径无消费者（AudioBuffer 只写不读）；
    `AudioBuffer.consume_chunk` 无调用方。

## 11. 本次会话后续

用户基于本地图制定视线纠正优化需求 → 需求成文后进入 /autoplan 评审管线
（CEO → [design/DX 按范围] → Eng，单最终门）。
