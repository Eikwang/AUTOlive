<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-EDTALK\\main-autoplan-restore-20260921-135917.md" -->
## Implementation plan
# 视线纠正处理时机优化（眼源切换方案）

状态: /autoplan 评审中（CEO/DX 已裁决，Eng 待审）| 分支: main | 日期: 2026-09-21

> **TL;DR：裁决 = 路线 B（眼源切换 + 修复后贴回）；用户原案（先修正再推理）被 probe 实测否决（§2/§4）。运行时选择器 `--gaze-eye-source ori|rendered`（默认 ori）落地行为回滚承诺（DX 发现1）。术语表见 §6。**
前置: GAZE_CORRECTION_PLAN.md（Route C' 已上线）+ PIPELINE_MAP.md（链络底图）

## 1. 用户需求（原文）

> 现在遇到的问题是：Generator推理时会让原图的眼睛区域劣化，然后视线修正在被劣化的
> 眼部图像上工作，输出的是质量较低的眼睛，然后再面部修复后，输出画面的角色眼睛和原图
> 并不太像了。
> 所以我想优化一下视线修正功能的处理流程，先进行视线修正，然后用修正的图进行Generator
> 推理，然后再面部修复。让视线修正在质量较好的原图上进行，这样即使Generator推理时劣化
> 了眼睛部分，也不会导致眼睛变形。并且这样优化最简单，只需修改流程中的处理顺序，避免
> 出现不可控风险。

用户提议的流程：原始帧 → **视线修正** → Generator 口型推理 → 面部修复 → 贴回。

## 2. 提议方向的前提核查（代码 + 既有 probe 实测）

| # | 前提 | 核查结果 |
|---|------|----------|
| P1 | 先修正再推理，修正会保留在输出中 | **被 probe 实测否决**：gaze_crop_probe.py（2026-09-03）验证 C（输入端纠正）eye_diff 1.8x 噪声级——Generator 重绘抹除输入端修正；C'（输出端）28.7-29.2x 强信号。脚本内置结论："2列不跟随纠正（Generator 抹除），路线 C 输入端失效" |
| P2 | 即使修正保留，眼睛质量也会变好 | 逻辑不成立：最终帧眼睛像素源头是 Generator 重绘（eyes_enabled mask 贴回 rendered）——输入端修正（若保留）改变的是眼位/眼向，**像素质量仍由 Generator 重绘决定**，无法解决"不像原图" |
| P3 | 根因假设：眼睛不像原图 | **代码证实**：gaze-on 时最终眼 = GPEN(gaze生成(Generator重绘的眼))——三重生成链，源头是 256² 劣化重绘；而 T0 目检用户确认"视线修复很好"的对比视频跑的是 **orifra 原图**（gaze_quality_probe.py 输入=orifra 全帧）。用户批准的质量域 ≠ 当前生产管线的质量域 |
| P4 | "只改顺序"是最简单方案 | 改顺序本身是 1 行；但被 P1 否决。真正解决 P3 的方案工量见 §3（仍属 S 级） |

## 3. 修正方向（待评审裁决）：眼源切换——修正工作在原图眼上，贴回域不变

核心思路：保持 Route C' 的管线位置与 compositor/mask 全部不变，只把 **gaze 生成的
像素源**从「Generator 重绘的眼」换成「orifra 原生眼」，并把**贴回时机**移到面部修复
之后（GPEN 不再触碰眼睛）：

```
现行为:  gen → gaze(源=rendered劣化眼) → uint8 → GPEN修复(整脸含眼) → 贴回(眼=三重生成)
修正后:  gen → uint8 → GPEN修复(整脸) → gaze(源=ori原生眼, 贴回=修复后rendered) → 贴回
                                                                    (眼=一次生成,原生源)
```

- 眼源 = `prepare_crop(orifra, meta)` 的 ori_crop_256（与 count 同为 256 空间，facemark
  直接适用）；T0 目检已在该域确认画质（40/40 帧）。
- bypass 帧（眨眼/检测失败/门控关闭）贴**未修正原生眼**——"gaze on ⇒ 眼睛永远原生源"
  的不变式，消除眨眼帧 Generator 眼/原生眼交替闪烁。
- 需处理：ori_crop_256 需要预载器提供**原始未混合副本**（现 blend 原地写 orifra，
  循环回绕后眼源被上轮修正污染 → 生成式二次修正漂移，ENG A-3 同源风险）。
- 预算增量：ori 256² H2D 上传 ~0.3ms + uint8↔float 转换 ~0.2ms（贴回与生成推理成本已在
  既有 gaze 2-3ms 内，不重复计）。产能 ~50fps → ~48-49fps（CEO 复核修正：原稿 3-4ms/~44fps
  系重复计入既有 gaze 成本，已更正；p99 以 timing 直方图实测为准）。

## 4. 备选路线（评审中裁决）

- A（用户原案）：gaze 移到 Generator 前 —— P1 实测否决，退出。
- B（本案推荐）：眼源切换 + 修复后贴回（§3）。
- C（ENG 审查曾评估的 Route B 全帧后置）：compositor 之后全帧坐标映射贴回 —— 多全套
  clamp 几何映射/全帧拷贝/D2H 同步问题，§3 在 256 crop 域内等效达成，工量更小。
- D（不动流程，只调 GPEN α / 时序平滑）：不解决"源头是劣化眼"的根因；**定位为本案 B 的
  回滚路径**——若混合域接缝/抖动未过目检，回退到现状（gaze on 现行为）即 D。**运行时
  可达性（ENG F1a）：rendered 模式的调用点回落到 repair 之前（逐位复现现状），ori 模式
  在 repair 之后——D 经 --gaze-eye-source rendered 运行时可达，为真二类门。**
- E（条件化关闭 Generator 眼部生成以省下被丢弃的眼部计算）：**否决**——Generator 与 GPEN
  均为整 crop 单次前向，无区域级开关，眼部计算在模型内部不可分离（见 Review record S1-E）。
- F（微调 Generator 眼部忠实度——根因层解法）：→ TODOS 战略路线图；本方案定位为战术止血，
  保持贴回机制薄、可逆。

## 5. 范围

- 修改: gaze_correction.py（correct 契约更名 eye_source/canvas 双 tensor——DX 发现4；眼源运行时选择器 ori|rendered 默认 ori；门控仅约束生成段、原生贴回段不受门控——ENG F2）、
  render_loop.py（调用点按 eye_source 分流：rendered→repair 前旧位逐位复现现状 / ori→repair 后；gaze timing 键覆盖上传+转换+correct+贴回全程——ENG F1a/F8）、sequence_frame_pack.py
  （预载 pristine ori_crop_256）、compositor.py（blend 使用 pristine crop，可选）。
- 不动: compositor mask 结构、Audio2Lip/Generator/repair 推理栈。CLI/HTTP 仅**增量**扩展：新增 --gaze-eye-source 旗标与 /gaze/params 的 eye_source 字段（向后兼容，DX 发现1）。
- 验收: 眼区可见性断言（gaze on/off 像素差）、bypass 帧眼=原生、缓冲不污染、
  真实资产目检对比视频（T0 协议，**在生产混合域出片**——专查 ori 眼/GPEN 脸接缝与
  bypass 帧连续性）、timing 直方图 gaze 键 p99 实测、同 idx 双渲染幂等断言
  （pristine 眼源）、**实施前置：在当前部署 checkpoint 重跑 gaze_crop_probe 并记录权重
  SHA**（2026-09-03 的 1.8x 抹除结论需对现模型复验）。

## 6. 术语表

- Route C'：GAZE_CORRECTION_PLAN.md 定案的注入点——视线纠正挂在 Generator 输出端。
- count：资产目录 count/<段>/（256² 头部裁剪帧，Generator 唯一输入；最终帧非嘴部像素不采用其输出）。
- pristine：未经 blend 原地改写的原始副本（防循环回绕后的二次修正漂移）。
- T0/T4-T7：任务编号；T0 亦指 GAZE_CORRECTION_PLAN 的质量目检协议（对比视频 + 用户签字）。
- 二类门：可逆决策（有开关/回滚路径可撤销）。
- EMA：指数滑动平均（α 为新帧权重）。

## 7. 裁决与验收

CEO 阶段裁决与验收义务块（不可变；DX 阶段补充见 Review record 的 dx 义务块）：

