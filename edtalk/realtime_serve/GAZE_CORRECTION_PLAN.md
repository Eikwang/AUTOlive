<!-- /autoplan restore point: /c/Users/admin/.gstack/projects/Eikwang-EDTALK/main-autoplan-restore-20260902-212553.md -->
# 实时推理视线修正集成方案（Gaze Correction Integration）

状态: /autoplan 审查中 | 分支: main | 日期: 2026-09-02

## 1. 问题陈述

视线修正功能在实时管线中完全缺失（PIPELINE_REVIEW.md 问题 2），CLI 仅有一个 placeholder 开关（`--gaze-correction`）。
参考实现 `pytorch_gaze_redirection/src/inference.py` 是离线批处理设计，直接照搬会摧毁实时管线：

| 组件（inference.py） | 耗时特性 | 实时管线处置 |
|---|---|---|
| dlib 全帧人脸检测 + 68 点关键点（每帧） | 10-30ms，CPU | **完全删除**——预处理已提供逐帧 facemark（68 点，256×256 空间），`CharacterDataPack` 启动时已加载 |
| EAR 眨眼状态机（EAR_CLOSE=0.12 / EAR_OPEN=0.16 / 确认 3 帧 / 上限 26 帧） | 可忽略（纯数值） | **原样保留**——EAR 从 facemark 36-41 / 42-47 直接计算 |
| 眼睛中心 EMA 平滑（α=0.4，大位移降权 0.3） | 可忽略 | **原样保留** |
| eye_size 首帧锁定（1.5× 眼眶外包络） | 可忽略 | **保留**，段切换时 reset |
| 眼部裁剪 → PIL + torchvision transforms → 64×64 | 数 ms，PIL 慢 | **替换**：GPU 上直接 slice + `F.interpolate` + 归一化，零 CPU 往返 |
| `generator(batch, angles)` 推理（batch=2，64×64，约 20 层 conv） | 数 ms | **PTH + CUDA Graph**（与主 Generator 同待遇，一次 launch） |
| `skimage.match_histograms` 每帧 2 次 | 5-20ms/次，纯 CPU | **替换**：GPU 单通道 mean/std 颜色迁移（μ/σ 对齐），或先直接禁用观察色差 |
| 圆形 mask 混合贴回 | 数 ms CPU | **替换**：圆形 mask 预计算为 GPU 常量，blend 全程 GPU |

## 2. 管线位置（用户指定 + 架构验证）

```
facemark(68点, 256空间) ──┐
cropped 256×256 (GPU) ────┴─▶ GazeCorrector.correct()
                                   │  (EAR眨眼门控 + 双眼64×64推理 + GPU贴回)
                                   ▼
                          修正后 crop (GPU) ─▶ Audio2Lip/Lip ─▶ Generator ─▶ FaceRepair ─▶ Compositor
                              视线修正 → 口型推理 → 面部修复（用户指定顺序）
```

位置有效性验证：视线修正作用于 **喂给口型 Generator 的 count crop**。修正后的眼睛经 Generator 渲染；
compositor 的嘴部 mask 只贴回嘴区域，眼睛区域的修正结果自然保留在最终帧上。✓

## 3. 设计（GazeCorrector，realtime_serve/gaze_correction.py 新增）

```python
class GazeCorrector:
    """GPU 内联视线修正。眨眼帧自动跳过（EAR 状态机）。"""

    def __init__(self, ckpt_path, device='cuda', convergence_h=0.05, smooth_alpha=0.4):
        # Generator(style_dim=3)，加载 ckpt['current_generator']，eval
        # CUDA Graph 捕获：固定输入 [2, 6, 64, 64]（RGB3 + angles3 tiled）
        # 圆形 mask 常量（64×64，随 eye_size 在 GPU 上 resize）
        # 复刻原版状态：_prev_centers / _fixed_eye_size / blink 计数器

    def reset(self):
        """段切换时调用：清空平滑状态 + eye_size 锁定 + blink 状态。"""

    def correct(self, crop_gpu: torch.Tensor, facemark_256: np.ndarray) -> torch.Tensor:
        """输入输出均为 (3,256,256) RGB float [-1,1] GPU tensor。
        眨眼帧 / facemark NaN：直接返回原 crop（零开销路径）。"""
        # 1. EAR（facemark 36-47，numpy 标量计算 ~0.1ms）
        # 2. 眼中心 EMA 平滑 + eye_size 锁定
        # 3. GPU 裁剪：slice → F.interpolate(64) → 归一化 → stack [2,3,64,64]
        # 4. graph.replay() → 输出 [2,3,64,64]
        # 5. GPU 颜色迁移（μ/σ 对齐替代 match_histograms）
        # 6. GPU resize 回 eye_size + 圆形 mask blend 写回 crop tensor（in-place 或 copy）
```

关键不变式：
1. **输入输出同形状同设备**（3,256,256 RGB [-1,1] GPU）——render_loop 无感知地插在 infer_frame 之前。
2. **眨眼/异常帧零开销旁路**：EAR 高于阈值确认帧直接返回原 crop，不跑模型。
3. **全程 GPU**：crop/推理/贴回无一次 CPU 往返；EAR 与中心平滑用 numpy 标量（facemark 本来在 CPU）。

## 4. 模型格式判断（用户问题：能否转 ONNX）

**结论：可转，全部标准 ONNX op**（Conv / InstanceNorm / ReLU / ConvTranspose / sigmoid / mean-max-cat / tanh，
eval 模式 Dropout 为 no-op，`angles.expand` 在固定 64×64 输入下 trace 无歧义，固定输入 [2,6,64,64]）。

**裁决：仍推荐 PTH + CUDA Graph**（P5 显式优于聪明 / P4 复用）：
- models.py 已有完整的 Graph 捕获基建（主 Generator 刚验证过），gaze Generator 照搬 ~20 行；
- 零转换脚本、零精度验证负担、零新文件格式；
- ONNX 导出脚本作为 TODOS 后备项：仅当部署目标脱离 PyTorch 运行时才启用。

## 5. render_loop 集成（T2）

```python
# _process_frame()，Generator 推理之前
cropped_gpu = cropped  # (1,3,256,256) GPU，已含 normalize
if self.gaze_corrector is not None and facemark is not None:
    cropped_gpu = self.gaze_corrector.correct(cropped_gpu[0], facemark).unsqueeze(0)
rendered = self.model_manager.infer_frame(cropped_gpu, alpha_D_lip, cropped_gpu)
```

- `switch_segment()` → `gaze_corrector.reset()`；
- 主 Generator 的 CUDA Graph 输入形状不变（256×256 恒定）——不受影响；
- timing 直方图新增 `gaze` 阶段。

## 6. 配置与可观测性（T3/T4）

- `--gaze-correction` 升级为三态：`off`（默认）/ `on` / `adaptive`（复用 face_repair 的 `_repair_allowed()` 语义：buffer 深度 ≥ REPAIR_MIN_BUFFER 才启用）；
- `/status` 增加 `gaze_state: off|on|adaptive|blink_skip`；
- RUNBOOK.md 增补：参数行 + 眨眼跳过说明 + ONNX 备选路径说明。

## 7. 性能预算（验收依据）

当前产能富余（log13 后续 CUDA Graph 运行）：gen 8.9 + repair 7.2 + comp 0.3 + put 背压 20 = total 39.7ms
→ 实际生产 ≈ 19.7ms/帧 ≈ **50fps 产能**。

| 新增项 | 预算 |
|---|---|
| EAR + 中心平滑（CPU numpy） | ~0.2ms |
| GPU crop + interpolate + normalize | ~0.5ms |
| gaze Generator graph replay（64×64×2） | ~1-2ms |
| GPU 颜色迁移 + mask blend | ~0.5ms |
| **合计** | **~2-4ms/帧** |

预算后产能 ≈ 40fps，仍远超 25fps 输出线。验收指标：`--gaze-correction on` 下 FPS 25 稳定、buffer 蓄满、
`gaze` p99 < 10ms、眨眼帧不修正（日志计数）。

## 8. 已知代价与风险

- **色差风险**：match_histograms 被替换为 μ/σ 迁移，生成眼睛与眼周色域可能出现轻微差异。缓解：先 on 模式目检；
  若不可接受再评估 GPU 直方图匹配（复杂度高，暂不入 scope）。
- **facemark 质量依赖**：闭眼/侧脸帧 facemark 可能漂移 → EAR 误判。缓解：复用原版状态机的滞回 + 上限帧数，
  误判最坏后果 = 该帧不修正（安全方向）。
- **CUDA Graph 捕获失败**：回退 eager（复用主 Generator 的 fallback 模式），gaze 推理变慢但不崩。

## 9. 明确不在本次范围

