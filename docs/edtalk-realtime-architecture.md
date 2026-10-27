# EDTalk 实时数字人服务器架构梳理（B1 交付，2026-10-27 画面输出批次）

> 产出依据：Phase 0 磁盘实证 + 源码逐行核验（autoplan main-20261027-120103）。
> 本文档为批次 R-ceo8 的持久化交付——批次结束后作为数字人链路的权威速查。

## 一、出画链路（RenderLoop → OBS）

```
RenderLoop(25fps 产出线程，render_loop.py:401 _run)
  │ 每 帧: _process_frame(:698)
  │   ├ CharacterDataPack.get_frame(idx) → (orifra 原帧, count 256裁剪, meta crop框, facemark 68点, mask)
  │   ├ 口型系数: Audio2Lip(mel→系数) 帧锁定（_next_lip_frame :542——音频位置≡显示口型位置）
  │   ├ 画外音直通（:743-752）: lip_mode=off 或 facemark 全 NaN → orifra 原帧直出（跳过全套 GPU）
  │   ├ Generator(256 crop) → 视线纠正（gaze_corrector）→ GPEN 面部修复（face_repair）
  │   └ compositor.blend(:167): 256 产物按 meta crop 框贴回 orifra → 全帧
  ├ FrameBuffer(:28 容量环形缓冲) ── OutputStreamer(:197 精确 FPS 节奏泵)
  └ unity_capture 虚拟摄像头 → OBS
```

**贴回机制**：blend 将口型产物贴回 orifra 的 crop 区域——源素材分辨率即输出分辨率
（本仓库 wy 角色 orifra=256×256，源视频即 256 特写 → 输出=头部区域，管线无缺陷，
2026-10-27 ffprobe+PIL 实证）。**合成层 A 方案**（uc3a）在此链 blend 后、UnityCapture 前
注入统一画布合成（R-eng11 注入点纪律：统一出帧出口 helper、专用画布缓冲、禁碰预加载
orifra——pristine 纪律 render_loop.py:757-763/825-829）。

**分辨率语义**（R-ceo9 硬输入）：
- 摄像头按首个角色段分辨率初始化（main.py:359-361）；
- `resolution_change_cb` 在**每次段切换**事务内触发（render_loop.py:278-283）→
  unity_capture.py:114-158 关闭→重开虚拟摄像头（OBS 侧=可感知重启抖动）；
- 分辨率失配帧被静默 resize 拉伸（unity_capture.py:194-196，仅 10s 限频告警）。
- 统一画布政策的必要性即源于此：全会话单画布，重建仅会话启动。

## 二、段管理与口型门控

- 段热装卸：`/segment/add`（幂等注册+惰性加载，已缓存段不重载——重处理刷新
  须先 remove+add；remove 拒当前渲染段 sequence_frame_pack.py:1148）、
  `/segment/remove`（拒当前段/调度中段）、`/segment/switch`（immediate 硬切 /
  segment_end 边界无缝——HTTP 立即返回、服务端边界执行）、`/segment/schedule`。
- **段完成感知（T0b 结论）**：`/status` 暴露 current_segment/frame_index/
  segment_sequence{position,sequence}/pending_switch/lip_mode（service.py:396-429,463）
  ——轮询式边界感知可行（编排 hold_for_song/前置位的正向先验）。
- 口型门控：`RealtimeService.set_lip_params('on'|'off')`（service.py:305）——全局单值
  （render_loop.lip_mode），逐帧生效双向热切；off=画外音直通+音频帧锁定照常出声。
  编排 lip 联动（T6）=性能层（R-eng5）：去抖+读回断言+边界轮询，失败 debug 日志
  （on→off 方向无害；off→on 失败=可见缺陷→低频告警）。
- 音频推送：`/audio/push_full`（整段，帧锁定播放，返回=单飞行句完成判定）、
  `/audio/push`（流式）。点歌锚点=push 受理时刻（R-eng1/F-M4：剩余时长通知编排）。

## 三、编排引擎与素材侧（AUTOlive）

- 规则模型（config_model.py）：RuleCfg（pred_version=legacy|new，R-eng3 谓词版本）、
  StepCfg（lip_flag/hold_for_song/pred_version）、TierCfg（计数档位覆盖）。
- new 语义：时长=素材时长（apply 二段从 sidecar 物化，未知→step_timeout_s 兜底+
  timeout_degrade 报警 R-eng6②）；mute 谓词=素材口型标志；接管=song 触发才接管；
  song 主线 hold_for_song（on_song_started 锚点+剩余时长，playback_manager push
  受理点接线）。
- legacy 语义：旧谓词 audio_mode==none/file 音轨维持至重存（零静默行为变化）；
  min_retrigger_s/min_sequence_hold_s 已退役（R-ceo5/uc2a，读取兼容+告警）。
- 素材元数据 sidecar（data/edtalk_materials_meta.json，R-eng2）：type/lip_flag/
  duration_s/fps/background/head_position/head_scale/composite/closeup/preview/conflict；
  红线=不写 edtalk/ 四件套数据格式。X3 迁移定型：规则步骤引用先行（跨类冲突标记）、
  未引用默认主线、存量特写回溯标记。

## 四、F3 调查报告：step_timeout_s「步骤超时上限」

**职能**：步骤推进保险丝——物化于 `seq["current_deadline"]`（monotonic），防
segment_end 等待悬挂卡死序列。双路径（choreographer.py）：
1. none 步（纯画面）：deadline=duration_s（播放时长上限）；
2. audio 步：deadline=duration_s（语义=完成上限，F1）或缺省=step_timeout_s；
3. 新增（本批）：new 规则素材时长未知→step_timeout_s 兜底+timeout_degrade 报警；
   song hold_for_song 主线段起步=step_timeout_s 保险丝（歌曲未起播的诚实降级），
   on_song_started 到达后覆盖延长。
**保险丝触发=timeout_degrade**：报警+强制推进（_complete_step）。
**结论**：保留（不退役）——它是所有「等待类」步的最终兜底；UI 已按本文案标注
（choreography.py 步骤超时上限 tooltip）。song hold 场景由 on_song_started 覆盖，
保险丝仅在歌曲真正未起播时诚实降级。

## 五、残留风险与观察项

- 合成层 A 方案实施时：edtalk/ realtime_serve 首次分叉（18 文件现与上游 md5 全同）
  ——补丁台账随首分叉建立（R-eng11）。
- 混合分辨率素材共存（E4）：统一画布政策落地前，避免同会话混用不同分辨率素材
  （逐段摄像头重建抖动）。
- legacy 谓词双轨：30 天退役期限（TODOS 2026-10-27 节 #2）。