<!-- autoplan-accepted:ceo -->
- 采用路线 B：眼源切换（gaze 生成源 = orifra 原生眼 ori_crop_256，256 crop 域）+ 贴回时机后移至 face_repair 之后（GPEN 不再处理最终可见眼像素）。compositor mask 结构、CLI、HTTP 契约、Audio2Lip/Generator/repair 推理栈全部不动。验证：T5 六项单测 + T6 生产混合域 T0 目检（硬验收）。
- correct 新契约：correct(source_256, target_256, facemark, gaze_frame)；source=原生眼源（pristine ori_crop_256 GPU float [-1,1] RGB），target=修复后 rendered；不修改任一输入，返回新张量；μ/σ 颜色迁移参照与贴回目标域的接缝表现写入实现注释并在 T6 目检裁决。
- bypass 不变式：gaze on（含 adaptive 门控关闭帧、眨眼 state=1、检测失败 state=2）⇒ 最终帧眼睛一律原生源——生成步被门控/旁路时贴未修正原生眼（使用平滑后眼中心）。此为产品行为变更，标注 TASTE 留最终门确认。
- pristine 眼源：BatchPreloader 解码期并行产出 pristine ori_crop_256（窗口加载 + publish_prefetch 双路径）；disk-fallback 帧就地 prepare_crop 降级并计数；pristine crop 同时供 compositor.blend 使用（顺带修复既有 blend 回绕漂移，X1）。
- 实施前置（T4）：在当前部署 checkpoint 上重跑 realtime_serve/gaze_crop_probe.py 并记录权重 SHA；若 1.8x 抹除结论在现权重上不复现（Generator 已保留输入端修正），回到最终门重新裁决路线。
- 验收全集：贴回隔离断言（非眼区逐位==target）；双输入不污染；bypass 帧眼==原生；同 idx 双渲染幂等（pristine 源）；gaze on/off 眼区可见性；降级链（生成异常→原生眼、pristine 缺失→就地降级+计数）；gaze 键 p99 timing 实测；生产混合域 T0 出片（接缝 + bypass 连续性 + facemark 抖动专查）并经用户目检签字。
- 性能预算（修正后）：增量 ~+0.5ms/帧（ori H2D 0.3ms + uint8↔float 0.2ms），产能 ~50→~48-49fps；若实测 p99 使产能跌破 40fps 则回到最终门复议。
- 回滚路径：D（现状行为）为 B 的行为回滚；--gaze-correction off 为功能回滚；均为二类门。
- 备选 E 否决存档：Generator/GPEN 均为整 crop 单次前向、无区域级开关，"被丢弃的眼部计算"不可分离。
- 延迟项进 TODOS：F（Generator 眼部忠实度微调）、X3（全脸身份保真审计）、生成时视线条件化（战略）、ori crop GPU 批量预载。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- eye_source 运行时选择器（DX 发现1）：构造参数 --gaze-eye-source {ori,rendered}，默认 ori；/gaze/params 增补 eye_source 字段（拒绝式校验，additive 兼容）；rendered 模式语义 = source=canvas=修复后 rendered（注：与旧管线顺序不逐位等价，旧顺序行为仅经 git revert 可达——回滚语义已在计划中注明）。验证：热切 pytest 用例 + /status gaze_params 回显。
- correct 契约命名（DX 发现4，替代 CEO 块中的 source_256/target_256 命名，语义不变）：correct(eye_source, canvas, facemark, gaze_frame)——眼从 eye_source 读、贴进 canvas、返回新张量；canvas 域守卫断言（shape/device/[-1,1] 域检查）失败→降级原生眼+计数。
- T4 可执行化（DX 发现2）：命令 `python -m realtime_serve.gaze_crop_probe --character-dir wy --segment 1 --frames 3000,3010,3020,3030`（默认 ckpt 路径）；判定阈值：输入端 eye_diff 倍数 <5x 且 C' 端 >10x ⇒ 抹除结论复现，方案成立；输入端 ≥10x ⇒ 修正开始穿透 Generator ⇒ 回最终门重新裁决。权重 SHA 提取：`python -c "import hashlib;print(hashlib.sha256(open('wy/checkpoint/new.pt','rb').read()).hexdigest())"`。
- 文档导航（DX 发现3/5）：TL;DR 置顶、§6 裁决与验收标题、§7 术语表（Route C'/count/pristine/T 编号/二类门/EMA）、状态行反映评审进度——已由基线修订落地。
- 可观测性（DX 发现6）：降级计数命名 gaze_frames.pristine_fallback（/status）+ 日志行 `[GazeCorrector] pristine fallback`；canvas 守卫失败计入 error_skips 并首帧打栈（沿用既有模式）。
- T6 目检判据预置（DX 发现7）：3 组代表资产、100% 缩放逐帧下接缝不可见（无色差台阶/几何错位）、bypass 帧序列连续无跳变；不达标处置：先切 eye_source=rendered 复测，仍不达标→D（现状行为）。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- 双调用点分流（ENG F1a，替代 DX 块中 rendered 模式"source=canvas=修复后 rendered"语义与"旧顺序仅经 git revert 可达"注记）：rendered 模式调用点回落 repair 之前（逐位复现现状，D 成为运行时可达的真二类门）；ori 模式调用点在 repair 之后。验证：U11 逐位回归断言（REGRESSION RULE 强制）。
- 门控结构（ENG F2）：_gaze_allowed() 仅约束 GPU 生成段；bypass-only 分支（贴未修正原生眼）不受门控，路径单点收敛在 corrector 内。验证：U7（含"adaptive 门关帧最终眼==原生源"断言）。
- 状态机连续性（ENG F3）：bypass 贴回统一走 corrector 内部（推进 EMA/eye_size/blink 状态、跳过 forward）；legacy 路径 _fixed_eye_size None 守卫；门重开/mode 切换失效 _prev_gen 与 _skip_run。验证：U7/U9（段首门关 N 帧→无崩溃、贴回有效、门重开无鬼影）。
- 幂等断言语义（ENG F4，修正 CEO 块"同 idx 双渲染幂等"定义）：(a) eye_source 输入跨渲染逐位不变（pristine 保证）；(b) 两次渲染间 reset() 后输出逐位相等。不要求带时序平滑的连续渲染输出幂等。
- pristine 别名防护（ENG F5）：pristine（含 disk-fallback 就地降级路径）与 orifra 无内存别名（np.shares_memory 断言）；fallback 路径强制 copy。验证：U14。
- 通道序测试（ENG F6）：R≠B 合成帧单测，断言修正眼色相跟随 eye_source 域（防 BGR/RGB 互换被 μ/σ 部分掩盖到 T6 才现形）。验证：U10。
- T4 灰区规则（ENG F7）：输入端倍数落在 5x-10x ⇒ 回最终门（保守取向，与 T4 门性质一致）；记录实测数值。
- gaze timing 键边界（ENG F8）：= eye_source 上传 + 域转换 + correct + 贴回全程（repair 输出→D2H 之间）。
- eye_source 校验下沉 owner 层（ENG F9，复刻 M17 模式）：HTTP/CLI 只是第一道。
- 观测注记（ENG F10）：pristine_fallback 与既有 fallback 联合告警阈值（/status）。
- compositor.py:202-204 失实注释修正（E-T8，X1 落地同一提交）。
<!-- /autoplan-accepted:eng -->
## Review record

# CEO 审查（Phase 1）— 模式: SELECTIVE EXPANSION（autoplan override）

## 系统审计

- 分支 main，WIP 提交流（76a6df4 等），25fps 里程碑已达成（PERFORMANCE_REPORT.md），Route C' 已上线（GAZE_CORRECTION_PLAN.md 最终门裁决记录）。无 stash 冲突迹象；本计划触及文件与近期微调提交（fine_tune/、train/）无交集——微调活动与本服务运行时解耦，但 **checkpoint 权重可能已更迭**（子代理发现 2，成立）。
- 本日会话已完成全链路代码审读（PIPELINE_MAP.md，248 行）+ 两个 probe 脚本核读；无 TODO/FIXME 遗留于触及文件。
- 设计文档：无独立 office-hours 产物；GAZE_CORRECTION_PLAN.md 即该特性的设计档案（同域延续）。

## 0A. 前提挑战

| # | 前提 | 评估 | 结论 |
|---|------|------|------|
| P1 | 先修正再推理，修正保留 | probe 实测否决（1.8x 噪声级），且 probe 时效性存疑（权重可能更迭） | 用户原始方向 → **USER CHALLENGE 移交最终门**（附复验条件：当前 checkpoint 重跑 probe） |
| P2 | 输入端修正即可提升眼质量 | 逻辑不成立：眼像素源头是 Generator 重绘 | 同上，并入 Challenge |
| P3 | 根因 = 眼源是劣化重绘域 | 代码证实（三重生成链 vs T0 批准的 orifra 域） | 接受——本方案的核心依据 |
| P4 | 改顺序是最小方案 | 1 行改动但功能失效 | 接受否决；真正的最小方案是 B（仍属 S 级） |
| P5（声部新增） | probe 结论对当前 checkpoint 仍成立 | 未声明 probe 所用权重；期间微调活跃 | 接受附条件：实施前重跑 probe + 记录 SHA（并入验收） |
| P6（声部新增） | 原生眼贴 GPEN 平滑脸的混合域无缝 | 假定，未验证；blend 同域性假设被打破 | 接受附条件：生产混合域 T0 目检 + 接缝专查（并入验收） |
| P7（声部新增） | bypass 帧贴原生眼是纯改进 | 属产品行为变更（眨眼帧从 Generator 眼→原生眼），伪影暴露面扩至全帧 | 接受，标注 **TASTE** → 最终门 |

"什么都不做"的代价：gaze-on 时眼睛持续偏离原角色形象（用户已实测感知并报告）——真实质量缺陷，非假设。继续。

## 0B. 现有代码杠杆（子问题 → 已有代码）

| 子问题 | 已有资产 | 复用方式 |
|---|---|---|
| 原生眼像素源 | prepare_crop(orifra, meta) → ori_crop_256 | 移到预载期批量生产（_decode_frame 已解 orifra，加 resize/坐标即可） |
| 256 空间眼几何 | facemark (N,68,2) + gaze_data 预计算眼中心/eye_size | 原样适用（count 与 ori_crop_256 同为该 crop 区域的 256² resize） |
| 眼生成推理栈 | GazeCorrector（EAR/平滑/锁定/graph/μσ 迁移） | 契约扩展：源/贴回目标分离，内部逻辑不动 |
| 修复后 GPU 张量 | repair_gpu 返回独立新张量（torch.empty 分配） | 天然安全贴回目标，无 graph 静态缓冲别名风险 |
| 贴回 mask | compositor eyes_enabled（1.2x/1.7x）+ corrector 圆形 mask64 | 结构不动 |
| 自适应门控/计数 | _gaze_allowed + gaze_frames 计数 | 门控语义仅约束"生成"步，贴原生眼步常开（gaze on 时） |

无重复建设。

## 0C. 梦想状态映射

```
CURRENT STATE                     THIS PLAN                        12-MONTH IDEAL
25fps 稳定; gaze C' 上线;     ---> 眼源切换原生域; GPEN不触眼; ---> 全区域生成时条件化/忠实化
眼质量=三重生成的劣化域            贴回机制薄且可逆                  （无贴回补丁）; 动态 gaze 时序;
                                                                   区域增强插件统一管理
```