- gaze Generator 的 ONNX 导出脚本（可转已验证，仅在脱离 PyTorch 部署时启用）→ TODOS
- GPU 直方图匹配（μ/σ 迁移不达标时的后备）→ TODOS
- 主 Generator 重训练 / 头部姿态修正（正交）

## 10. 复用清单（What already exists）

| 需求 | 已有资产 |
|---|---|
| 逐帧 68 点 landmarks（256 空间） | CharacterDataPack._segment_cache['facemark']（启动加载） |
| CUDA Graph 捕获/回退模式 | models.py._capture_generator_graph（照搬） |
| adaptive 门控语义 | render_loop._repair_allowed()（同模式新增） |
| 三态 CLI 模式 | --face-repair on/adaptive 解析（同模式） |
| timing 直方图 | render_loop._timings（加 gaze 键） |
| 眨眼状态机参数 | inference.py 常量（原样复刻） |

---

# CEO 审查（Phase 1）— 模式: SELECTIVE EXPANSION（autoplan override）

## 系统审计

- 连续 8+ perf/fix 提交集中在渲染热路径后刚达成 25fps 里程碑（PERFORMANCE_REPORT.md）——本方案在**刚稳定的管线上加新 GPU 环节**，按回顾性检查原则从严审查。
- 无 stash、无 TODO/FIXME 遗留、无历史 learnings 冲突。
- 设计文档：无（延续性工程决策，repo 内已有 OUTPUT_BUFFER_PLAN 模式可循）。

## 0A. 前提挑战

| # | 前提 | 评估 | 结论 |
|---|------|------|------|
| P1 | facemark 预处理逐帧可用 | composite.py:136 已有 NaN 检查 → 确实存在 NaN 帧；方案已设计零开销旁路 | 接受，附条件（NaN→旁路） |
| P2 | 眼睛在 256×256 crop 内 | count crop 即头部裁剪，眼睛必然在内 | 接受 |
| P3 | gaze 模型在 count crop 域上有效 | 原版用于原始全帧；crop 域颜色一致但分辨率分布未经目检验证 | **接受，附验收条件**：on 模式目检为硬验收项 |
| P4 | μ/σ 迁移可替代 match_histograms | 一阶二阶矩对齐 vs 全分布对齐；GAN 输出分布接近原图时通常够用 | 接受（taste），fallback 已列 |
| P5 | +2-4ms 预算 | 基于实测 50fps 产能（put 背压 20ms 为证） | 接受 |
| P6 | 管线位置 gaze→lip→repair | 用户指定；架构上有效（compositor 只贴嘴，眼睛保留） | 接受。**附风险标注**：若 Generator 重绘稀释修正效果，correct() 的输入输出契约允许 5 分钟内迁移到 rendered 之后（输出端），验收目检时裁决 |

"什么都不做"的代价：视线漂移问题持续存在（用户明确要求此功能，PIPELINE_REVIEW 问题 2 的既定欠账）。真实需求，非假设。继续。

## 0B. 现有代码杠杆

见方案 §10 复用清单。全部复用，无重复建设。补充关键架构约束（主审发现）：

**[high] import 边界**：`gaze_correction.py` 必须只 `from pytorch_gaze_redirection.src.archs import Generator`，
**禁止** import `pytorch_gaze_redirection.src.inference`——后者顶层拉起 dlib/skimage/PIL（inference.py:6-12），
会把 ~50MB dlib 模型与无用依赖带进服务进程。方案 §3 未明说 → 并入任务 T1。

## 0C. 梦想状态映射

```
CURRENT STATE                      THIS PLAN                        12-MONTH IDEAL
25fps 稳定；gaze 仅 placeholder --> gaze→gen→repair 全GPU链路 ---> 全头部驱动（gaze+head pose
管线 gen8.9+repair7.2ms           眨眼感知+CUDA Graph+adaptive      +blink全时序）；区域增强模型
                                  门控                              插件化统一管理；TensorRT 全图化
```

方向判断：**靠近理想态**。本方案建立"区域级 GPU 增强模型"的集成模式（gaze 是第一个插件），
后续同类模型（眉毛、表情细化）可复制该模式，是平台化的一步。

## 0C-bis. 实现备选方案

```
APPROACH A: PTH + CUDA Graph 全 GPU 内联（方案主推）★
  Summary: GazeCorrector 持有 PTH 模型，Graph 捕获一次 launch，全链路 GPU tensor
  Effort:  S (human ~6h / CC ~30min)   Risk: 低（带回退）
  Pros:    复用刚验证的 Graph 基建；推理最快（单 launch）；零转换脚本与精度验证
  Cons:    与 face_repair 的 ORT 模式并存（两套推理栈）
  Reuses:  models.py._capture_generator_graph 模式、facemark 数据、adaptive 门控
  Completeness: 9/10

APPROACH B: ONNX 导出 + ORT IOBinding（照搬 face_repair 模式）
  Summary: 导出 onnx，IOBinding 零拷贝推理，与 face_repair 同栈
  Effort:  S-M (human ~1d / CC ~45min)   Risk: 低-中（转换精度验证负担）
  Pros:    推理栈统一（与 face_repair 一致）；部署脱离 PyTorch 的可能
  Cons:    +1 转换脚本 +1 权重文件 +1 精度验证步骤；ORT eager 逐层 launch 慢于 graph
  Reuses:  face_repair IOBinding 模式
  Completeness: 8/10

APPROACH C: 直接调用原 GazeRedirectInference
  Summary: 复用离线类，dlib 全帧检测照跑
  Effort:  XS   Risk: 高（30+ms/帧直接摧毁实时管线）
  排除：与 25fps 目标根本冲突
```

**裁决（P1+P3）：选 A。** A 的 eager fallback 已内嵌（Graph 捕获失败自动降级），
严格覆盖 B 的所有场景且推理更快；B 的唯一优势（脱离 PyTorch 部署）不在需求域 → TODOS。

## 0D. SELECTIVE EXPANSION 分析

复杂度检查：1 新文件 + 4 修改 + 1-2 测试 = 6-7 文件 → 低于 8 文件阈值。通过。

最小改动集：GazeCorrector + render_loop 接线 + CLI 三态。三者缺一不成立。

扩展候选（autoplan 自动裁决）：
| # | 提案 | 裁决 | 理由 |
|---|------|------|------|
| E2 | /status 增加 blink/修正率计数 | 采纳 | 可观测性是 scope（Prime Directive 5），S 工量 |
| E3 | ONNX 导出脚本 | TODOS(P2) | 方案已列，部署需求未出现 |
| E4 | GPU 直方图匹配 | TODOS(P3) | μ/σ 不达标的 fallback，需运行数据 |
| E5 | 启动日志回显 gaze 配置（delight） | 采纳 | S 工量，半径内 |

## 0E. 时序审讯

- HOUR 1: crop tensor 已是 [-1,1] RGB——原版 transform（Resize+ToTensor+Normalize）语义等价于
  直接 `F.interpolate(crop, 64)`，**省一次归一化**，且通道序一致（sequence_frame_pack RGB ✓）。
- HOUR 2-3: **[critical] in-place 陷阱**——`get_crop()` 返回预加载 GPU batch 的**切片视图**
  （sequence_frame_pack.py:211 `batch[offset:offset+1]`）。correct() 若 in-place 写回会污染
  预加载缓冲，帧循环回绕时该帧被二次污染。**必须输出新 tensor**（clone 或 blend 产生新分配）。
  方案 §3 的"in-place 或 copy"二义性 → 定死：copy。
- HOUR 4-5: 意外——原版 PIL resize 自带 antialias，`F.interpolate` 需显式 `antialias=True`
  （bilinear 降采样无 antialias 会产生混叠）。64px 眼部图对混叠敏感。
- HOUR 6+: 测试——mock generator + 合成 facemark 序列；眨眼窗口断言（修正跳过帧数 == 预期）；
  形状/设备/数值域不变式；**缓冲不污染测试**（correct 前后原 batch 数值逐位相等）。

## 0F. 模式确认

SELECTIVE EXPANSION（autoplan override），方案 A 落位。E2/E5 并入 scope，E3/E4 → TODOS。

## 11 节深度审查（主审）

### S1 架构审查

```
改造后依赖图:
CharacterDataPack ─facemark─▶ GazeCorrector ─corrected_crop─▶ RenderLoop._process_frame
pytorch_gaze_redirection/src/archs.py (仅模型定义, torch-only) ─▶ GazeCorrector
RenderLoop ─switch_segment─▶ GazeCorrector.reset()
main.py ─装配─▶ GazeCorrector (CLI 三态门控)
```

- 耦合变化：realtime_serve → pytorch_gaze_redirection/src/archs（新增，torch-only，干净）。
  裁决：同 repo 模型定义复用优于复制网络代码（DRY）。import 边界见 0B [high]。
- SPOF：correct() 每帧异常会杀死渲染循环。修复：try/except 旁路 + 错误计数 + 日志（并入 T1）。
- 回滚姿态：--gaze-correction off 即回滚（默认即 off），git revert 单提交。二类门。
- 扩展性：区域增强模型模式（0C）；10x 负载（多路推流）时 gaze 与主 Generator 争 GPU——
  当前单路场景无此问题，写入 12 个月理想态说明。

### S2 错误与救援映射

```
METHOD/CODEPATH            | 可出错场景                  | 异常类
---------------------------|-----------------------------|------------------
GazeCorrector.correct      | facemark None/NaN           | (无异常,旁路)
GazeCorrector.correct      | 模型前向异常/显存不足        | RuntimeError
GazeCorrector.correct      | 坐标越界（facemark 漂移）    | (无异常,clamp+旁路)
GazeCorrector.__init__     | ckpt 缺失/损坏              | FileNotFoundError
_capture graph             | Graph 不支持/驱动问题        | RuntimeError
```

```
异常                  | 救援? | 动作                     | 用户所见
----------------------|-------|--------------------------|------------------
facemark NaN/None     | Y     | 返回原 crop，计数 blink_skip | 无（该帧不修正）
模型前向异常          | Y(新) | try/except→原 crop+计数+日志 | 无（画面正常，修正暂停）
坐标越界              | Y     | clamp 至 crop 边界         | 无
ckpt 缺失             | Y     | main.py 启动失败 fail-fast | 启动报错（显式开启的功能，合理）
graph capture 失败    | Y     | 回退 eager，日志标注       | 无（略慢）
```
无 catch-all 新增（correct 的 try/except 是"降级到原 crop"的有意设计，非吞异常——带计数与日志）。
无 CRITICAL GAP。

### S3 安全与威胁模型

无新端点、无新外部输入（CLI 三态由 argparse 约束）、无密钥、无依赖新增（torch/torchvision 已有）。
ckpt 本地文件与现有 checkpoint 同域。无注入面。无发现。

### S4 数据流与边界用例

| 交互 | 边界用例 | 处理 |
|------|----------|------|
| 每帧 correct | facemark NaN / None | 旁路返回原 crop |
| 每帧 correct | 持续闭眼 >26 帧 | 状态机超时强制恢复修正（原版语义照搬） |
| 每帧 correct | 眼坐标越界 | clamp + 防御 |
| 段切换 | reset 状态（eye_size 重锁定） | T2 接线 |
| 启动 | ckpt 缺失 | fail-fast（显式开启时） |
| Graph | capture 失败 | eager fallback |

### S5 代码质量

命名 GazeCorrector/correct 直述职责。状态机常量复刻（不 import inference，因 dlib 依赖——合理例外）。
无圈复杂度超 5 的新方法预计无。无发现（除 0B import 边界与 0E in-place 陷阱，均已并入任务）。

### S6 测试图

```
新代码路径:
  EAR/眨眼状态机        : 合成 facemark 序列（睁→闭→睁），断言跳过窗口与恢复 → 单测
  中心平滑/eye_size 锁定 : 数值断言 → 单测
  correct() 不变式      : 形状/设备/数值域[-1,1] → 单测
  NaN 旁路              : 不跑模型（mock 计数）→ 单测
  graph capture 回退    : 模拟异常 → eager → 单测
  缓冲不污染            : correct 前后原 batch 逐位相等 → 单测 ★关键
  render_loop 接线      : gaze→gen 顺序与 reset 接线 → 更新 test_t5
```
凌晨 2 点放心发布的测试：眨眼窗口 + 缓冲不污染。敌意 QA：连续 NaN、facemark 全零（eye_size=0）、
blink 边界 26/27。混沌：随机 NaN 注入。金字塔：多单测、1 集成、无 E2E。

### S7 性能

预算 2-4ms（方案 §7），显存增量 ~20MB（小模型 + graph pool）。put 背压 20ms 证明产能余量充足。
无 N+1 类问题（无 DB）。无新发现。

### S8 可观测性

timing `gaze` 阶段、`/status` gaze_state + blink_skip 计数 + 修正率（E2）、RUNBOOK 条目（T4）、
启动回显（E5）。运行手册：持续 blink_skip=100%（facemark 全坏）→ 检查预处理数据。无发现。

### S9 部署与回滚

默认 off 零风险上线；CLI 即开关；回滚 = off 或 git revert。验证清单：启动日志（capture 状态）→
on 目检（P3 硬验收）→ adaptive 压测 → /status 字段齐全。无风险窗口（单实例）。

### S10 长期轨迹

可逆性 5/5（纯新增 + 开关）。技术债：+1 小项（μ/σ 色差风险，有 fallback 路径与触发条件）。
路径依赖：**正向**——建立区域增强模型插件模式。12 个月新工程师读架构图即懂。无发现。

### S11 设计与 UX — 跳过（无 UI 范围）

## NOT in scope（含理由）

- ONNX 导出脚本：可转已验证（archs.py 全标准 op），仅在脱离 PyTorch 部署时启用 → TODOS(P2)
- GPU 直方图匹配：μ/σ 迁移不达标时的后备 → TODOS(P3)
- 主 Generator 重训练 / 头部姿态修正：正交特性
- dlib 检测路径的任何复用：与实时管线根本冲突（10-30ms CPU）

## What already exists（复用清单）

见方案 §10 + 0B。关键三项：facemark 逐帧数据（省掉 dlib 全帧检测——本方案最大的性能杠杆）、
CUDA Graph 捕获/回退模式（照搬主 Generator）、三态 CLI/adaptive 门控语义（照搬 face_repair）。

## Dream state delta

本方案落地后：区域级 GPU 增强模型的集成模式建立（第一个插件），25fps 基础上加 ~3ms 增强预算仍余量 40fps 产能。
距 12 个月理想态剩余：head pose / blink 全时序驱动、模型插件统一管理框架、TensorRT 全图化——路径清晰无架构障碍。

## Failure Modes Registry

```
CODEPATH              | FAILURE MODE          | RESCUED? | TEST? | USER SEES?        | LOGGED?
----------------------|-----------------------|----------|-------|-------------------|--------
correct() 模型前向     | 显存/驱动异常          | Y(旁路)  | Y     | 该帧不修正        | Y+计数
facemark NaN/None     | 预处理数据缺失         | Y(旁路)  | Y     | 该帧不修正        | Y(blink_skip)
facemark 漂移          | 坐标越界              | Y(clamp) | Y     | 轻微错位(最坏)    | N(无害)
graph capture          | 驱动不支持             | Y(eager) | Y     | 无                | Y
ckpt 加载              | 文件缺失/损坏          | Y(fail-fast) | N/A | 启动报错        | Y
μ/σ 色差               | 生成眼色域偏移         | 部分(目检)| Y(目检) | 可感知色差      | N/A
```
CRITICAL GAP: 0

## Implementation Tasks（CEO 阶段）

- [ ] **T1 (P1, human: ~6h / CC: ~30min)** — gaze_correction — 新建 GazeCorrector
  - Surfaced by: 0C-bis 方案A / 0B import边界 / 0E in-place陷阱 / S2 SPOF
  - Files: realtime_serve/gaze_correction.py（新增）
  - 要点: 只 import src.archs（禁 inference）；correct() 输出新 tensor（禁 in-place，缓冲不污染）；
    F.interpolate(antialias=True)；try/except 旁路+计数；graph capture + eager fallback
  - Verify: pytest tests/test_gaze_correction.py
- [ ] **T2 (P1, human: ~2h / CC: ~10min)** — render_loop — 管线接线
  - Surfaced by: §5 / S1 依赖图 / 0E 段切换
  - Files: realtime_serve/render_loop.py
  - 要点: gen 前插入 correct(cropped[0], facemark)；switch_segment → reset()；
    timing 直方图加 gaze 键；try 旁路不影响 gen
  - Verify: pytest tests/test_t5_render_loop.py
- [ ] **T3 (P2, human: ~1h / CC: ~5min)** — main/config — CLI 三态 + 装配
  - Surfaced by: §6 / S9
  - Files: realtime_serve/main.py, config.py
  - 要点: --gaze-correction off/on/adaptive（复用 face_repair 解析模式）；adaptive 复用 REPAIR_MIN_BUFFER 门控；
    启动回显（E5）；fail-fast 校验 ckpt 存在
  - Verify: 启动冒烟 --help + off 默认行为不变
- [ ] **T4 (P2, human: ~1h / CC: ~5min)** — api — /status 扩展
  - Surfaced by: §6 / S8 / E2
  - Files: realtime_serve/api/app.py
  - Verify: GET /status 字段断言
- [ ] **T5 (P1, human: ~3h / CC: ~15min)** — tests — 单测 + 集成
  - Surfaced by: S6 测试图
  - Files: tests/test_gaze_correction.py（新增）, tests/test_t5_render_loop.py（更新）
  - 要点: 眨眼状态机序列断言、缓冲不污染（★）、不变式、旁路、graph 回退、RESET 接线
  - Verify: pytest tests/test_gaze_correction.py tests/test_t5_render_loop.py