方向判断：**靠近理想态的战术步**（原生域眼 = 与 T0 批准域对齐），但贴补丁路线与理想态的
"生成时条件化"存在张力——保持薄、可逆（D 回滚路径在案），F 进路线图对冲。

## 0C-bis. 实现备选方案

```
APPROACH A: 用户原案——gaze 移到 Generator 前
  Summary: 原始帧→视线修正→Generator→修复
  Effort:  XS (human ~1h / CC: ~5min)   Risk: 低（但功能失效）
  Pros:    改动最小；直觉自然
  Cons:    probe 实测修正被 Generator 重绘抹除（1.8x 噪声级）；且不改变眼像素源头，根因不动
  Reuses:  全部现有代码（移动一行）
  Completeness: 2/10（不解决陈述的问题）

APPROACH B: 眼源切换 + 修复后贴回（本案）★
  Summary: gaze 生成源=ori 原生眼（256 crop 域），贴回时机移到 GPEN 之后；bypass 帧贴原生眼
  Effort:  S (human: ~6-8h / CC: ~40min)   Risk: 低-中（混合域接缝需目检裁决，D 为回滚）
  Pros:    眼像素源与 T0 批准域对齐；GPEN 不再触眼（双重生成→一次生成）；不变式清晰
           （gaze on ⇒ 眼永远原生源）；compositor/mask/推理栈零改动
  Cons:    Generator 眼部渲染 + GPEN 眼部修复的计算仍发生但被覆盖（不可分离，见 S1-E）；
           新增 pristine ori crop 缓冲
  Reuses:  GazeCorrector 全部内部逻辑、facemark/gaze_data、eyes_enabled mask、repair_gpu 独立张量
  Completeness: 9/10

APPROACH C: 全帧后置（Route B 复活）——compositor 之后全帧坐标映射贴回
  Summary: 在 composited 全帧上按 clamp 几何映射眼坐标，裁剪→64²推理→贴回
  Effort:  M (human: ~2d / CC: ~1.5h)   Risk: 中（clamp 几何失配/D2H 同步/in-place 别名三处已知雷）
  Pros:    眼源同样可为原生域；不依赖预载器改动
  Cons:    全帧拷贝 1-2ms + 强制同步 0.5-2ms（WDDM 案底）+ 坐标映射复刻 prepare_crop clamp
           几何——B 在 256 crop 域内以零映射成本等效达成同一终态像素
  Reuses:  同 B
  Completeness: 8/10

APPROACH D: 参数止血（GPEN α 降/时序平滑调强）
  Summary: 不动流程，缓解劣化观感
  Effort:  XS   Risk: 低
  Pros:    零风险
  Cons:    不解决像素源头问题；GPEN α 降低会整体拉低修复收益
  Completeness: 4/10

APPROACH E: 条件化关闭 Generator 眼部生成 —— 不可行（见 S1-E）
APPROACH F: 微调 Generator 眼部忠实度 —— 根因层解法，进 TODOS 路线图（超本季 scope）
```

**裁决（P1 完整性 + P3 务实）**：选 B。B 是当前可行域内完整度最高且半径最小者；
C 被 B 严格覆盖（同终态、更少机械件）；D 转为 B 的回滚路径；E 不可行有据；F 进路线图。

## 0D. SELECTIVE EXPANSION 分析

复杂度检查：4 文件修改（gaze_correction/render_loop/sequence_frame_pack/compositor 可选）
+ 1 测试文件，0 新类。低于 8 文件阈值。通过。

最小改动集：pristine ori crop 缓存 + corrector 契约扩展 + render_loop 调用点后移。三者缺一不成立。

扩展候选（autoplan 自动裁决）：
| # | 提案 | 裁决 | 理由 |
|---|------|------|------|
| X1 | pristine ori_crop_256 同时供 blend 使用（顺带修复嘴部 blend 回绕漂移） | 采纳 | 同一数据、半径内、S 工量、修复既有缺陷 |
| X2 | /status 增加 eye-source 分解计数 | 不并入 | blink/nan/error_skips 已覆盖 bypass 分解；语义变化写入 RUNBOOK 即可 |
| X3 | 全脸身份保真审计（逐区域 probe） | TODOS(P2) | 子代理发现 1 的后续调查项，超半径 |
| X4 | facemark 抖动 → 贴回几何抖动平滑 | 采纳（设计内） | corrector EMA 已平滑中心；bypass 贴回同样使用平滑后中心，设计已覆盖，写入任务验收 |

## 0E. 时序审讯

- HOUR 1: correct() 新契约参数序与命名——`correct(source, target, facemark, gaze_frame)`；
  不变式写死：source/target 均不被修改，返回新张量（对二者皆 clone 隔离）。
- HOUR 2-3: [critical] pristine 眼源——预载 `_decode_frame` 增加 meta 坐标参数，解码时同步
  resize 出 ori_crop_256（uint8 CPU，~0.3MB/帧，批 30MB）；prefetch 路径（publish_prefetch）
  同步携带；disk-fallback 单帧路径就地 prepare_crop（降级可用，计 independent 计数）。
- HOUR 4-5: 转换链——repair 输出 uint8 BGR CHW → float RGB [-1,1]（flip+归一，GPU ~0.1ms）
  → gaze → uint8 BGR → 下载。 ori CPU uint8 → GPU float 每帧 ~0.3ms；可选后续优化为预载
  GPU 批（非本期）。
- HOUR 6+: 测试——贴回隔离断言（非眼区逐位==target）、双输入不污染、bypass 帧眼==原生、
  同 idx 双渲染幂等、graph 回退；生产混合域 T0 出片目检。

## 11 节深度审查（主审）

### S1 架构审查

```
改造后数据流（逐帧，gaze on）:
  BatchPreloader ─ orifra/crop/meta/facemark/[新]ori_crop_256(pristine) ─▶ RenderLoop
  gen(256²) → uint8 → repair_gpu(整crop, 独立新张量) → gaze.correct(source=ori²,
      target=repaired) ─ 眼区64²生成,圆形mask贴回 ─▶ 下载 → compositor.blend
      (mask含眼; 眼区像素已=原生源) → frame_buffer
```

- 耦合变化：GazeCorrector 从单输入变双输入（源/贴回分离）；BatchPreloader 新增 pristine
  crop 产物。均为既有模块内聚扩展，无新组件、无新线程。
- **S1-E（备选 E 否决依据）**：models.py 的 Generator 前向（`test_from_audio_pose_image`）
  与 face_repair 的 GPEN ONNX 均为整 256² crop 单次前向，无区域掩码/跳过机制；"被丢弃的
  眼部计算"（Generator 眼渲染 + GPEN 眼修复）在模型内部不可分离——除非模型手术（超 scope）。
  E 的诉求由"贴回覆盖"以零额外代价满足，计算浪费为架构常量（gen 8.9ms 中眼部占比小、
  repair 7.2ms 全脸本就要跑）。
- SPOF：correct 异常 → 降级序：生成失败→贴原生眼（phase1 已完成）；贴回失败→维持
  repaired rendered 眼（现状观感）。不杀渲染循环（沿用 try/except+计数）。
- 回滚姿态：--gaze-correction off 即回滚（mouth-only mask，原生眼，零 gaze 成本）；
  行为回滚（保留 gaze on 但回旧观感）= D 描述的 git revert 单提交。二类门。

### S2 错误与救援映射

```
METHOD/CODEPATH              | 可出错场景                     | 异常/表现
-----------------------------|-------------------------------|------------------
correct(source,target,...)   | facemark NaN / state=1/2      | (无异常,旁路→贴原生眼)
correct 生成步                | 模型前向异常/显存不足          | RuntimeError→降级原生眼
correct 贴回步                | 坐标越界                       | clamp+防御（既有）
pristine crop 缺失            | disk-fallback 帧/prefetch 未带 | 无异常→就地 prepare_crop 降级+计数
ori tensor 上传               | CUDA OOM                      | RuntimeError→该帧跳过贴回（现状观感）
ckpt/graph                   | 缺失/驱动                      | fail-fast / eager 回退（既有）
```

```
异常                  | 救援? | 动作                       | 用户所见
----------------------|-------|----------------------------|------------------
生成异常              | Y     | 降级原生眼+error_skips     | 无（眼=原生）
pristine 缺失         | Y     | 就地 prepare_crop+计数     | 理论上可能有回绕漂移（单帧）
上传失败              | Y     | 跳过贴回+计数              | 该帧 Generator 眼
```
无 catch-all 新增。无 CRITICAL GAP。

### S3 安全与威胁模型

无新端点、无新外部输入、无新依赖、无密钥。pristine crop 为进程内 CPU 缓冲。无发现。

### S4 数据流与边界用例

| 交互 | 边界用例 | 处理 |
|------|----------|------|
| 每帧 correct | 眨眼(state=1)/检测失败(state=2) | 贴未修正原生眼（不变式） |
| 每帧 correct | facemark NaN（无 gaze_data 段） | 同上 + nan_skips |
| adaptive 门控关闭帧 | buffer<6 | 贴原生眼（生成跳过）——不变式覆盖 |
| 同 idx 双渲染 | flush 后重渲染同一帧 | pristine 源 → 幂等（测试断言） |
| 段切换 | reset + 分辨率变化 | 既有 reset 语义；pristine 缓存随批重建 |
| mode on↔off 热切 | mask epoch 失效 | off 后 mouth-only mask=原生眼（与 bypass 像素一致，无跳变） |
| gaze_data 缺失段 | 实时 facemark 回退 | 源仍=pristine ori（质量域一致），仅几何来源更噪 |

**bypass 语义变更 = 产品行为变更**（眨眼帧 Generator 眼→原生眼）：TASTE → 最终门。

### S5 代码质量

- DRY：眼几何/生成/迁移全部复用 GazeCorrector 现有实现，仅参数化源/目标；probe 脚本
  CropGazeCorrector 的既有重复不在本 scope（工具脚本）。
- 命名：correct(source_256, target_256, ...) 直述；pristine 命名沿用 ENG A-3 语境。
- 复杂度：新增分支 ≤2（pristine 缺失降级、生成降级）。无发现（R1/R2/R3 修正已并入）。

### S6 测试图

```
新代码路径:
  贴回隔离        : 非眼区像素逐位==target 输入 → 单测 ★
  双输入不污染    : source/target 输入前后逐位相等 → 单测 ★（G4 强化版）
  bypass=原生     : state=1/2/门控关闭 → 眼区==ori 原生 → 单测
  同 idx 幂等     : pristine 源下双渲染眼区逐位相等 → 单测 ★
  on/off 可见性   : gaze on/off 眼区差>阈值、圆外逐位等 → 单测（E6 移植）
  降级链          : 生成异常→原生眼；pristine 缺失→就地降级+计数 → 单测
  顺序接线        : gaze 调用点在 repair 之后 → render_loop 测试更新
```
凌晨 2 点放心发布的测试：贴回隔离 + 同 idx 幂等。敌意 QA：连续 NaN、eye_size=0、
prefetch 切换后首帧、graph capture 失败。金字塔：多单测 + 1 目检集成（T0 混合域出片）。

### S7 性能

修正后预算（CEO 复核）：增量 = ori H2D 0.3ms + uint8↔float 0.2ms ≈ **+0.5ms/帧**；
产能 ~50 → ~48-49fps，put 背压 20ms 度量的富余不变。gaze p50 2-3ms 与 p99 由既有
timing 直方图实测验收。原稿 3-4ms/44fps 为重复计入（子代理发现 2 成立，已修正）。
显存：pristine crop 为 CPU 缓冲（+~60MB 双槽），GPU 增量为零新增常驻。无 N+1 类问题。

### S8 可观测性

既有 gaze_frames 计数 + timing gaze 键继续有效（语义注解更新）；新增降级路径计入
error_skips（生成）/独立 pristine-miss 计数（并入 nan/error 之外的 debug 字段，P2 可选）。
RUNBOOK 增补：不变式说明（gaze on ⇒ 眼=原生源）、接缝目检指引、D 回滚路径。无 CRITICAL GAP。

### S9 部署与回滚

默认行为变更仅发生在 gaze≠off 的运行（gaze off 用户零影响）。部署序：probe 复验 →
实施 → 单测 → 混合域 T0 目检（硬验收）→ 切流。回滚：off / git revert。单实例无风险窗口。
验证清单：启动日志 → 单测 → probe 复验 SHA 记录 → T0 出片（接缝+bypass 专查）→ adaptive 压测。

### S10 长期轨迹

可逆性 4/5（行为由 flag 门控；corrector 契约为内部 API）。技术债：+1 项（贴补丁架构
复利风险——F 路线图对冲；X3 全脸审计）。路径依赖：中性——pristine crop 缓存是后续
任何区域级增强（眉毛/表情）的通用地基。12 个月新工程师读 PIPELINE_MAP + 本记录即懂。

### S11 设计与 UX — 跳过（无 UI 范围）

## CEO 双声部（Step 0.5）

CODEX 不可用（not_authed）→ [subagent-only]。

### CLAUDE SUBAGENT（CEO — 战略独立挑战）核心发现（5 项 + 补充）

1. **[HIGH] 问题框定不完整**：P3 洞察只被用来换路由；"质量批准必须在生产域执行"应固化为
   协议；全脸身份审计应列为后续调查。→ 采纳：T0 验收改生产混合域出片（R3）；全脸审计 → TODOS(X3)。
2. **[HIGH] probe 时效性 + 混合域接缝 + 预算算术不自洽**：→ 全部采纳：probe 重跑（R3）、
   接缝目检（R3）、**预算算术错误确认为主审笔误（重复计入既有 gaze 成本），已修正（R1）**。
3. **[HIGH] 6 个月后悔**：被丢弃的眼部计算 → S1-E 否决 E 并写明理由（模型无区域开关）；
   过期 probe → 复验任务；贴补丁复利 → F 进路线图 + 保持薄可逆。
4. **[MEDIUM] 备选裁决**：D 写为回滚路径（R2 采纳）；E 给出明确否决理由（R2 采纳）；F 进路线图。
5. **[MEDIUM] 竞争风险（生成时条件化）**：→ TODOS 战略项；本方案保持贴回薄、可逆。
6. 补充（bypass 产品级变更）→ TASTE 移交最终门 + bypass 帧连续性纳入目检（R3）。

### CEO DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                   部分    N/A   N/A（outside 不可用，永不 CONFIRMED）
  2. Right problem to solve?           是      N/A   N/A
  3. Scope calibration correct?        是      N/A   N/A
  4. Alternatives sufficiently explored? 否→已补 N/A  N/A（E/F/D 补齐后成立）
  5. Quality risks covered?            补齐后是 N/A   N/A
  6. 6-month trajectory sound?         是(带对冲) N/A N/A