- [ ] **T6 (P2, human: ~1h / CC: ~5min)** — RUNBOOK — 文档
  - Surfaced by: §6 / S8
  - Files: realtime_serve/RUNBOOK.md
  - Verify: 人工检查

## CEO 完成总结

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY (CEO)             |
+====================================================================+
| Mode selected        | SELECTIVE EXPANSION（autoplan override）      |
| System Audit         | 25fps 刚达成，热路径加环节，从严审查          |
| Step 0               | 方案A(PTH+CUDA Graph); E2/E5并入,E3/E4→TODOS  |
| Section 1  (Arch)    | 2 issues（import边界/SPOF旁路）— 已并入任务    |
| Section 2  (Errors)  | 5 error paths mapped, 0 GAPS                  |
| Section 3  (Security)| 0 issues                                      |
| Section 4  (Data/UX) | 6 edge cases mapped, 0 unhandled              |
| Section 5  (Quality) | 1 issue (in-place 陷阱→定死 copy)             |
| Section 6  (Tests)   | 图已产出, 0 gaps（缓冲不污染★）               |
| Section 7  (Perf)    | 0 issues（+2-4ms 预算成立）                   |
| Section 8  (Observ)  | 4 观测点补齐                                  |
| Section 9  (Deploy)  | 0 risks（默认off，CLI即开关）                 |
| Section 10 (Future)  | Reversibility: 5/5, debt items: 1(有fallback) |
| Section 11 (Design)  | SKIPPED（无UI范围）                           |
+--------------------------------------------------------------------+
| NOT in scope         | written (4 items)                             |
| What already exists  | written                                       |
| Dream state delta    | written                                       |
| Error/rescue registry| 5 methods, 0 CRITICAL GAPS                    |
| Failure modes        | 6 total, 0 CRITICAL GAPS                      |
| TODOS.md updates     | 2 items (E3/E4)                               |
| Scope proposals      | 4 proposed, 2 accepted (SEL)                  |
| CEO plan             | 本文件即计划                                  |
| Outside voice        | subagent-only（Codex auth 不可用）            |
| Lake Score           | 3/3 选项选了完整方案                          |
| Diagrams produced    | 3（架构/数据流/测试图）                       |
| Stale diagrams found | 0                                             |
| Unresolved decisions | 0（taste 项留最终门）                         |
+====================================================================+
```

**UNRESOLVED DECISIONS:**（无——两个 taste 级风险移交最终门：
① P3 gaze 模型在 crop 域的目检验收；② P6 若 Generator 稀释修正效果则迁移到输出端）

## CEO 双声部（Step 0.5）

CODEX 不可用（auth missing）→ [subagent-only]。

### CLAUDE SUBAGENT（CEO — 战略独立挑战）核心发现

1. **[CRITICAL] 方案 §2 的"管线位置验证 ✓"是错的——修正后的眼睛会被 compositor 完全丢弃**。
   代码事实：`compositor.blend()`（158-174 行）`blended = rendered*mask + ori*(1-mask)`；
   `_create_mouth_mask()` 内边界=嘴部 hull（48-59）→ alpha=1.0，外边界=下颌+鼻翼（0-16, 31-35）
   且显式约束"不超过鼻翼线"。**眼睛（36-47）在鼻翼上方 → mask 恒为 0 → 最终帧眼睛 =
   orifra 预录制原帧的眼睛，Generator 输出的眼睛整体被丢弃**。推论：按此方案实现后功能静默失效；
   且现有 face_repair 修复的眼睛同样不可见（同一合成架构）。
   **主审复核：成立。** 主审 0A-P6 的"只贴嘴所以眼睛保留"方向写反——正确表述是"只贴嘴所以眼睛
   保留 **orifra 原样**"。0A P6 结论作废，重新裁决（见下方 USER CHALLENGE）。
   更坏的推论：方案 §7 的全部验收指标（FPS/buffer/p99/眨眼计数）没有一个验证修正是否真的出现在画面里。

2. **[CRITICAL] 漏掉离线预处理替代方案——count/orifra 是预录制静态资产，收敛角是常量**。
   预处理阶段对每段 count 一次性跑 gaze 修正（inference.py 的 process_image_sequence 直接可用）：
   实时管线零改动零 GPU 开销、保留 match_histograms 与已验证 dlib+EAR 路径、入库前可人工目检。
   成本 = 预处理时长 +10%。方案 T1-T4（GazeCorrector/CUDA Graph/GPU 颜色迁移/三态 CLI）全部不需要。
   唯一需要实时的理由是"动态 gaze"（按会话改变注视方向）——但 angles 是常量 [-0.05,0,-1]/[0.05,0,1]，
   该需求当前不存在。
   **主审复核：成立。** 方案通篇未论证"为什么必须实时"。

3. **[HIGH] "facemark 每帧可用"在本机（Windows SAFE_MODE）默认不成立**：run_preprocess.py:194
   Windows 安全模式强制 `--no-landmark` → facemark 全 NaN → gaze 全帧旁路零效果，且
   `gaze_state` 枚举没有 nan_skip/无数据状态，外部无法发现失效。集成片段 `facemark is not None`
   是死代码（get_frame 直接索引 numpy 永不为 None，真正要查的是 np.isnan）。
   **主审复核：成立**——主审 0A-P1 只查了 NaN 检查存在，未查 SAFE_MODE 下全 NaN 的默认路径。

4. **[HIGH] EAR 状态机跨域迁移未验证**：阈值 0.12/0.16 是 1080p dlib 域上调的；256 空间眼宽仅
   15-25px，关键点噪声大 → 单帧 EAR 抖动与眨眼同量级 → 修正/跳过帧交替**闪烁**（比不修正更糟）。

5. **[HIGH] in-place 陷阱**：与主审 0E 发现一致（get_crop 视图 + 循环回绕二次修正漂移），并补强：
   常量收敛角反复叠加 → 输出漂出训练域、色差累积。两声部独立命中，高置信。

6. **[HIGH] gaze 与 face_repair 抢同一份产能，联合预算未分析**：repair adaptive 门控 buffer≥6，
   gaze +2-4ms 会降低 repair 触发率——两个画质功能此消彼长且无日志可见。另：主审 §7 基线引用错文件
   （log13 是 Graph 前旧数据；8.9/7.2/39.7 出自 PERFORMANCE_REPORT.md 里程碑段）——已修正认知。

7. **[MEDIUM] μ/σ 迁移误差被低估**：眼部是亮巩膜/暗皮肤双峰分布，线性 μ/σ 与直方图匹配差异最大处
   恰在此。先 30 行脚本对比两种迁移的可见差异再决定（若采纳发现 2，问题整个消失）。

8. **[MEDIUM] ONNX"可转"结论正确但采纳发现 2 后无关紧要**；实时方案下 Graph 只包 Generator 前向，
   slice/interpolate/blend 仍 eager 逐 kernel，2-4ms 预算偏乐观，应按 eager/ graph 两档实测。

9. **[MEDIUM] 问题本身零用户证据**：需求来源是内部 review 的 TODO 占位，不是用户反馈。未回答：
   资产库多大比例视频真有视线问题（HDTF 类多看镜头，2.9° 收敛修正不可见）？只修 gaze 不修 head pose
   的"头偏眼看镜头"分离感可能比原样更怪？本仓库无任何 gaze 输出质量证据。
   → 先用离线实现对 3-5 个代表资产出对比视频目检打分。

10. **[MEDIUM] blink 门控语义被静默改动**：原版检测不到人脸时沿用上一帧眼中心继续修正
    （inference.py:179-184），方案对 NaN 直接旁路——两种失效态行为不同且未说明。

### CEO USER CHALLENGE（两声部一致 → 移交最终门，永不自动裁决）

**Challenge 1: 注入点与实施路线**
- **用户说**：视线修正集成到实时推理流程，位于口型推理之前（视线修正→口型推理→面部修复）。
- **两声部一致推荐**：当前注入点（Generator 输入端）技术上无效——compositor 的嘴部 mask 使
  眼睛区域恒为 0，修正不会出现在输出帧（发现 1，代码可验证）。可行路线二选一：
  - **路线 A（subagent 首推）**：离线预处理修正 count 资产（发现 2）——实时零改动，省 ~80% 工程量，
    保留已验证路径，入库前目检；代价：无动态 gaze 能力（当前 angles 是常量，需求不存在）。
  - **路线 B（实时，修正注入点）**：GazeCorrector 挂在 **compositor 之后**——用 metadata crop 坐标
    把 256 空间 facemark 映射回 1080p 全帧，在最终帧眼睛区域裁剪→64×64 推理→贴回
    （原版 _paste_eye 的正确实时移植）。成本 ~5-10ms/帧（含全帧 slice/copy），预算内但比原方案贵。
- **为什么挑战**：发现 1 是代码可验证的功能静默失效，不是偏好。
- **可能缺失的上下文**：用户是否有"动态 gaze"规划（按会话/交互改变注视方向）——若有，实时路线 B
  是必要地基；若无，路线 A 完全够用且更稳。
- **若我们错了的代价**：选 A 后发现需要动态 gaze → 重做实时集成（方案 B 的设计已在案）；选 B 后发现
  用户只想要静态修正 → 多付了实时工程与每帧 GPU 成本。
- **无论选哪条**：发现 3（SAFE_MODE 全 NaN / 死守卫）、4（EAR 跨域闪烁）、5（in-place）、
  6（联合产能）、9（离线目检先行）作为修正并入任何路线。

### CEO DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                   否(P6错) N/A   DISAGREE→门
  2. Right problem to solve?           部分     N/A   DISAGREE→门
  3. Scope calibration correct?        否       N/A   DISAGREE→门
  4. Alternatives sufficiently explored? 否     N/A   DISAGREE→门
  5. Quality risks covered?            部分     N/A   部分确认
  6. 6-month trajectory sound?         否       N/A   DISAGREE→门
═══════════════════════════════════════════════════════════════
```
Codex 不可用 → 单声部。声部 10 项发现中主审复核确认 6 项成立（1/2/3/4/5/6），
4 项部分成立（7/8/9/10）→ 全部作为方案修正或 USER CHALLENGE 处置。