═══════════════════════════════════════════════════════════════
```
单声部：声部 5+1 项发现全部复核——6 项成立采纳（1/2/3/4/5/补充），预算算术为主审笔误当场修正。
单声部 critical 无；HIGH 3 项已全部转为修正/条件。

## NOT in scope（含理由）

- 备选 E（条件化关闭眼部生成）：模型无区域级开关，不可行（S1-E）
- 备选 F（Generator 眼部忠实度微调）：根因层解法但超本季 scope → TODOS
- X3 全脸身份保真审计：超半径 → TODOS
- 生成时视线条件化（战略）：超半径 → TODOS
- ori crop GPU 批量预载优化：+0.3ms/帧非瓶颈 → TODOS
- dlib 实时检测路径：与实时管线根本冲突（既有结论不变）

## What already exists（复用清单）

见 0B。关键五项：prepare_crop 几何、GazeCorrector 内部逻辑全量、gaze_data 预计算、
repair_gpu 独立输出张量、eyes_enabled mask 结构。

## Dream state delta

本方案落地后：眼像素域与用户批准域对齐（一次生成+原生源），GPEN 不再二次污染眼部；
pristine crop 缓存为后续区域增强提供通用地基。距 12 个月理想态剩余：生成时条件化/忠实化
（F/战略项）、动态 gaze 时序、插件化管理——路径清晰，贴回机制保持可逆不留死结。

## Failure Modes Registry

```
CODEPATH              | FAILURE MODE          | RESCUED? | TEST? | USER SEES?        | LOGGED?
----------------------|-----------------------|----------|-------|-------------------|--------
生成步异常            | 显存/驱动             | Y(原生眼)| Y     | 眼=原生           | Y+计数
facemark NaN/state1/2 | 预处理数据            | Y(原生眼)| Y     | 眼=原生           | Y
pristine 缺失         | disk-fallback帧       | Y(就地降级)| Y   | 单帧可能漂移      | Y(计数)
贴回坐标越界          | facemark 漂移         | Y(clamp) | Y     | 轻微错位(最坏)    | N(无害)
graph capture         | 驱动不支持            | Y(eager) | Y     | 无                | Y
混合域接缝            | ori眼/GPEN脸色差      | 部分(目检)| Y(目检) | 可感知接缝      | N/A
ckpt 加载             | 缺失/损坏             | Y(fail-fast) | N/A | 启动报错         | Y
```
CRITICAL GAP: 0

## Implementation Tasks（CEO 阶段）

- [ ] **T1 (P1, human: ~2h / CC: ~10min)** — sequence_frame_pack — pristine ori_crop_256 预载
  - Surfaced by: 0E HOUR2-3 / S1 / 既有 blend 回绕漂移
  - Files: realtime_serve/sequence_frame_pack.py（_decode_frame 带 meta 坐标出 pristine crop；窗口加载与 publish_prefetch 双路径）
  - Verify: pytest 新增 pristine 存在性/数值用例
- [ ] **T2 (P1, human: ~3h / CC: ~15min)** — gaze_correction — correct 双 tensor 契约
  - Surfaced by: 0E HOUR1 / S1 / G4 强化
  - Files: realtime_serve/gaze_correction.py
  - 要点: correct(source_256, target_256, facemark, gaze_frame)；μ/σ 参照切换与接缝观察写入实现注释；bypass=贴未修正原生眼（用平滑后中心）；不修改任一输入
  - Verify: pytest tests/test_gaze_correction.py 扩展
- [ ] **T3 (P1, human: ~1h / CC: ~10min)** — render_loop — 调用点后移 + 转换链
  - Surfaced by: §3 / S1 数据流
  - Files: realtime_serve/render_loop.py
  - 要点: gen→uint8→repair→uint8转float RGB→gaze(源=ori GPU float,目标=repaired)→uint8→下载→blend（blend 可选改用 pristine crop=X1）；timing gaze 键保持
  - Verify: pytest tests/test_t5_render_loop.py 更新
- [ ] **T4 (P1, 实施前置, human: ~0.5h / CC: ~5min)** — probe 复验当前 checkpoint
  - Surfaced by: 声部发现 2（probe 时效性）
  - Files: 无代码；产出 probe report + 权重 SHA 记录进本计划
  - Verify: 1.8x/29x 结论在现权重上复现（或触发重新裁决）
- [ ] **T5 (P1, human: ~3h / CC: ~15min)** — tests — 六项断言
  - Surfaced by: S6 测试图
  - Files: tests/test_gaze_correction.py, tests/test_t5_render_loop.py
  - 要点: 贴回隔离★/双输入不污染★/bypass=原生/同idx幂等★/on-off可见性/降级链
  - Verify: pytest 全套
- [ ] **T6 (P1, 硬验收, human: ~1h / CC: ~10min)** — 生产混合域 T0 目检
  - Surfaced by: 声部发现 1/2 + 补充
  - 要点: 真实资产出对比视频（现管线全链路），专查接缝/bypass 连续性/facemark 抖动
  - Verify: 用户目检签字
- [ ] **T7 (P2, human: ~0.5h / CC: ~5min)** — RUNBOOK + PIPELINE_MAP 文档更新
  - Surfaced by: S8/S9
  - Verify: 对照清单 diff

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|---------|
| D1 | CEO | 模式 SELECTIVE EXPANSION | autoplan override | — | 延续性工程决策 | — |
| D2 | CEO | 0C-bis 选 B（眼源切换+修复后贴回） | Mechanical | P1/P3 | 可行域内完整度最高、半径最小 | A(实测否决)/C(B覆盖)/D(转回滚)/E(不可行)/F(TODOS) |
| D3 | CEO | X1 pristine crop 供 blend 复用并入 | Mechanical | P2 | 同数据半径内修既有漂移缺陷 | — |
| D4 | CEO | X2 status 新计数不并入 | Mechanical | P3 | 既有计数已覆盖 | — |
| D5 | CEO | X3 全脸审计/F 微调/战略条件化 → TODOS | Mechanical | P3 | 超半径 | — |
| D6 | CEO | probe 重跑为实施前置（T4） | Mechanical | P1 | 声部发现2成立 | — |
| D7 | CEO | 预算算术修正 R1（3-4ms/44fps→0.5ms/48-49fps） | Mechanical | P5 | 主审笔误，声部复核实证 | — |
| D8 | CEO | bypass=原生眼不变式 → TASTE 留最终门 | Taste | — | 产品行为变更，用户裁决 | — |
| D9 | CEO | 用户原始方向（先修正再推理）→ USER CHALLENGE 移交最终门 | USER CHALLENGE | — | 前提 P1/P2 被 probe 实测否决 | — |

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"77e8f8abfee5284149ed32d061f1186722a61064e2fdf46627fea28ae8dae7b4","replacements":[{"oldText":"- 预算增量：ori 256² 上传 ~0.3ms + uint8↔float 转换 ~0.2ms + 既有 gaze 2-3ms ≈ 共 3-4ms，\n  产能 ~50fps → ~44fps，余量充足。","newText":"- 预算增量：ori 256² H2D 上传 ~0.3ms + uint8↔float 转换 ~0.2ms（贴回与生成推理成本已在\n  既有 gaze 2-3ms 内，不重复计）。产能 ~50fps → ~48-49fps（CEO 复核修正：原稿 3-4ms/~44fps\n  系重复计入既有 gaze 成本，已更正；p99 以 timing 直方图实测为准）。"},{"oldText":"- D（不动流程，只调 GPEN α / 时序平滑）：不解决\"源头是劣化眼\"的根因。","newText":"- D（不动流程，只调 GPEN α / 时序平滑）：不解决\"源头是劣化眼\"的根因；**定位为本案 B 的\n  回滚路径**——若混合域接缝/抖动未过目检，回退到现状（gaze on 现行为）即 D。\n- E（条件化关闭 Generator 眼部生成以省下被丢弃的眼部计算）：**否决**——Generator 与 GPEN\n  均为整 crop 单次前向，无区域级开关，眼部计算在模型内部不可分离（见 Review record S1-E）。\n- F（微调 Generator 眼部忠实度——根因层解法）：→ TODOS 战略路线图；本方案定位为战术止血，\n  保持贴回机制薄、可逆。"},{"oldText":"- 验收: 眼区可见性断言（gaze on/off 像素差）、bypass 帧眼=原生、缓冲不污染、\n  真实资产目检对比视频（T0 协议）、timing 直方图 gaze 键 p99 实测。","newText":"- 验收: 眼区可见性断言（gaze on/off 像素差）、bypass 帧眼=原生、缓冲不污染、\n  真实资产目检对比视频（T0 协议，**在生产混合域出片**——专查 ori 眼/GPEN 脸接缝与\n  bypass 帧连续性）、timing 直方图 gaze 键 p99 实测、同 idx 双渲染幂等断言\n  （pristine 眼源）、**实施前置：在当前部署 checkpoint 重跑 gaze_crop_probe 并记录权重\n  SHA**（2026-09-03 的 1.8x 抹除结论需对现模型复验）。"}]} -->

<!-- autoplan-accepted:ceo -->
- 采用路线 B：眼源切换（gaze 生成源 = orifra 原生眼 ori_crop_256，256 crop 域）+ 贴回时机后移至 face_repair 之后（GPEN 不再处理最终可见眼像素）。compositor mask 结构、CLI、HTTP 契约、Audio2Lip/Generator/repair 推理栈全部不动。验证：T5 六项单测 + T6 生产混合域 T0 目检（硬验收）。
- correct 新契约：correct(source_256, target_256, facemark, gaze_frame)；source=原生眼源（pristine ori_crop_256 GPU float [-1,1] RGB），target=修复后 rendered；不修改任一输入，返回新张量；μ/σ 颜色迁移参照与贴回目标域的接缝表现写入实现注释并在 T6 目检裁决。
- bypass 不变式：gaze on（含 adaptive 门控关闭帧、眨眼 state=1、检测失败 state=2）⇒ 最终帧眼睛一律原生源——生成步被门控/旁路时贴未修正原生眼（使用平滑后眼中心）。此为产品行为变更，标注 TASTE 留最终门确认。
- pristine 眼源：BatchPreloader 解码期并行产出 pristine ori_crop_256（窗口加载 + publish_prefetch 双路径）；disk-fallback 帧就地 prepare_crop 降级并计数；pristine crop 同时供 compositor.blend 使用（顺带修复既有 blend 回绕漂移，X1）。
- 实施前置（T4）：在当前部署 checkpoint 上重跑 realtime_serve/gaze_crop_probe.py 并记录权重 SHA；若 1.8x 抹除结论在现权重上不复现（Generator 已保留输入端修正），回到最终门重新裁决路线。
- 验收全集：贴回隔离断言（非眼区逐位==target）；双输入不污染；bypass 帧眼==原生；同 idx 双渲染幂等（pristine 源）；gaze on/off 眼区可见性；降级链（生成异常→原生眼、pristine 缺失→就地降级+计数）；gaze 键 p99 timing 实测；生产混合域 T0 出片（接缝 + bypass 连续性 + facemark 抖动专查）并经用户目检签字。
- 性能预算（修正后）：增量 ~+0.5ms/帧（ori H2D 0.3ms + uint8↔float 0.2ms），产能 ~50→~48-49fps；若实测 p99 使产能跌破 40fps 则回到最终门复议。
- 回滚路径：D（现状行为）为 B 的行为回滚；--gaze-correction off 为功能回滚；均为二类门。
- 备选 E 否决存档：Generator/GPEN 均为整 crop 单次前向、无区域级开关，"被丢弃的眼部计算"不可分离。
- 延迟项进 TODOS：F（Generator 眼部忠实度微调）、X3（全脸身份保真审计）、生成时视线条件化（战略）、ori crop GPU 批量预载。
<!-- /autoplan-accepted:ceo -->


---

# DX 审查（Phase 2.5）— 模式: DX POLISH（autoplan override）

产品类型：本机运营 + 嵌入式推理服务（AI-Vtuber 经 HTTP/facade 集成）。主 persona：solo operator（开发者本人，CLI 启动、虚拟摄像头供直播/Unity 消费）；次 persona：嵌入方集成者（HTTP 契约消费者）。

## 开发者旅程图（9 阶段）

```
发现→评估→安装→hello world→集成→调试→升级→扩展→迁移
 ✓   ✓   ✓   ⚠probe命令块  ✓   ⚠计数名  ⚠eye_source  N/A  N/A
                          (发现2)   (发现6)  (发现1)
```

## 开发者共情叙事（第一人称）

我是这个服务的运营者。gaze on 跑了几周，弹幕开始说"眼睛不像本人了"。我打开计划文档想确认优化方案——读到第 89 行才看到"选了路线 B"，前面全是 Route C'/ENG A-3/X1 这类我 half-记得的代号。我要实施：第一道闸是"重跑 probe"，但文档没给我完整命令行和判定阈值——1.8x 和 29x 之间多少算复现？我猜。我跑完实施后发现混合域有轻微接缝，想临时退回旧眼源对比——文档承诺"D 为行为回滚"，但没告诉我怎么在运行时选到 D，只能 git revert 重启。这就是发现 1/2/3/5 的来源，全部已列为验收项。

## 竞争基准（参考基准，内部服务无公开竞品 TTHW）

```
Tool               | TTHW     | Notable DX Choice        | Source
Stripe（参考）      | 30s      | 一条 curl 出真数据        | 参考基准
Vercel（参考）      | 2min     | push 即部署              | 参考基准
本服务（现状）      | ~2-3min  | probe 脚本可复制粘贴      | 本次评审
本服务（修复后目标）| ≤3min    | T4 命令块+阈值+TL;DR      | 本计划
```

## 魔法时刻

瞳孔在对比视频里转向正视的那一刻（T0 协议已验证有效）。交付载体：copy-paste probe 命令（既有 gaze_quality_probe/gaze_crop_probe 模式延续）——发现 2 的命令块补齐后此载体完整可用。

## 8 pass 评分（证据锚定）

| Pass | 分 | 证据与差距 | 修复 |
|---|---|---|---|
| 1 Getting Started | 8→9 | T4 是第一道闸但无命令块/阈值（发现2）；模型加载 ~1-2min 为物理地板 | T4 命令块+数值阈值 |
| 2 API/CLI/SDK | 8→9 | eye_source/canvas 命名陷阱（发现4）；回滚无运行时选择器（发现1） | 更名+flag |
| 3 Error Messages | 7→8 | 降级链行为齐备但无观测名（发现6）；T4 出口有阈值（好） | pristine_fallback 计数 |
| 4 Documentation | 6→8 | 零可复制示例；代号无词汇表；裁决埋第 89 行；状态行陈旧（发现2/3/5） | TL;DR+§6/§7+命令块 |
| 5 Upgrade Path | 8→9 | off 开关齐备；eye_source 增量字段向后兼容 | 发现1 落地 |
| 6 Dev Environment | 8 | pytest 基建 + probe 脚本 + /status 观测在位 | — |
| 7 Community | 7 | 内部服务：PIPELINE_MAP/GAZE 文档链齐备，团队可发现 | — |
| 8 DX Measurement | 6→8 | timing 直方图在；目检判据未预置（发现7）；boomerang=/devex-review 可测 | T6 判据预置 |

## 双声部发现整合

CODEX 不可用（not_authed）→ [subagent-only]。CLAUDE SUBAGENT（DX）7 项发现主审复核全部成立：
1 [高] 回滚 D 无运行时选择器 → **采纳**：eye_source 运行时参数（rendered 模式=source=canvas=修复后 rendered；旧顺序逐位复刻仅 git revert 可达，已注明）
2 [中] T4 不可复制粘贴 → **采纳**：命令块+阈值（复现=输入端倍数 <5x 且 C' 端 >10x；输入端 ≥10x→回最终门）
3 [中] 代号词汇表 → **采纳**：§7
4 [中] correct 契约命名陷阱 → **采纳**：更名 eye_source/canvas + canvas 域守卫断言
5 [中] 裁决块埋没/状态陈旧 → **采纳**：§6 标题 + TL;DR + 状态行更新
6 [中] 降级计数无观测名 → **采纳**：/status gaze_frames.pristine_fallback + 日志 `[GazeCorrector] pristine fallback`
7 [低] T6 判据未预置 → **采纳**：3 组资产 100% 缩放接缝不可见、bypass 序列连续；不达标→eye_source=rendered 或 D

## DX DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          是      N/A   N/A（outside 不可用）
  2. API/CLI naming guessable?         修复后是 N/A   N/A
  3. Error messages actionable?        修复后是 N/A   N/A
  4. Docs findable & complete?         修复后是 N/A   N/A
  5. Upgrade path safe?                是      N/A   N/A
  6. Dev environment friction-free?    是      N/A   N/A
═══════════════════════════════════════════════════════════════
```
单声部：7 项发现全部采纳；无单声部 critical。

## DX SCORECARD
```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Getting Started      | 9/10  | ↑（T4 命令块）                      |
| API/CLI/SDK          | 9/10  | ↑（更名+flag）                      |
| Error Messages       | 8/10  | ↑（观测名）                         |
| Documentation        | 8/10  | ↑（TL;DR/§6/§7）                    |
| Upgrade Path         | 9/10  | ↑（eye_source）                     |
| Dev Environment      | 8/10  | —                                   |
| Community            | 7/10  | —（内部服务口径）                   |
| DX Measurement       | 8/10  | ↑（T6 判据）                        |
+--------------------------------------------------------------------+
| TTHW                 | ~2-3min → ≤3min（Competitive 层达标）        |
| Overall DX           | 7.25/10 → 8.5/10                             |
| Mode                 | DX POLISH                                    |
+====================================================================+
```

## NOT in scope（DX）
- /status 增加 eye-source 分解计数（CEO X2 已裁）：既有计数语义注解即可
- probe 输出 JSON 化：文本 report 已够 T4 判定
- 嵌入方 SDK/类型存根：超半径

## What already exists（DX）
probe 双脚本（T0 协议载体）、/status gaze_* 观测面、三段式错误映射（app.py _map_control_errors）、RUNBOOK 排查指引、EMBEDDING.md additive-only 契约。

## Implementation Tasks（DX 阶段）

- [ ] **DX-T1 (P1, human: ~1.5h / CC: ~10min)** — eye_source 运行时选择器
  - Surfaced by: DX 发现1（回滚承诺落地）
  - Files: gaze_correction.py / render_loop.py / main.py / config.py / service.py / api/app.py
  - 要点: 构造参数 --gaze-eye-source {ori,rendered} 默认 ori；/gaze/params 增补 eye_source 字段（拒绝式校验，additive）；rendered 模式=source=canvas=修复后 rendered
  - Verify: pytest 热切用例 + /status gaze_params 回显
- [ ] **DX-T2 (P2, human: ~1h / CC: ~5min)** — 可观测性补全
  - Surfaced by: DX 发现4/6
  - 要点: canvas 域守卫断言（shape/device/域，失败→降级原生眼+计数）；gaze_frames.pristine_fallback 计数 + 日志 `[GazeCorrector] pristine fallback`
  - Verify: pytest 守卫用例；/status 字段断言

（DX 发现2/3/5/7 的文档侧修复已由本轮基线修订直接落入计划 §0/§6/§7 与 T6 判据，不另立任务。）

## Decision Audit Trail（DX 续）

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|---------|
| D10 | DX | eye_source 运行时选择器并入（发现1） | Mechanical→TASTE 留门 | P1 | 回滚是硬承诺，机制必须落地 | 仅 git revert |
| D11 | DX | correct 更名 eye_source/canvas（发现4） | Mechanical | P5 | 语义直述优于易误读命名 | source/target 保留 |
| D12 | DX | T4 命令块+数值阈值（发现2） | Mechanical | P1 | 第一道闸必须可执行可判定 | 定性裁决 |
| D13 | DX | 词汇表/TL;DR/§6/状态行（发现3/5） | Mechanical | P1 | 文档可导航性 | — |
| D14 | DX | pristine_fallback 观测名（发现6） | Mechanical | P1 | 不可观测=不可运维 | — |
| D15 | DX | T6 判据预置（发现7） | Mechanical | P1 | 防目检标准漂移 | — |

<!-- autoplan-baseline-edits:dx {"sourceSha256":"2a8e46a126a4cf03597e115be6d0cd50dc929ac59bf7e5ab52eab99f7b4e7e6e","replacements":[{"oldText":"状态: /autoplan 审查中 | 分支: main | 日期: 2026-09-21","newText":"状态: /autoplan 评审中（CEO/DX 已裁决，Eng 待审）| 分支: main | 日期: 2026-09-21\n\n> **TL;DR：裁决 = 路线 B（眼源切换 + 修复后贴回）；用户原案（先修正再推理）被 probe 实测否决（§2/§4）。运行时选择器 `--gaze-eye-source ori|rendered`（默认 ori）落地行为回滚承诺（DX 发现1）。术语表见 §6。**"},{"oldText":"- 修改: gaze_correction.py（correct 双 tensor 契约：源=ori、贴回=target）、","newText":"- 修改: gaze_correction.py（correct 契约更名 eye_source/canvas 双 tensor——DX 发现4；眼源运行时选择器 ori|rendered 默认 ori）、"},{"oldText":"- 不动: compositor mask 结构、CLI、HTTP 契约、Audio2Lip/Generator/repair 推理栈。","newText":"- 不动: compositor mask 结构、Audio2Lip/Generator/repair 推理栈。CLI/HTTP 仅**增量**扩展：新增 --gaze-eye-source 旗标与 /gaze/params 的 eye_source 字段（向后兼容，DX 发现1）。"},{"oldText":"对现模型复验）。","newText":"对现模型复验）。\n\n## 6. 术语表\n\n- Route C'：GAZE_CORRECTION_PLAN.md 定案的注入点——视线纠正挂在 Generator 输出端。\n- count：资产目录 count/<段>/（256² 头部裁剪帧，Generator 唯一输入；最终帧非嘴部像素不采用其输出）。\n- pristine：未经 blend 原地改写的原始副本（防循环回绕后的二次修正漂移）。\n- T0/T4-T7：任务编号；T0 亦指 GAZE_CORRECTION_PLAN 的质量目检协议（对比视频 + 用户签字）。\n- 二类门：可逆决策（有开关/回滚路径可撤销）。\n- EMA：指数滑动平均（α 为新帧权重）。\n\n## 7. 裁决与验收\n\nCEO 阶段裁决与验收义务块（不可变；DX 阶段补充见 Review record 的 dx 义务块）："}]} -->

<!-- autoplan-accepted:dx -->
- eye_source 运行时选择器（DX 发现1）：构造参数 --gaze-eye-source {ori,rendered}，默认 ori；/gaze/params 增补 eye_source 字段（拒绝式校验，additive 兼容）；rendered 模式语义 = source=canvas=修复后 rendered（注：与旧管线顺序不逐位等价，旧顺序行为仅经 git revert 可达——回滚语义已在计划中注明）。验证：热切 pytest 用例 + /status gaze_params 回显。
- correct 契约命名（DX 发现4，替代 CEO 块中的 source_256/target_256 命名，语义不变）：correct(eye_source, canvas, facemark, gaze_frame)——眼从 eye_source 读、贴进 canvas、返回新张量；canvas 域守卫断言（shape/device/[-1,1] 域检查）失败→降级原生眼+计数。
- T4 可执行化（DX 发现2）：命令 `python -m realtime_serve.gaze_crop_probe --character-dir wy --segment 1 --frames 3000,3010,3020,3030`（默认 ckpt 路径）；判定阈值：输入端 eye_diff 倍数 <5x 且 C' 端 >10x ⇒ 抹除结论复现，方案成立；输入端 ≥10x ⇒ 修正开始穿透 Generator ⇒ 回最终门重新裁决。权重 SHA 提取：`python -c "import hashlib;print(hashlib.sha256(open('wy/checkpoint/new.pt','rb').read()).hexdigest())"`。
- 文档导航（DX 发现3/5）：TL;DR 置顶、§6 裁决与验收标题、§7 术语表（Route C'/count/pristine/T 编号/二类门/EMA）、状态行反映评审进度——已由基线修订落地。
- 可观测性（DX 发现6）：降级计数命名 gaze_frames.pristine_fallback（/status）+ 日志行 `[GazeCorrector] pristine fallback`；canvas 守卫失败计入 error_skips 并首帧打栈（沿用既有模式）。
- T6 目检判据预置（DX 发现7）：3 组代表资产、100% 缩放逐帧下接缝不可见（无色差台阶/几何错位）、bypass 帧序列连续无跳变；不达标处置：先切 eye_source=rendered 复测，仍不达标→D（现状行为）。
<!-- /autoplan-accepted:dx -->