## 方案修正（声部确认的技术缺陷，任何路线都需要）

| # | 修正 | 内容 |
|---|------|------|
| G1 | 注入点裁决 | USER CHALLENGE 1：路线 A（离线预处理）或路线 B（compositor 后实时）由用户在最终门裁决 |
| G2 | NaN 守卫修正 | `facemark is not None` 死守卫改为 correct() 内 `np.isnan(facemark).any()` 检查；/status 增加 nan_skip 计数与 gaze_state='no_data' 态 |
| G3 | 启动资产校验 | 启动时统计 facemark NaN 率，超阈值（如 >50%）则 gaze 强制 off + WARN 日志 |
| G4 | in-place 定死 | correct() 输出新 tensor（clone/copy），回归测试：同一帧两次渲染的输入逐位一致 |
| G5 | 联合预算实测 | gaze on + repair adaptive 联合实测（repair 触发率、buffer 分布）；性能基线引用修正为 PERFORMANCE_REPORT.md 里程碑段 |
| G6 | EAR 域适配 | 实时路线下先离线统计 256 空间 EAR 分布再定阈值；离线路线下问题消失（1080p dlib 同域） |
| G7 | 色差预验证 | 实时路线下先 30 行脚本对比 μ/σ vs 直方图匹配可见差异；离线路线下保留 skimage |
| G8 | 质量前置验证 | 任何路线：先对 3-5 个代表资产出对比视频目检打分（发现 9） |

---

# DX 审查（Phase 2.5）— 模式: DX POLISH

产品类型：本机运营的推理服务（操作者=开发者本人）。Persona：solo operator，CLI 启动、经虚拟摄像头供 Unity 消费。
（与 OUTPUT_BUFFER_PLAN 审查同一 persona，保持一致性。）

## 开发者旅程图

```
发现→评估→安装→hello world→集成→调试→升级→扩展→迁移
 ✓   ✓   ✓   ⚠见维度1    ⚠新增  ⚠flag语义  N/A  N/A  N/A
```

## 8 维评分（0-10，POLISH 模式：逐项修至达标）

| 维度 | 分 | 证据与差距 | 修复 |
|------|----|-----------|------|
| 1 Getting started | 6 | 默认 off 行为不变 ✓；但 **Windows SAFE_MODE 资产 facemark 全 NaN**（CEO 发现 3）→ 开了 on 也"静默零效果"，hello world 永远到不了 | G3 启动 NaN 率校验 + WARN；RUNBOOK 写明资产前置条件 |
| 2 CLI ergonomics | 8 | 旧 `--gaze-correction` 是 boolean store_true，三态化后**裸 flag 调用报错**（breaking）；与 --face-repair 命名模式一致性 ✓ | F1: nargs='?' const='on'——裸 flag=on 向后兼容，带值 on/adaptive |
| 3 Error messages | 5 | ckpt 缺失 fail-fast 无三段式；blink/nan 跳过风暴无对策指引 | F2 + T6 runbook 条目 |
| 4 Docs | 6 | RUNBOOK 缺 gaze 条目；两条路线（离线/实时后置）的说明均未写 | T6 |
| 5 Escape hatches | 9 | off/on/adaptive 全覆盖；off 精确恢复今日行为 | 达标 |
| 6 Credible | 8 | 行为可预测；跳过有计数 | 达标 |
| 7 Findable | 7 | /status 缺 no_data 态（CEO G2） | F3 |
| 8 Valuable | 8 | 前提=修正真的出现在画面（USER CHALLENGE 1 裁决后有效）；否则本维=0 | 依赖门裁决 |

**DX Scorecard 总分: 7.25/10 → 修复后 8.5/10**（F1-F3 + G3 落地后）
**TTHW: ~2-3min（模型加载地板，不变）；但"到可见效果的 hello world"依赖资产 facemark 质量——G3 把"静默失望"变成"明确告知"。**

## 路线差异 DX 考量（供最终门裁决参考）

- **路线 A（离线预处理）**：DX 对象变为预处理管线（run_preprocess.py 新步骤 + 目检流程）。
  无运行时状态、无 flag 语义变更、TTHW=预处理时长 +10%；错误路径=检测失败率报告。整体 DX 显著更简单。
- **路线 B（实时 compositor 后）**：上表评分适用，另需全帧注入的 timing 观测（新增 fullframe 阶段计时）。

## DX 修正（并入方案）

| # | 修正 | 内容 |
|---|------|------|
| F1 | flag 兼容 | `--gaze-correction` 用 `nargs='?' const='on'`：裸 flag=on（旧调用不报错），带值 on/adaptive |
| F2 | 报错规格 | ckpt 缺失=问题+原因+对策三段式（"weights/current_model.ckpt 不存在——--gaze-correction 所需模型；检查预处理输出或保持默认 off"） |
| F3 | /status 完整态 | gaze_state 枚举 = off \| no_data \| blink_skip \| on \| adaptive（合并 CEO G2/G3） |

## DX 双声部（Step 0.5）

CODEX 不可用 → [subagent-only]。

### CLAUDE SUBAGENT（DX — 独立体验挑战）核心发现（11 项）

1. **[CRITICAL] 路线未裁决但全部操作者交付物按实时路线写死**：T3/T4/T6、§6 状态语义、§7 验收数字、
   S2 错误映射未按路线条件化。选 A 则三态 CLI/五态枚举/blink_skip 全成死规格；选 B 则 §7 预算与验收
   数字失效。→ 修复：两条路线的操作面都写成规格；任务标注路线条件。
2. **[CRITICAL] 裸 `--gaze-correction` 从"静默 no-op"变成"启动失败"**：已验证 `config.gaze_correction_enabled`
   全仓库无消费者（grep 仅定义+赋值）——今天裸 flag 被接受但什么也不做；三态化后裸 flag 触发
   argparse 错误拒启，已有启动脚本全部失效。→ 修复：`nargs='?' const='on' default='off'`
   choices=['off','on','adaptive']——裸 flag=on 向后兼容，显式 off 合法。
3. **[HIGH] flag 语法三处互相矛盾**：§6 三态 / T3"复用 face_repair"（实际 face_repair 是
   choices=[on,adaptive]+独立 --no-face-repair，没有 off 值）/ F1 的 nargs 语法。→ 写死一处。
4. **[HIGH] ckpt 无 CLI 覆盖 + 两份字节级相同同名文件**（pytorch_gaze_redirection\log\ 与
   realtime_serve\weights\ 的 current_model.ckpt，228168211 字节同 mtime）——重训后旧权重静默加载。
   且 S2 的"FileNotFoundError"断言错误：损坏抛 torch 反序列化运行时异常。→ `--gaze-ckpt PATH` +
   默认改名 gaze_generator.ckpt + 键校验（缺 current_generator 报"这不是 gaze 模型权重"）。
5. **[HIGH] repair 被挤占不可观测**：/status 无 repair 字段；gaze 复用 repair 的阈值常量 6 无独立旋钮。
   → /status 加 repair_skip_count；`--gaze-min-buffer`；RUNBOOK 写明交互。
6. **[HIGH] gaze_state 枚举三处矛盾 + 配置态与瞬态混杂**：blink_skip 是逐帧瞬态会持续抖动；adaptive
   门控关闭期与"正在修正"不可区分；F2 示例报错文本双重错误（文件存在/预处理产物非权重）。
   → gaze_state 只表达配置模式（off/on/adaptive/forced_off），活动性用独立计数
   （corrected_frames/blink_skips/nan_skips/gate_skips）。