---

# 工程审查（Phase 3）— 模式: FULL_REVIEW（autoplan，最终门前的收官审查）

## Step 0 范围挑战（autoplan: 不缩减）

- 复杂度检查：4-6 文件（gaze_correction / render_loop / sequence_frame_pack / compositor 可选 / main+config+service+api 增量）+ 测试文件，0 新类 → 低于 8 文件阈值，通过。
- 子问题→现有代码映射经声部代码核实（14 次工具调用）全部成立：render_loop.py:730-742 现顺序、gaze_correction.py:158-225 状态机、**compositor.py:202-204 的"安全"注释失实**（sequence_frame_pack.py:162-175 ensure_loaded 快速路径不重载常驻批——X1 污染前提确认且比计划陈述更严重：任何帧数落在双槽窗口内的段回绕后直接复用被污染数组）。
- 最小改动集确认；范围不缩减。

## S1 架构审查

```
改造后逐帧数据流（gaze on，双调用点分流 — ENG F1a）:
BatchPreloader ─ orifra/crop/meta/facemark/[新]pristine ori_crop_256 ─▶ RenderLoop
eye_source == 'rendered'（D 语义，逐位复现现状）:
  gen(256² float) → correct(eye_source=rendered, canvas=rendered) [旧位置, repair 前]
    → uint8 → GPEN(整crop) → 下载 → blend
eye_source == 'ori'（本案 B）:
  gen → uint8 → GPEN(整crop) → uint8转float RGB
    → correct(eye_source=pristine_ori, canvas=repaired) [repair 后]
       门控 _gaze_allowed() 只包 GPU 生成段；贴原生眼段不受门控（ENG F2）
    → uint8 → 下载 → blend（pristine crop 供 blend，X1）
```
- 耦合增量：correct 四参双 tensor 契约 + corrector 内部两段式（生成/贴回）。贴回路径单点收敛在 corrector 内（F2 的结构保障）。compositor/mask 零改动。
- SPOF：correct 异常降级链不变（生成失败→贴原生眼→维持 canvas 眼）。
- 回滚：功能=off；行为=rendered 模式（真二类门，F1a 落地——修复 DX 块中"旧顺序仅经 git revert 可达"的缩水）。
- 10x 负载（F10）：+0.5ms 线性成立；非线性风险=解码池每帧多一次 256² resize → 段切换/预取并发时窗口加载变慢 → disk-fallback（~25ms/帧）概率上升。处置：pristine_fallback 与既有 fallback 联合告警阈值（/status），不改方案。

## S2 代码质量

- 命名 eye_source/canvas ✓（DX D11）；eye_source 校验下沉 owner 层（F9，复刻 M17 模式 gaze_correction.py:120-134——HTTP 层只是第一道）。
- DRY：probe 脚本 CropGazeCorrector 重复不在 scope（工具脚本，既有）。
- 失实注释修正：compositor.py:202-204（E-T8，防误导后来者）。
- 复杂度：corrector 新增 bypass-only 分支 + 状态机连续性推进（F3），分支增量可控。

## S3 测试审查（never skip）

框架：pytest（tests/ 既有 164 passed）。

```
新代码路径 → 覆盖图（★=质量）:
[+] gaze_correction.correct(eye_source, canvas, facemark, gaze_frame)
  ├ 双输入不污染（source/canvas 输入前后逐位相等）        [GAP→U2 ★★★]
  ├ 贴回隔离（非眼区逐位==canvas）                        [GAP→U1 ★★★]
  ├ bypass-only 分支（门关帧贴原生眼、状态机推进、跳过forward）[GAP→U7 ★★★ ENG F2/F3]
  ├ canvas 域守卫（形状/设备/域失败→降级原生眼+计数）      [GAP→U8 ★★ DX-T2/F9]
  ├ legacy _fixed_eye_size None 守卫（段首即门关）         [GAP→U9 ★★ ENG F3]
  └ 通道序（R≠B 合成帧，色相跟随 eye_source 域）           [GAP→U10 ★★★ ENG F6]
[+] render_loop 调用点分流（ENG F1a）
  ├ rendered 模式逐位复现现状（回归断言）                  [GAP→U11 ★★★ REGRESSION RULE]
  ├ ori 模式 timing 键含上传/转换/correct/贴回全程         [GAP→U12 ★ ENG F8]
  └ adaptive 门关帧最终眼==原生源                         [GAP→并入 U7 断言 ENG F2]
[+] sequence_frame_pack pristine 生产
  ├ 窗口加载 / publish_prefetch 双路径 pristine 存在性     [GAP→U13 ★★]
  ├ pristine 与 orifra 无内存别名（np.shares_memory，含 fallback 强制 copy）[GAP→U14 ★★★ ENG F5]
  └ 同 idx 双渲染：输入幂等 + reset() 后输出幂等           [GAP→U15 ★★ ENG F4 语义修正]
[+] eye_source 参数链（CLI/HTTP/热更/owner 校验）          [GAP→U16 ★★]

COVERAGE: 0/14 新路径已有测试（全部为计划新增路径）| GAPS: 14 → 并入 T5 扩展（E-T5）
幂等断言语义修正（ENG F4）：pristine 保证的是输入纯净而非输出幂等——_prev_gen 时序平滑使
同 idx 双渲染输出必然不同（by design）。断言死写为：(a) eye_source 输入跨渲染逐位不变；
(b) 两次渲染间 reset() 后输出逐位相等。照字面断言输出幂等的测试必假失败。
回归规则：U11（rendered 模式逐位复现现状）为 REGRESSION RULE 强制项——新契约下既有行为的
唯一防线，无豁免。
```

测试计划产物落盘：`~/.gstack/projects/Eikwang-EDTALK/admin-main-eng-review-test-plan-20260921-153000.md`

## S4 性能

- 预算复核：+0.5ms（196KB H2D + 256² uint8↔float），20ms/帧 → 48.8fps，与 48-49fps 表述一致 ✓。
- F8 测量边界：gaze timing 键 = eye_source 上传 + 域转换 + correct + 贴回全程（repair 输出→D2H 之间）——否则"跌破 40fps 回最终门"的复议触发器系统性失灵。已并入 §5（基线修订）。
- F10 解码池：观测注记（联合告警阈值）。无其他 N+1/内存问题（pristine CPU +~60MB 双槽已在案）。

## Failure Modes Registry（Eng 更新）

```
CODEPATH                | FAILURE MODE            | RESCUED? | TEST? | USER SEES?       | LOGGED?
------------------------|-------------------------|----------|-------|------------------|--------
门关帧贴回(F2)          | 门控整体短路违反不变式    | Y(结构)  | U7    | 眼=原生           | Y
门重开鬼影(F3)          | _prev_gen 陈旧           | Y(失效)  | U7    | 短暂鬼影→消除     | N
fallback 别名(F5)       | 视图共享→当帧源污染      | Y(copy)  | U14   | 偶发接缝(消除)    | Y
通道序(F6)              | BGR/RGB 互换被 μσ 掩盖   | N→测试拦截| U10  | 眼色不对(T6才现形)| N
T4 灰区(F7)             | 5x-10x 无裁决规则        | Y(规则)  | N/A   | N/A              | Y(记录)
timing 边界(F8)         | p99 低估→复议失灵        | Y(定义)  | U12   | N/A              | Y
```
CRITICAL GAP: 0（F6 原为潜在静默域错误——μ/σ 会部分掩盖通道互换直到 T6 出片才现形；U10 拦截后归零）

## ENG DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               补后是   N/A   N/A（outside 不可用）
  2. Test coverage sufficient?         补后是   N/A   N/A
  3. Performance risks addressed?      是      N/A   N/A
  4. Security threats covered?         是      N/A   N/A
  5. Error paths handled?              补后是   N/A   N/A
  6. Deployment risk manageable?       是      N/A   N/A
═══════════════════════════════════════════════════════════════
```
单声部：声部 10 项发现主审复核全部成立（F1-F9 采纳为修正/任务；F10 为观测注记）。F1/F2 为 high——F1 采纳方案 (a) 双调用点分流；F2 采纳 bypass-only 分支结构。无 critical。

## NOT in scope（Eng 确认）

- 模型手术（区域级开关）：不可行（CEO S1-E 存档）
- F 微调 / X3 全脸审计 / 战略条件化 / ori GPU 批量预载：TODOS（auto-write）
- probe 脚本重构/JSON 化：工具脚本不入围

## What already exists（Eng 确认）

CEO 0B 清单经代码核实全部成立。新增确认：repair_gpu 独立输出张量（torch.empty 分配，无 graph 静态缓冲别名）、_gaze_allowed 门控语义（现短路整个 correct——F2 要求改结构）、M17 owner 层校验模式、ensure_loaded 快速路径不重载常驻批（X1 污染机制确认）。

## Implementation Tasks（Eng 阶段新增/修订）

- [ ] **E-T1 (P1, human: ~1h / CC: ~8min)** — render_loop 双调用点分流（F1a）
  - Files: realtime_serve/render_loop.py
  - Verify: U11 逐位回归断言
- [ ] **E-T2 (P1, human: ~2h / CC: ~12min)** — corrector bypass-only 分支 + 状态机连续性（F2/F3：门关帧贴原生眼、legacy None 守卫、门重开失效 _prev_gen）
  - Files: realtime_serve/gaze_correction.py
  - Verify: U7/U9
- [ ] **E-T3 (P1, human: ~1h / CC: ~8min)** — fallback 强制 copy + 别名断言（F5）
  - Files: sequence_frame_pack.py / compositor.py
  - Verify: U14
- [ ] **E-T4 (P1, human: ~0.5h / CC: ~5min)** — gaze timing 键边界（F8）
  - Files: realtime_serve/render_loop.py
  - Verify: U12
- [ ] **E-T5 (P1, human: ~3h / CC: ~18min)** — 测试扩展 U7-U16（并入 T5 执行）
  - Files: tests/test_gaze_correction.py, tests/test_t5_render_loop.py
  - Verify: pytest 全套
- [ ] **E-T6 (P2, human: ~0.2h / CC: ~3min)** — T4 灰区规则（F7：5x-10x 视为回最终门，保守取向）
  - Verify: 文档对照
- [ ] **E-T7 (P2, human: ~0.5h / CC: ~5min)** — eye_source owner 层校验（F9）
  - Files: realtime_serve/gaze_correction.py 或 render_loop.py
  - Verify: U16
- [ ] **E-T8 (P2, human: ~0.2h / CC: ~3min)** — compositor.py:202-204 失实注释修正（X1 落地后同步更新）

## Decision Audit Trail（Eng 续）

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|---------|
| D16 | Eng | F1 采纳 (a) 双调用点分流（D 变真二类门） | Mechanical | P1 | 完整性 + T6 需像素级 A/B | F1(b) 措辞降级 |
| D17 | Eng | F2 bypass-only 分支（门控只包生成段） | Mechanical | P1 | 不变式的结构保障 | 维持整体短路 |
| D18 | Eng | F4 幂等断言语义修正 | Mechanical | P1 | 时序平滑使输出幂等不可达 | 字面输出幂等 |
| D19 | Eng | F7 灰区即回最终门 | Mechanical | P1 | 保守取向与 T4 门性质一致 | 灰区视为复现 |
| D20 | Eng | F9 校验下沉 owner 层 | Mechanical | P5 | 复刻 M17 模式 | 仅接口层校验 |
| D21 | Eng | F10 观测注记不改方案 | Mechanical | P3 | 线性预算成立 | — |

<!-- autoplan-baseline-edits:eng {"sourceSha256":"cb9f13326f3e46059ea9fd60c03adaffa2910d57291437de3e98b46428fec7d2","replacements":[{"oldText":"- D（不动流程，只调 GPEN α / 时序平滑）：不解决\"源头是劣化眼\"的根因；**定位为本案 B 的\n  回滚路径**——若混合域接缝/抖动未过目检，回退到现状（gaze on 现行为）即 D。","newText":"- D（不动流程，只调 GPEN α / 时序平滑）：不解决\"源头是劣化眼\"的根因；**定位为本案 B 的\n  回滚路径**——若混合域接缝/抖动未过目检，回退到现状（gaze on 现行为）即 D。**运行时\n  可达性（ENG F1a）：rendered 模式的调用点回落到 repair 之前（逐位复现现状），ori 模式\n  在 repair 之后——D 经 --gaze-eye-source rendered 运行时可达，为真二类门。**"},{"oldText":"- 修改: gaze_correction.py（correct 契约更名 eye_source/canvas 双 tensor——DX 发现4；眼源运行时选择器 ori|rendered 默认 ori）、\n  render_loop.py（调用点后移 + ori tensor 转换）、","newText":"- 修改: gaze_correction.py（correct 契约更名 eye_source/canvas 双 tensor——DX 发现4；眼源运行时选择器 ori|rendered 默认 ori；门控仅约束生成段、原生贴回段不受门控——ENG F2）、\n  render_loop.py（调用点按 eye_source 分流：rendered→repair 前旧位逐位复现现状 / ori→repair 后；gaze timing 键覆盖上传+转换+correct+贴回全程——ENG F1a/F8）、"}]} -->

<!-- autoplan-accepted:eng -->
- 双调用点分流（ENG F1a，替代 DX 块中 rendered 模式"source=canvas=修复后 rendered"语义与"旧顺序仅经 git revert 可达"注记）：rendered 模式调用点回落 repair 之前（逐位复现现状，D 成为运行时可达的真二类门）；ori 模式调用点在 repair 之后。验证：U11 逐位回归断言（REGRESSION RULE 强制）。
- 门控结构（ENG F2）：_gaze_allowed() 仅约束 GPU 生成段；bypass-only 分支（贴未修正原生眼）不受门控，路径单点收敛在 corrector 内。验证：U7（含"adaptive 门关帧最终眼==原生源"断言）。
- 状态机连续性（ENG F3）：bypass 贴回统一走 corrector 内部（推进 EMA/eye_size/blink 状态、跳过 forward）；legacy 路径 _fixed_eye_size None 守卫；门重开/mode 切换失效 _prev_gen 与 _skip_run。验证：U7/U9（段首门关 N 帧→无崩溃、贴回有效、门重开无鬼影）。
- 幂等断言语义（ENG F4，修正 CEO 块"同 idx 双渲染幂等"定义）：(a) eye_source 输入跨渲染逐位不变（pristine 保证）；(b) 两次渲染间 reset() 后输出逐位相等。不要求带时序平滑的连续渲染输出幂等。
- pristine 别名防护（ENG F5）：pristine（含 disk-fallback 就地降级路径）与 orifra 无内存别名（np.shares_memory 断言）；fallback 路径强制 copy。验证：U14。
- 通道序测试（ENG F6）：R≠B 合成帧单测，断言修正眼色相跟随 eye_source 域（防 BGR/RGB 互换被 μ/σ 部分掩盖到 T6 才现形）。验证：U10。
- T4 灰区规则（ENG F7）：输入端倍数落在 5x-10x ⇒ 回最终门（保守取向，与 T4 门性质一致）；记录实测数值。
- gaze timing 键边界（ENG F8）：= eye_source 上传 + 域转换 + correct + 贴回全程（repair 输出→D2H 之间）。
- eye_source 校验下沉 owner 层（ENG F9，复刻 M17 模式）：HTTP/CLI 只是第一道。
- 观测注记（ENG F10）：pristine_fallback 与既有 fallback 联合告警阈值（/status）。
- compositor.py:202-204 失实注释修正（E-T8，X1 落地同一提交）。
<!-- /autoplan-accepted:eng -->

---

# 最终门裁决（Phase 4 CLOSED）

**用户裁决（2026-09-21）：A) 批准路线 B。** User Challenge 1（原案"先修正再推理"）接受否决——由 T4 在当前 checkpoint 重跑 probe 存档复验；两项 Taste（bypass=原生眼不变式、eye_source 运行时选择器+双调用点）全部采纳。执行序：T4（probe 复验，<5x 复现 / ≥10x 或 5x-10x 灰区→回门）→ T1-T5 + E-T1-E-T5 + DX-T1/T2 → T6 目检签字。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` Phase 1 | 范围与战略 | 1 | clean | 路线 B 9/10 胜出；probe 时效/混合域接缝/预算算术修正；D1-D9 |
| Outside Review | codex（三阶段均尝试） | 独立第二意见 | 0 | unavailable | Codex 未认证；三阶段均降级 Claude 子代理 [subagent-only] |
| DX Review | `/autoplan` Phase 2.5 | 开发者体验 | 1 | clean | 7 项发现全采纳（eye_source 选择器/T4 可执行化/契约更名/观测名）；7.25→8.5/10 |
| Eng Review | `/autoplan` Phase 2.5→3 | 架构与测试（必需门） | 1 | clean | 10 项发现全采纳（双调用点/门控结构/别名/通道序/timing 边界）；测试图 14 路径全入计划 |
| Design Review | 跳过 | 无 UI 范围 | 0 | skipped | — |

- **OUTSIDE COVERAGE:** codex 未认证（not_authed）——ceo/dx/eng 三阶段 outside_status=unavailable，design=skipped。Coverage 部分缺失，已如实记录；修复：`codex login` 后可对任何阶段重跑外部声部。
- **CROSS-MODEL:** 不适用（无已完成的外部评审；三阶段原生子代理各自独立命中回滚可达性与 T6 判据前置两主题）。
- **VERDICT:** CEO + DX + ENG CLEARED（经最终门用户裁决 A）— ready to implement（T4 probe 复验先行）

NO UNRESOLVED DECISIONS

---

# 最终门裁决 2（2026-09-21，实施后用户生产指令）：全脸统一修复

用户生产观测：ori 模式眼睛 = 原生像素未经修复，相对 GPEN 修复过的嘴/鼻偏糊
（修复网锐化增益只覆盖下半脸）。用户指令：视线修正贴回后整脸进面部修复，
再贴回原始帧——全脸统一清晰度。

**裁决：采纳。** Route B 原不变式"GPEN 不触碰最终可见眼像素"由用户显式废止；
新不变式：**眼源原生域 + 全脸统一修复**。

- 安全性先例：legacy rendered 模式数周生产即"纠正眼→GPEN"顺序，视线方向经
  修复保留（GPEN 为保结构修复网，非重绘网）；且 GPEN 输入从 Generator 劣化眼
  升级为原生眼，身份风险低于问题起源时的三重生成链。
- 结构简化：ori 与 rendered 收敛到同一调用点（repair 前），差异只剩眼源张量；
  ENG F1a 双调用点降级为单调用点分流；canvas=rendered 免 uint8↔float 往返
  （较 post-repair 变体省 ~0.2ms）。
- F8 timing 键定义更新：= pristine 上传 + 域转换 + correct（gen 输出→repair 之间）。
- U11 rendered 逐位回归不变；U7 系 corrector 级断言不受调用点影响。
- 验收：用户在生产域目检（功能运行 + 25fps 已确认；本轮验收眼清晰度均匀性
  与全脸一致观感）。