7. **[MEDIUM] 错误文本缺三段式；blink 风暴排查指引会误诊**（EAR 域失配时会引向检查数据）。→
   错误文本表 + 按计数分流诊断（nan_skips 高→资产；资产干净但 blink_skips 高→EAR 阈值）+ 启动输出
   EAR 分布统计；逐帧计数 N 秒汇总限流；/status 加 gaze_graph: captured|eager。
8. **[MEDIUM] T6 无 copy-paste 命令、Verify=人工检查**。→ 交付物清单化（参数表行/快速启动块/
   status 表行/故障排查节/路线说明），Verify 改"对照清单 diff"。
9. **[MEDIUM] G3 扫描可能改变 off 默认路径**（从不开 gaze 的操作者突然看到 gaze 告警）；EAR 阈值/
   收敛角/色迁方法零旋钮。→ G3 扫描门控在 gaze≠off 之内；`--gaze-min-buffer`；RUNBOOK 写明
   angles 是固定常量及改法。
10. **[MEDIUM] 路线 B 专属**：S2/§7 沿旧注入点整体过期；metadata 裁剪坐标可为 NaN
    （crop_head_video.py:733-734）；gaze 后置会**静默覆盖 repair 的眼部输出**（gaze 最后落笔），
    两功能像素优先级未文档化。→ 标注条件化 + metadata-NaN 旁路 + 顺序说明 + fullframe 计时。
11. **[MEDIUM] 路线 A 专属**：离线覆写无备份/dry-run/幂等保障/修正标记——修正后资产与原始不可区分，
    /status 显示 off 而画面已含修正（可观测性说谎）。→ 备份目录、--dry-run、metadata 修正标记、
    服务端与预处理端都拒绝二次修正。

### DX DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          否      N/A   DISAGREE→修正
  2. API/CLI naming guessable?         部分    N/A   DISAGREE→修正
  3. Error messages actionable?        否      N/A   DISAGREE→修正
  4. Docs findable & complete?         否      N/A   DISAGREE→修正
  5. Upgrade path safe?                否      N/A   DISAGREE→修正
  6. Dev environment friction-free?    部分    N/A   部分确认
═══════════════════════════════════════════════════════════════
```
主审与声部独立命中同一主题（flag breaking ×2 声部、repair 可观测 ×2、路线条件化 ×2）——高置信。

## DX 修正（修订版，替代前版 F1-F3）

| # | 修正 | 内容 |
|---|------|------|
| F1' | flag 语法写死一处 | `--gaze-correction`：`nargs='?' const='on' default='off' choices=['off','on','adaptive']`。裸 flag=on（向后兼容）；`off` 显式合法；不再声称"与 face_repair 一致"（face_repair 语法本身混乱，不复制） |
| F2' | 错误注册表 | ckpt 缺失/键错误/损坏三态报错文案进错误注册表（含"这不是 gaze 模型权重：缺少键 current_generator"）；NaN 资产报错附 copy-paste 修复命令（不带 --safe_mode 重跑预处理步骤 10） |
| F3' | 状态语义拆分 | gaze_state = 配置态（off/on/adaptive/forced_off）；活动性 = 独立计数 corrected_frames/blink_skips/nan_skips/gate_skips（N 秒汇总限流）+ gaze_graph: captured\|eager + gaze_asset_nan_rate |
| F4 | ckpt 治理 | `--gaze-ckpt PATH`；默认路径 weights/gaze_generator.ckpt（改名消除同名歧义）；加载校验 current_generator 键 |
| F5 | 产能交互可观测 | /status 加 repair_skip_count；`--gaze-min-buffer`（默认 6，独立旋钮）；RUNBOOK 写明 gaze↔repair 交互 |
| F6 | off 默认纯净 | G3 NaN 率扫描仅在 gaze≠off 时执行（默认路径零新开销零新告警） |
| F7 | T6 交付物清单化 | 参数表行/快速启动块（含完整命令+资产前置条件）/status 表新增行/三场景故障排查（NaN 资产、blink 风暴、eager 降级）/路线说明；Verify=对照清单 diff |
| F8 | 路线 A 防护（条件化） | 若选 A：备份目录、--dry-run（输出各资产检测失败率）、metadata 修正标记、双端拒绝二次修正、RUNBOOK"该角色已离线修正，--gaze-correction 必须 off" |
| F9 | 路线 B 补全（条件化） | 若选 B：metadata 坐标 NaN 旁路、gen→repair→comp→gaze 顺序说明（gaze 最后落笔覆盖 repair 眼部）、fullframe 独立计时、S2/§7 按新注入点重推导 |

**PHASE 2.5 COMPLETE.** DX overall: 7.25→8.5/10（F1'-F7 落地后）。TTHW: ~2-3min（进程）；
到可见效果依赖路线裁决与资产前置。Codex: N/A。Claude subagent: 11 issues（2 critical）。
Consensus: 1/6 部分确认，5 DISAGREE → 已全部转为修正 F1'-F9。Passing to Phase 3。

---

# 工程审查（Phase 3）— 最终门前的收官审查

## Step 0 范围挑战（autoplan: 不缩减）

- 复杂度检查：1 新文件 + 4-5 修改 + 1-2 测试 = 6-8 文件 → 阈值边缘，通过；但路线 A 只动预处理
  （1-2 文件）——复杂度随路线差异巨大，任务已按路线条件化。
- 子问题→现有代码映射：§10 复用清单属实（已核对 archs.py / models.py / render_loop.py）。
- **[E-ARCH] 核心架构裁决：GazeCorrector 必须设计成"注入点无关"**——把「EAR/状态机/中心平滑/
  eye_size 锁定/推理」与「贴回目标」解耦：路线 A 的壳是资产批处理循环（目标=count 资产），路线 B
  的壳是单帧全帧接口（目标=composited 帧）。核心逻辑共享，gate 裁决后只写薄壳。两路线争论期间
  核心类先行，避免裁决后返工。
- 结论：范围成立，架构修正 E-ARCH 如上，E-ENG1/2 见下。

## S1 架构审查（两路线）

```
路线 A（离线）:
  run_preprocess.py 步骤11（新壳） ─▶ GazeCorrector 核心（检测态/推理/贴回）
  count/ 资产 ─▶ 修正+标记 ─▶ 目检 ─▶ 入库      render_loop: 零改动

路线 B（实时, compositor 后）:
  RenderLoop._process_frame:
    gen → repair → composite → composited(1440×2560 CPU)
    → GazeCorrector.correct_fullframe(composited, facemark_256, meta)
        坐标映射: full = crop_xy + facemark_256 × (crop_size/256)
        眼 slice(全帧) → GPU → resize64 → 推理 → μ/σ → 贴回
    → frame_buffer.put(composited)
  facemark/metadata ──▶ correct_fullframe
```

- 路线 B 工程要点（已核算）：坐标映射 4.95×（crop_size≈1268/256）——眼宽 15-25px → 全帧 75-125px；
  全帧 slice/贴回每眼 ~100KB×2 拷贝 <0.2ms；推理 1-2ms（graph）；EAR+映射 0.3ms。
  **预算 ~2-3ms/帧 ✓**（替换原 §7 的 2-4ms，引用基线已修正为 PERFORMANCE_REPORT 里程碑段）。
- in-place on composited：与 compositor 现有行为同域（循环回绕 batch 重载洗掉，sequence_frame_pack
  wrap-aware 重载），防御性 copy 仍执行（G4）。
- repair↔gaze 落笔顺序（DX 发现 10）：face_repair 修复的眼睛本就被 compositor 丢弃（CEO 发现 1），
  gaze 后置落在 composited 帧上——两者无像素冲突，但必须在 RUNBOOK 说明该事实。
- SPOF（路线 B）：correct_fullframe try/except 旁路 + 计数 ✓（S2）。
- 回滚：路线 A=git revert（预处理独立）；路线 B=--gaze-correction off。均二类门。

## S2 代码质量

- DRY：blink 状态机/EAR/平滑逻辑单实现双壳（E-ARCH）✓；不复制 inference.py（dlib 隔离，import 边界
  已定死）。
- 命名：GazeCorrector / correct_fullframe / process_asset 直述 ✓。
- 复杂度：核心状态机方法 <5 分支预计 ✓。
- 发现 0（超出 CEO 清单）：`GazeCorrector.__init__` 加载即 CUDA Graph 捕获 + ckpt 键校验
  （F4），构造函数做 I/O 与设备操作——与 models.py 现有模式一致，可接受。

## S3 测试审查（never skip）

测试图与防 flake 策略见测试计划产物
（~/.gstack/projects/Eikwang-EDTALK/edtalkdev-main-test-plan-20260902-215742.md）。
12 单测 + 4 集成，双路线条件化标注；U6 缓冲不污染与 U2 眨眼窗口为 2am 关键测试；
U10/U11 为路线 A 专属（幂等/失败率）。无未覆盖路径。E-ENG1：新增 U13——μ/σ 与 skimage 的
A/B 像素差异断言（30 行对比脚本产物进 tests/fixtures），门裁决前先跑（G7）。

## S4 性能

- 路线 A：运行时零成本；预处理 +10% 时长 ✓。
- 路线 B：~2-3ms/帧（上面核算），产能 50fps→~44fps，仍远超 25 输出线 ✓。graph replay 值得
  （batch2×64×64 单 launch）；eager fallback 下 ~4-5ms 仍可接受。E-ENG2：`--gaze-min-buffer`
  独立旋钮（F5）落地后，gaze 与 repair 的 adaptive 竞争可调。
- 显存：+gaze 模型 ~20MB + graph pool ~10MB ✓。无 N+1（无 DB）。无新发现（G5 联合预算已列）。

## NOT in scope（Eng 确认）

CEO §9 四项维持；新增：跨角色批处理工具化（单角色先行）、head pose 修正（正交）。

## What already exists（Eng 确认）

§10 清单 + run_preprocess.py 步骤骨架（路线 A 的挂载点）、metadata 结构（crop_x/y/size 已有）。

## 工程完成总结

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (ENG)                |
+====================================================================+
| Step 0 范围挑战      | 范围成立; E-ARCH 注入点无关核心类             |
| Section 1 (Arch)    | 2 issues (E-ARCH/E-ENG1) — 已裁决并入          |
| Section 2 (Quality) | 0 issues（状态机单实现双壳）                   |
| Section 3 (Tests)   | 12 单测+4 集成+U13；artifact 落盘 ✓            |
| Section 4 (Perf)    | 0 issues（路线B 2-3ms 核算成立）               |
| NOT in scope        | 已写（6 项）                                   |
| What already exists | 已写                                           |
| Failure modes       | 见 CEO 注册表 + F2' 错误注册表, 0 CRITICAL     |
| Test plan artifact  | 已写盘 ✓                                       |
| TODOS 收集          | E3 ONNX导出, E4 GPU直方图匹配, G8 质量前置     |
| Unresolved          | USER CHALLENGE 1 留门                          |
+====================================================================+
```

## 工程双声部（Step 0.5）

CODEX 不可用 → [subagent-only]。

### CLAUDE SUBAGENT（ENG — 独立架构挑战）核心发现

1. **[CRITICAL] 路线 A 的目标资产也是错的——count 修正同样不可见**：最终帧 face 区域像素源头是
   **orifra**（`blend()` 的 `ori_crop_256 = resize(orifra[y1:y2, x1:x2])`），count 的唯一消费者是
   Generator（render_loop.py:408），其非嘴部输出被 mask 丢弃。按计划文本修 count = 第二次静默 no-op。
   **修复：路线 A 目标必须是 orifra**（最好并入 step 10 落盘前，零额外压缩代次），并写死防回归事实：
   "count 的唯一消费者是 Generator，其非嘴部输出被 compositor 丢弃"。
2. **[CRITICAL] 路线 B 的 in-place 安全论据错误——重复修正是常态而非边缘**：每次音频 push 后
   `_flush_buffer()` 不 reset 视频钟 → flush 后 put 不阻塞、渲染 ~20ms/帧 → **同一 video_idx 连续
   渲染两次**；且 `FrameBuffer.put` 按引用存储（output_buffer.py:109-111）→ 两槽位别名同一可变数组。
   现有 blend 部分幂等所以雷没炸；gaze 是非幂等生成式重绘，叠加即漂移（±0.05rad×2 = 5.8° 跳变）。
   **修复：写入目标必须是池化逐帧缓冲（全帧拷贝 1-2ms 计入预算）+ 同 idx 双渲染确定性测试**。
3. **[HIGH] 坐标映射公式错误**：预处理先 clamp 再 resize，facemark 在可能非等比拉伸的图上检测；
   metadata `scale_factor` 恒 1.0 不可信（crop_head_video.py:771 硬编码）。映射必须复刻
   `prepare_crop` 的 clamp 几何（x/y 独立缩放，用 clamp 后的 x1_c/y1_c/crop_w/crop_h）。
   DX 发现 10 的"NaN 字段"指错——crop_x/y/size 恒有限，真正故障是 clamp 几何失配（不抛异常，
   表现为把修正贴到鼻梁上）。
4. **[HIGH] 计划文档已不可执行**：主规格（§2/§3/§5/§7/T1-T6）仍按已证伪注入点写，修正以补丁形式
   散落四阶段。**裁决后必须坍缩为"裁决记录+单一路线规格+重写任务表"**，先于任何编码。
5. **[HIGH] 测试系统性缺口——所有断言都在 correct() 两侧，没有一个验证修正到达最终输出帧**。
   端到端可见性断言（gaze on/off 眼区圆内像素差超阈值、圆外逐位相等）是唯一防第三次"静默 no-op"
   的测试，两条路线共同硬验收。另缺：同 idx 双渲染确定性、clamp 几何映射、通道序（链路 4 次翻转）、
   真实资产 EAR 稳定性（合成序列必过，2am 真正炸的是真实数据）。
6. **[HIGH] 路线 B 预算低估**：未计全帧防御拷贝 1-2ms + 每帧强制 D2H 同步 0.5-2ms（WDDM 有案底）
   + 转换 0.2-0.5ms → **现实 4-7ms**。64×64 graph 收益仅 ~0.5-1ms，先 eager 实测 >2ms 再开 graph；
   照搬 models.py 模式须连 side-stream warmup + sync + 形状守卫 + 静态缓冲一起搬。
7. **[MEDIUM] NaN 语义被 G2 错误合并**：原版语义是"检测失败→hold 上一帧中心继续修正"（眨眼才旁路）。
   合并后未检出帧夹在修正帧中间露原眼来回跳。→ 分流：检测失败→hold；眨眼→旁路。NaN 来源至少 3 条
   （SAFE_MODE、dlib 模型缺失、检测失败行）。
8. **[MEDIUM] 常量域换算缺失**：`_smooth` 的 20px 是全帧域（256 空间 ≈4px）；eye_size 应锁 256 空间
   （tracker pad_ratio 使 256 空间表观尺寸恒定）每帧换算全帧。
9. **[MEDIUM] 路线 B 纹理失配**：最终帧眼区是 256 round-trip 软化像素，生成眼偏锐 →"贴片感"；
   路线 A 无此问题（与邻域一致软化）。G8 目检应在此分流。
10. **[MEDIUM] facemark 双重消费未定义**：compositor 已有 EMA，gaze 状态应独立（不共享 mask 缓存状态）。
11. [LOW] 事实小错：NaN 检查位置（compositor.py:90/211 非 136）；dlib ~99MB 是运行时加载非 import；
    1440×2560/crop_size≈1268 应分辨率无关表述（当前 longest_edge=1920）。

### ENG DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               否(2crit) N/A  DISAGREE→修正
  2. Test coverage sufficient?         否(可见性) N/A DISAGREE→修正
  3. Performance risks addressed?      部分     N/A   部分确认
  4. Security threats covered?         是       N/A   CONFIRMED
  5. Error paths handled?              否(NaN分流) N/A DISAGREE→修正
  6. Deployment risk manageable?       是(条件)  N/A  部分确认
═══════════════════════════════════════════════════════════════
```
声部 11 项发现主审复核全部确认（1/2/3/4/5/7 直接采纳为 E-修正；6/8/9/10/11 并入）。

## 工程/跨阶段修正 E1-E10（并入方案）

| # | 修正 | 内容 |
|---|------|------|
| E1 | 路线 A 目标改 orifra | count 非嘴部输出被丢弃（防回归事实写死）；最好并入 step 10 落盘前；F8 防护清单为交付物 |
| E2 | 路线 B 池化写缓冲 | 全帧拷贝入池化缓冲（1-2ms 计入预算）；同 idx 双渲染确定性测试 |
| E3 | clamp 几何映射 | 复刻 prepare_crop clamp，x/y 独立缩放，弃 scale_factor；face>短边测试用例 |
| E4 | 域换算表 | 常量逐项标注域无关/域相关；_smooth 阈值与 eye_size 锁定按 256 空间 |
| E5 | NaN 语义分流 | 检测失败→hold 中心继续修正；眨眼→旁路；nan_skips 分计数 |
| E6 | 端到端可见性断言 | gaze on/off 眼区圆内像素差>阈值、圆外逐位相等——两路线共同硬验收 |
| E7 | 预算重写 | 路线 B 4-7ms（含拷贝/同步/转换）；graph 先 eager 实测 |
| E8 | 裁决后文档坍缩 | 裁决记录+单一路线规格+重写任务表，先于编码 |
| E9 | 真实资产 EAR + 通道序测试 | 每 30s 切换次数<N；非对称色 fixture |
| E10 | 独立 facemark 状态 | gaze 不共享 compositor EMA |

**PHASE 3 COMPLETE.** Codex: N/A。Claude subagent: 11 项（2 critical）。
Consensus: 1/6 CONFIRMED, 2 部分确认, 3 DISAGREE → E1-E10。Passing to Phase 4 (Final Gate)。

## DX 共情叙事（第一人称）

我是这个服务的运营者。我早就习惯在启动命令里加 `--gaze-correction`——今天它静默 no-op，我毫不知情；
明天它改成三态，我的老命令直接拒绝启动，报错也不告诉我怎么迁移。我按文档跑了预处理，facemark 出来
全 NaN；我开启 gaze，FPS 没掉、日志没红、buffer 蓄满，可输出视频里的眼睛一像素都没变过。我开了
gaze 之后 repair 触发率悄悄掉了一半，/status 上什么也看不出来。**这四段挫败感就是 DX 发现
1/2/3/5 的来源，全部已列为任务验收项（T3/T4/T6/G3/F5）。**

## 跨阶段主题（2+ 阶段独立发现 → 高置信信号）

| 主题 | 出现阶段 | 结论 |
|------|----------|------|
| **修正可能永远不出现在输出里（静默 no-op）** | CEO 发现1、DX 发现1、ENG A-1/A-3 + 测试缺口 | 三阶段独立命中同一风险——最高置信信号。E6 端到端可见性断言成为两路线共同硬验收 |
| **in-place/别名污染** | CEO 0E+声部发现5、ENG A-3 | 三次独立确认；E2 池化写缓冲 + 同 idx 双渲染确定性测试 |
| **facemark 资产质量（SAFE_MODE/模型缺失/检测失败）** | CEO 发现3、DX 维度1、ENG 边界2/6 | 三阶段；G3 启动校验 + E5 NaN 分流 + copy-paste 修复命令 |
| **flag 破坏性变更** | DX 发现2/3 | 两处独立；F1' 单一语法规格写死 |
| **gaze↔repair 产能竞争不可观测** | CEO 发现6、DX 发现5 | 两阶段；F5 repair_skip_count + --gaze-min-buffer |

## 决策审计表（Decision Audit Trail）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否 |
|---|------|------|------|------|------|------|
| D1 | CEO | 模式 SELECTIVE EXPANSION | autoplan override | — | 延续性工程决策 | — |
| D2 | CEO | 0C-bis 选方案A（PTH+CUDA Graph） | Mechanical→被ENG修正 | P1/P3 | Graph 基建复用；B 的优势不在需求域 | B |
| D3 | CEO | E2 /status 计数、E5 启动回显并入 | Mechanical | P2 | 半径内 <1 天 | — |
| D4 | CEO | E3 ONNX导出、E4 GPU直方图 → TODOS | Mechanical | P3 | 需求未出现/需运行数据 | — |
| D5 | CEO | 复核确认声部发现1（注入点无效）并作废 P6 | Mechanical | P1 | 代码可验证的功能失效 | — |
| D6 | CEO | USER CHALLENGE 1（路线A vs B）移交最终门 | USER CHALLENGE | — | 用户指定方向被两声部挑战 | — |
| D7 | DX | F1'-F9 修正并入（flag语法/错误注册表/状态拆分/ckpt治理/产能可观测/off纯净/T6清单化/路线条件化） | Mechanical | P1 | 操作者第一体验是需求，2 critical 代码核实 | — |
| D8 | ENG | E-ARCH 注入点无关核心类 | Mechanical | P5/P4 | 裁决后只写薄壳，避免返工 | — |
| D9 | ENG | E1-E10 修正并入（orifra目标/池化缓冲/clamp映射/域换算/NaN分流/可见性断言/预算重写/文档坍缩/真实EAR测试/独立状态） | Mechanical | P1 | 2 critical 代码核实成立 | — |
| D10 | ALL | **路线裁决（A orifra离线 vs B compositor后实时）+ 质量目检结果处置** | **USER CHALLENGE** | — | 用户原始方向（输入端注入）已被证伪 | — |
| D11 | 门 | **USER CHALLENGE 1 裁决：分步路线**——先 T0+T7 质量前置（离线对比视频目检 + 颜色迁移 A/B），用真实输出验证功能价值后再裁路线 A/B。两路线共用的前置，零浪费 | 用户裁决 | — | 先回答"功能值不值得做"，避免在无效功能上投入任何路线的工程量 | 直接 A / 直接 B |

# 最终门裁决（Phase 4 CLOSED）

**用户裁决：分步路线。** 先执行 T0（质量前置目检）+ T7（颜色迁移 A/B），据结果裁决路线。

## T0/T7 结果（2026-09-03）

- T0 目检**通过**：wy/segment1 帧 3000-3040，40/40 帧修正成功；用户确认"视线修复很好"（comparison.mp4）。
- T7：μ/σ 与 skimage **肉眼不可分辨** → 采用 μ/σ（免 skimage 依赖）。
- 实测 ~505ms/帧（dlib 全帧检测主导）——离线路线可行，但为复用预处理 facemark 数据留出加速空间。

## 路线终裁（用户方案 + probe 修正）

用户提出路线 C：动态开关 + 视线纠正位于口型推理前或后 + **compositor mask 扩展双眼区域** +
全部 GPU 一次完成 + 预处理数据化（dlib 不进实时循环）+ 保留动态角度能力。

**Probe 实测裁决（gaze_crop_probe.py，4 帧跨帧验证）**：
- **C-输入端（口型推理前）**：eye_diff 1.8x 噪声级 —— **Generator 重绘抹除输入纠正，失效**（证实 ENG 审查担忧）。
- **C'-rendered 端（口型推理后）**：eye_diff **28.7-29.2x 强信号**，目检画质自然融合 —— **成立**。

**最终架构（C'）**：
```
count crop → 口型Generator → rendered(256,GPU) → GazeCorrector.correct() → face_repair
→ compositor（mask = 嘴部 + 双眼区域[eyes_enabled]） → 输出
```
- 动态开关：off 时 mask 回退嘴部-only（输出眼睛 = 原视频原生眼，与今日完全一致）
- 动态角度：--gaze-convergence（按素材调整收敛角）
- 全程 GPU（rendered 已在 GPU，零 CPU 往返）；开销 ~2-3ms/帧；25FPS 产能余量 40fps
- dlib 不进实时循环：眼位置来自预处理 facemark（256 空间）

## 裁决后的执行序（已完成）

1. ✅ T0/T7 质量前置（probe + 目检通过）
2. ✅ GazeCorrector 核心类（realtime_serve/gaze_correction.py：EAR 状态机/平滑/锁定/动态角度/错误旁路/graph-eager 双模式）
3. ✅ compositor mask 扩展双眼（eyes_enabled，1.2x 内圈/1.7x 外圈过渡带）
4. ✅ render_loop 接线（gen 后 uint8 前，timing gaze 键，switch_segment reset，adaptive 门控）
5. ✅ CLI 三态（裸 flag=on 向后兼容）+ --gaze-convergence + --gaze-ckpt + fail-fast 校验
6. ✅ /status 扩展（gaze_state/gaze_graph/gaze_frames 计数）
7. ✅ 测试 15 项新增（164 passed 全套）+ RUNBOOK 排查指引

**遗留（TODOS）**：EAR 阈值 256 域校准（真实资产统计）、on/off 运行时热切换（阶段 2）、
GazeCorrector 模型插件化（head pose 等后续区域模型复用此模式）。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` Phase 1 | 范围与战略 | 1 | issues_open→closed | 10 声部发现（2 critical 代码核实：注入点无效/离线替代遗漏）；G1-G8 修正 |
| Codex Review | 未运行 | Codex auth 不可用 | 0 | unavailable | 降级为 Claude 子代理 [subagent-only] |
| DX Review | `/autoplan` Phase 2.5 | 开发者体验 | 1 | issues_open→closed | 11 发现（2 critical：路线未条件化/裸flag破坏性变更）；F1'-F9 |
| Eng Review | `/autoplan` Phase 3 | 架构与测试（必需门） | 1 | issues_open→closed | 11 发现（2 critical：count目标也错/重复渲染别名）；E1-E10；测试产物落盘 |
| Design Review | 跳过 | 无 UI 范围 | 0 | skipped | — |

- **CROSS-MODEL:** 三阶段独立命中"静默 no-op"（×3）、in-place 污染（×3）、facemark 资产质量（×3）、产能竞争（×2）——最高置信信号
- **VERDICT:** CEO + ENG + DX CLEARED（经最终门裁决分步路线，全部发现已处置或条件化）— ready to implement（T0/T7 先行）

NO UNRESOLVED DECISIONS
