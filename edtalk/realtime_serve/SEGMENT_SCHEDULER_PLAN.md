<!-- /autoplan restore point: /c/Users/admin/.gstack/projects/Eikwang-EDTALK/main-autoplan-restore-20260903-111801.md -->
# 段调度器方案（Segment Scheduler）

状态: /autoplan 审查中 | 分支: main | 日期: 2026-09-03

## 1. 问题陈述

服务启动后固定循环 orifra 下排第一的段，其它素材未经验证。需要段级调度能力：

1. **默认**（现状保持）：启动循环第一个段
2. **指定列表**：`--segments "1,55,开心"` 按给定顺序循环（段名以目录实际名称为准）
3. **自动循环**：`--segments auto` 按 sorted 顺序循环全部段

约束：数据加载/调度优化，不得破坏现有架构稳定（缓冲池/输出流/gaze/repair 均已就绪）。

## 2. 现状与关键机制（已核对代码）

- `CharacterDataPack.segments` = sorted(段目录名)；`switch_segment(seg)` = 快速 reset + 后台预载（非阻塞）
- `RenderLoop.switch_segment`：换段 + 重锚视频时钟（新段从第 0 帧起）+ flush 输出缓冲 + gaze reset
- 视频时钟：`video_idx = int((now - segment_start) * fps) % total`——**段播放完成检测 = elapsed ≥ total/fps**，
  调度器只需每帧比较一次，无需新时钟
- API `POST /segment/switch`：手动切单段（已存在）

## 3. 设计

### SegmentScheduler（render_loop.py 内新小类，~70 行）

```python
class SegmentScheduler:
    """段级播放调度：按序列循环切段。 owning=RenderLoop。"""
    def __init__(self, sequence: list[str]):
        self.sequence = sequence        # 非空、已验证存在的段名列表
        self.index = 0                  # 当前在 sequence 中的位置
    def current(self) -> str: ...
    def advance(self) -> str:           # index+1 循环，返回下一段名
    def sync_to(self, segment: str):    # 手动 /segment/switch 后对齐位置
```

### RenderLoop 集成

- `set_segment_sequence(sequence)` 注入（None = 现状，无调度）
- `_run()` 每帧检查（在 _process_frame 前后均可，放帧首）：
  ```python
  if self._scheduler is not None:
      seg_total = character_data.total_frames
      if (time.perf_counter() - self._video_start_time) * fps >= seg_total:
          nxt = self._scheduler.advance()
          self.switch_segment(nxt)   # 复用全部现有 reset/flush/gaze-reset 路径
  ```
- 手动 `switch_segment` API 调用时 → `scheduler.sync_to(segment)`（调度从新段继续循环）

### CLI（main.py/config.py）

```
--segments ""      默认：第一个段循环（现状）
--segments "1,55"  指定列表循环（逗号分隔，段名可含中文）
--segments auto    sorted(segments) 全循环
```

- 启动校验：列表段名必须存在，否则 fail-fast 并打印可用段（三段式报错）
- 单段列表/auto 单段 = 等价现状（退化安全）

### /status 扩展

`segment_sequence: {mode: single|list|auto, sequence: [...], position: n}`

## 4. 切换代价与稳定性

- 每次切段 = 现有 switch_segment 全路径：buffer flush（≤8 帧，~0.32s 冻结）+ BatchPreloader 快速
  reset + 后台并行预载（~0.5s 内就绪，期间单帧磁盘回读兜底）+ gaze reset + mask 随段自动更新
- 段间切换频率 = 段长（8-10 分钟/段@25fps）——冻结代价可忽略
- 音频播放不受切段影响（现有语义：音频继续，视频切换）
- 复用清单：switch_segment 全路径（DRY）、facemark/gaze_data 按段加载、/status、
  _video_start_time 时钟（不新增时钟——F13 教训：系统内只允许一个 pacing 时钟）

## 5. 明确不在本次范围

- 每段播放遍数/per-segment loops 参数（默认 1 遍即切；需要时加 --segment-loops）→ TODOS
- 运行时 API 修改调度序列（POST /segment/sequence）→ TODOS
- 跨段交叉淡化/过渡效果 → TODOS
- 多角色（跨 character_dir）调度 → TODOS

## 6. 复用清单

| 需求 | 已有资产 |
|---|---|
| 段切换全路径 | RenderLoop.switch_segment（reset+flush+gaze-reset） |
| 播放完成信号 | _video_start_time 时钟（elapsed ≥ total/fps） |
| 段列表 | CharacterDataPack.segments（sorted） |
| 校验/报错模式 | --gaze-correction fail-fast 三段式 |
| 状态可见 | /status |

---

# CEO 审查（Phase 1）— 模式: SELECTIVE EXPANSION

## 系统审计

- 25FPS 里程碑 + gaze 纠正就绪后，本方案动的是**调度层**（低频路径），渲染热路径零改动——
  与前两轮热路径改动风险等级不同，但仍按"刚稳定的系统"从严审查。
- 方案已核对 render_loop/sequence_frame_pack/api/main 现状（§2）。

## 0A. 前提挑战

| # | 前提 | 评估 | 结论 |
|---|------|------|------|
| P1 | 播完检测 = elapsed ≥ total/fps | 核对 _video_idx：elapsed 超界时 `% total` 自动回绕到段头；调度器 40ms 粒度检测窗口内最多渲染 1 帧回绕帧，随后 switch_segment 重锚+flush 清掉——无可见错位 | 接受 |
| P2 | 中文段名（"开心"） | Python3 Windows argv Unicode API 无乱码；sorted 按码位序确定性 ✓；**风险：用户输入中文逗号"，"**（全角）会破坏 split(",") | 接受，解析兼容全角逗号（并入 T3） |
| P3 | 手动切换与调度器并发 | sync_to（API 线程）与 advance（生产线程）竞态窗口：查 index+赋值两步非原子。频率极低但存在 | 接受，Scheduler 加 threading.Lock（并入 T1） |
| P4 | 切换冻结代价 | flush 后 re-arm 是 min(preroll,4)=4 帧 ≈ **0.16s**（方案写 0.32s 高估）；段长 8-10 分钟，占比 0.03% | 接受（数字修正） |
| P5 | 默认零变化 | 无 --segments 时 scheduler=None，零新代码路径 | 接受 |

## 0B/0C/0C-bis（要点）

- 0B：§6 复用清单完整——**不新增时钟**（F13 教训：单 pacing 时钟原则）。
- 0C：单段循环 → 顺序调度 → 12 个月：运行时序列 API/多角色/智能切换。方向正确。
- 0C-bis：**A) RenderLoop 内嵌 Scheduler（选定）**；B) 独立调度线程——新增线程+时钟，违反
  单时钟原则，否；C) CharacterDataPack 数据层调度——切换涉及 buffer flush/时钟重锚（渲染层
  职责），错位，否。选 A（P5 显式 + P4 DRY）。

## 0D. SELECTIVE EXPANSION

- E1 每段遍数 --segment-loops → TODOS（已列）
- E2 运行时序列 API POST /segment/sequence → TODOS（已列）
- E3 手动切换后调度对齐 sync_to → **核心范围**（不 left-shift 会循环回旧位置）
- E4 /status 调度状态 → 核心范围

## 0E. 时序审讯

- HOUR 1: 段名本身含逗号会破坏列表解析——文档声明 + strip+过滤空段；段名含逗号属用户自担
- HOUR 2-3: **启动即目标段**（segments[0] = 排第一段 = 当前段）——set_segment_sequence 不得触发
  一次冗余 switch；序列起点 index=0 与现状一致
- HOUR 4-5: auto 模式 sorted 的确定性（码位序）✓ 满足"程序排列"
- HOUR 6+: 测试图见 S6

## 0F. 模式确认：SELECTIVE EXPANSION，方案 A，E3/E4 并入，E1/E2 → TODOS

## 11 节深度审查（主审）

- **S1 架构**：Scheduler 无线程无时钟（纯状态机），依赖单向 RenderLoop→Scheduler；
  API 经 render_loop 引用调 sync_to。路径遍历免疫：段名必须在 character_data.segments
  白名单内（天然防 ../ 穿越）。SPOF：advance 纯内存操作无异常源；切换走 switch_segment
  全路径（已验证）。回滚 = 不传 --segments，二类门。
- **S2 错误**：段不存在 → 启动 fail-fast 列出可用段（三段式）；sync_to 未知名 → API 404
  （已有）；无 catch-all 新增。0 GAP。
- **S3 安全**：无新端点（POST /segment/switch 已存在）；CLI 白名单校验免疫注入。无发现。
- **S4 边界**：单段列表/auto 单段（退化=现状）✓；段名含空格（strip）✓；全角逗号 ✓；
  音频播放中切段（现有语义：音频继续视频切）✓；播放中手动切换→sync_to ✓。0 未处理。
- **S5 质量**：Scheduler ~70 行单职责状态机 ✓；无重复（复用 switch_segment）✓。
- **S6 测试图**：
  ```
  Scheduler 单测: advance 循环回绕 / sync_to 对齐+未知名容错 / 单段退化 / 锁
  RenderLoop 集成: 播完触发 switch（假时钟）/ 手动切换后调度继续 / 无调度零变化
  CLI 解析: 中文段名 / 全角逗号 / 空串默认 / 不存在段 fail-fast / auto
  ```
  2am 关键测试：播完触发切换 + 启动即目标段不触发冗余切换。
- **S7 性能**：每帧一次浮点比较（纳秒级）；切换 0.16s 冻结/段（0.03% 占比）。无发现。
- **S8 可观测**：/status segment_sequence + 切换日志 `[Scheduler] '1' -> '11'`。达标。
- **S9 部署**：CLI 即开关；默认零行为变化；验证清单=三模式启动各一次+切换观察。0 风险。
- **S10 轨迹**：可逆 5/5；为运行时 API（E2）/多角色调度铺路。
- **S11 Design**：跳过（无 UI 范围）。

## NOT in scope / 复用清单 / Dream delta

见方案 §5/§6；Dream delta：单段循环 → 顺序调度 → 运行时可控序列（TODOS）→ 多角色智能切换。

## Failure Modes Registry

```
CODEPATH            | FAILURE MODE        | RESCUED? | TEST? | USER SEES?        | LOGGED?
--------------------|---------------------|----------|-------|-------------------|--------
--segments 解析     | 段名不存在          | Y(fail-fast) | Y | 启动报错+可用段列表 | Y
scheduler.advance   | 理论无异常源        | N/A      | Y     | —                 | —
手动切换 vs 调度    | index 竞态          | Y(锁)    | Y     | 无                | N/A
切换 flush+preroll  | 0.16s 冻结          | 已知代价 | Y(目检) | 短暂冻结        | Y(切换日志)
音频播放中切段      | 音频继续视频切      | Y(现有)  | Y     | 预期行为          | Y
```
CRITICAL GAP: 0

## Implementation Tasks（CEO 阶段）

- [ ] **T1 (P1)** — SegmentScheduler 类（render_loop.py）：sequence/index/current/advance/
  sync_to + threading.Lock；RenderLoop.set_segment_sequence + _run 播完检测 + switch_segment
  联动 sync_to；启动即目标段不触发冗余切换
  - Files: realtime_serve/render_loop.py — Verify: pytest tests/test_segment_scheduler.py
- [ ] **T2 (P1)** — CLI/config：--segments 解析（auto/列表/空默认；全角逗号兼容；strip；
  fail-fast 白名单校验三段式）+ config 字段 + 装配 + banner 回显
  - Files: realtime_serve/main.py, config.py — Verify: --help + 三模式启动冒烟
- [ ] **T3 (P2)** — /status segment_sequence + 切换日志
  - Files: realtime_serve/api/app.py — Verify: 字段断言
- [ ] **T4 (P1)** — 测试：Scheduler 单测 + RenderLoop 集成（假时钟播完触发/手动切换对齐/
  无调度零变化）+ CLI 解析（中文/全角逗号/fail-fast）
  - Files: tests/test_segment_scheduler.py（新）, tests/test_t5_render_loop.py（补）
- [ ] **T5 (P2)** — RUNBOOK 调度章节（三模式/中文段名/切换冻结说明）
  - Files: realtime_serve/RUNBOOK.md

## CEO 完成总结

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (CEO)                |
+====================================================================+
| Mode selected        | SELECTIVE EXPANSION                          |
| System Audit         | 调度层低频路径，渲染热路径零改动              |
| Step 0               | 方案A内嵌Scheduler; E3/E4并入; E1/E2→TODOS   |
| Section 1  (Arch)    | 0 issues（无线程无时钟）                      |
| Section 2  (Errors)  | 2 error paths, 0 GAPS                        |
| Section 3  (Security)| 0 issues（白名单免疫路径穿越）                |
| Section 4  (Data/UX) | 5 edge cases mapped（全角逗号/中文/单段等）   |
| Section 5  (Quality) | 0 issues                                     |
| Section 6  (Tests)   | 图已产出, 0 gaps                             |
| Section 7  (Perf)    | 0 issues                                     |
| Section 8  (Observ)  | /status+切换日志                             |
| Section 9  (Deploy)  | 0 risks（默认零变化）                         |
| Section 10 (Future)  | 可逆 5/5                                     |
| Section 11 (Design)  | SKIPPED（无UI）                              |
+--------------------------------------------------------------------+
| Unresolved decisions | 0（taste 项留门：切换冻结感知度）             |
+====================================================================+
```

---

# DX 审查（Phase 2.5）— 模式: DX POLISH

Persona 不变（solo operator，CLI 启动）。本方案 DX 面：一个 CLI 参数、/status 字段、RUNBOOK 章节。

## 8 维评分（修复前 → 修复后）

| 维度 | 分 | 说明 |
|------|----|------|
| 1 Getting started | 8 | `--segments auto` 自明；默认零变化 |
| 2 CLI ergonomics | 9 | 单参数三态；**全角逗号兼容**（CEO P2 并入）；段名以目录实际名为准（用户原话） |
| 3 Error messages | 8 | 段不存在 fail-fast 列出可用段（复用 gaze 三段式模式） |
| 4 Docs | 7→8 | RUNBOOK 调度章节（T5）：三模式/中文段名/冻结说明 |
| 5 Escape hatches | 9 | 不传=现状；任意子集列表 |
| 6 Credible | 8 | 切换日志 + /status 位置可见 |
| 7 Findable | 8 | /status segment_sequence |
| 8 Valuable | 9 | 多素材循环直接可用 |

**DX Scorecard: 8.1 → 8.6/10**。TTHW 不变。

DX 发现：
- [中] 段名含英文逗号会破坏列表解析 → 文档声明 + strip 过滤（CEO 0E）
- [低] 切换 0.16s 冻结在自动模式下周期出现（每段一次）→ 切换日志已缓冲感知；RUNBOOK 写明
- [低] "auto" 作为段名的场景（真有叫 auto 的段）→ 模式词优先，文档声明该保留字

### CLAUDE SUBAGENT（DX — 独立体验挑战）核心发现

1. **[中] /status segment_sequence 语义矛盾**：paused 不在 mode 枚举；默认(scheduler=None)未定义；
   position 基数未说明；auto 下 sequence 与 available_segments 冗余。
   → 修复：mode 枚举 `off|list|auto`（off=未启用）；paused 用 `position: -1` 表达；position 0 基
   指向 sequence 下标；auto 可省略 sequence。
2. **[中] 校验时机错层**：段白名单在 CharacterDataPack 加载后才存在；Config.__post_init__ 处不可
   复制；放 setup_service 末尾则模型加载完才报错（fail-fast 名不符实）。空串/纯分隔符行为未定义。
   → 修复：语法层在 parse_args；存在性+分辨率校验钉在 CharacterDataPack 加载后、ModelManager 前；
   空串→回退默认；报错含原始输入/解析列表/可用段/大小写敏感提示。
3. **[低] auto 保留字**：仅整串 strip 后严格等于 "auto" 才是模式；"auto,1" 按列表解析（自洽报错）。
4. **[低] 术语统一**：CLI --segments ↔ /status segment_sequence ↔ 未来 API 路径的映射表进 RUNBOOK。
5. **[低] 逗号三重缓解可消**：改 `nargs="+"`（空格分隔）+ 每项内部再 split(",")——全角逗号/段名
   含逗号两类问题同时消失；`--segments 1,55` 旧习惯不破坏（单段名不存在 → fail-fast 自愈）。
   **采纳**。
6. **[低] T5 文档缺口**：启动参数表行、/status 表行、段名发现方式（= character_dir 子目录名或查
   /status）、手动切换交互、main.py docstring Usage 与 endpoints 打印块、回滚章节。

### DX DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          是      N/A   CONFIRMED
  2. API/CLI naming guessable?         部分    N/A   部分确认
  3. Error messages actionable?        部分    N/A   部分确认
  4. Docs findable & complete?         部分    N/A   部分确认
  5. Upgrade path safe?                是      N/A   CONFIRMED
  6. Dev environment friction-free?    是      N/A   CONFIRMED
═══════════════════════════════════════════════════════════════
```

## DX 修正 D1-D4（并入任务）

| # | 修正 | 内容 |
|---|------|------|
| D1 | /status 语义定死 | mode = off\|list\|auto；序列外段 = position:-1；position 0 基；auto 省略 sequence |
| D2 | 校验两层钉死 | 语法层 parse_args（nargs="+"，每项可含逗号）；存在性+分辨率在 DataPack 加载后/模型加载前；空串回退默认 |
| D3 | auto 保留字规则 | 整串严格=="auto" 才是模式；列表内 "auto" 走白名单报错（自洽） |
| D4 | T5 文档清单扩展 | 六项（参数表/状态表/段名发现/交互说明/docstring+endpoints/回滚）+ 术语映射表 |

---

# 工程审查（Phase 3）— 最终门前的收官审查

## 跨阶段主题（2+ 阶段独立发现 → 高置信信号）

| 主题 | 出现阶段 | 结论 |
|------|----------|------|
| **校验层次/时机** | CEO 发现4.2（auto 零校验+分辨率）、DX 发现2（错层慢失败） | 两阶段独立命中 → D2 两层钉死（语法层 parse_args / 存在性+分辨率在 DataPack 后模型前） |
| **状态机语义未定义** | CEO 发现3.2（序列外段）、DX 发现1（paused 不在枚举） | 两阶段 → S6+D1 定死 |
| **解析健壮性** | CEO P2（全角逗号）、DX 发现3+5（保留字/nargs+） | 两阶段 → D2/D3 + nargs="+" |

## Step 0 范围挑战（Eng）

复杂度：1 新小类 + 4 文件修改 + 2 测试文件 = 7 文件 ✓ 阈值内。调度为纯状态机无线程无时钟 ✓。
架构修正 E-ENG 见下（来自主审独立复核 + Eng subagent 结果）。

## S1-S4 主审分析（含 S1-S8 落地设计要点）

- **S1 架构**：S1a（先 load 后发布）的连带——BatchPreloader.reset 在 RenderLoop.switch_segment
  中调用（DataPack.switch_segment 返回后），load-先行不改变该顺序 ✓；video clock 重锚仍在
  RenderLoop 层 ✓。S1b 的 try/except 必须记录异常类+段名+帧号并连续失败计数（连续 >30 次
  fail-fast 退出——防死循环刷日志）。
- **S2**：reset_preroll 语义复核——与 process_full_audio 相同调用（重置为 min(preroll,4)）；
  audio_start 保全（S7）用 render_loop._pending_audio_start 重置实现。
- **S3**：调度临界区 = _segment_lock（已有锁复用）；锁内重验 elapsed 用重锚后的
  _video_start_time（手动切换 elapsed≈0 → 放弃）。switch_segment 增加来源参数
  （source: 'manual'|'scheduler'，默认 manual）。
- **S4**：启动校验位置钉在 CharacterDataPack 构造后、ModelManager 构造前（D2）；分辨率比对
  读 metadata.shape 或首帧尺寸。
- **S6 测试**：测试计划产物已落盘（edtalkdev-main-test-plan-20260903-113805.md），
  U1-U6 + I1-I9 + C1-C6 覆盖 S1-S8/D1-D4 全部修正点。

## 工程完成总结

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (ENG)                |
+====================================================================+
| Step 0 范围挑战      | 7 文件阈值内；纯状态机 ✓                     |
| Section 1 (Arch)    | S1a/b/S2/S3 设计要点已核（单时钟保持）        |
| Section 2 (Quality) | 0 新问题                                      |
| Section 3 (Tests)   | artifact 落盘 ✓；U+I+C 全覆盖 S/D 修正        |
| Section 4 (Perf)    | 每帧 float 比较；0 issues                     |
| Failure modes       | 0 CRITICAL GAPS（S1 修后）                    |
| Test plan artifact  | ✓                                             |
| TODOS               | E1 loops, E2 运行时API, 过渡效果, 多角色      |
| Unresolved          | 0                                             |
+====================================================================+
```

## 工程双声部（Step 0.5）

CODEX auth 缺失 → [subagent-only]。

### CLAUDE SUBAGENT（ENG — 独立架构挑战）核心发现

1. **[HIGH] F1：S1a"先 load 后发布"仍留两个洞**——(a) publish 先于 ensure_loaded(0)：窗口内渲染
   线程带旧时钟 idx 调 ensure_loaded → 命中"不在任何 batch"分支启动随机 batch 后台加载，挤掉
   batch-0 预载 → 新段头 ~0.5s 全靠 25ms 单帧回读；(b) S1c"每帧单次读 total（快照段名）"反而制造
   新竞态：在途帧以旧段名调 ensure_loaded → _reset_locked 拨回旧段 + 新段预载因 gen 失配整体丢弃。
   **修复**：DataPack.switch_segment 内部钉死 `_load_segment → reset(new) → ensure_loaded(0,new) →
   最后发布 current_segment`（失败保持旧段）+ 内部锁（并发 double-load 今天就存在）；S1c 保持
   live 读（撕裂窗口 ≤1 帧错帧可接受），禁快照。
2. **[HIGH] F2：S2 的 reset_preroll 紧跟 flush(keep_reserve=5) 是空操作**——flush 后 depth=5 ≥
   re-arm 阈值 4 → 立即通过；保留的 5 帧全是旧段尾帧，播完后 underrun 重发的是旧段冻结帧。
   **且 process_full_audio 里同样的组合今天就是失效的（存量 bug）**——log14 的 "Preroll: waiting 4"
   只是打印时序，50ms 内即 complete。
   **修复**：切换与推流路径都改 `flush(keep_reserve=0)` 再 reset_preroll；RUNBOOK 删"冻结"表述。
3. **[HIGH] F3：调度切换失败 → 时钟不重锚 → 每帧重触发 → 逐段链式尝试 + 日志风暴**。plan 伪代码
   先 advance 后 switch，index 已推进；switch_segment 三个异常源（ValueError/FileNotFoundError/
   数量不一致）无一被接。
   **修复**：try 包整个循环体（含调度检查）；调度 switch 失败仍重锚 _video_start_time（重启当前段）
   + 连续失败熔断（N 次后 /status 可见 degraded）；日志限频。
4. **[MEDIUM] F4：S1b 引入静默失败面**——audio_start 标记在 put 前被消费（render_loop.py:463-467
   vs :469-472），put 前异常 → _pending_pcm 孤儿且零日志。S7 落地必须门控
   （`_pending_pcm is not None and not _pending_audio_start` 才重置——无条件重置会在标记帧幸存于
   reserve 时双标记）。/status 必须暴露 last_error/degraded（否则 try/except = 线程死亡换永久静默）。
5. **[MEDIUM] F5：调度检查必须帧首**——帧尾放置时越界迭代已产出旧段回绕帧且被 flush reserve 保留
   → 切后先闪 40ms 旧段第 0 帧（P1"flush 清掉无错位"与代码相反）。伪代码作用域错误
   （fps/seg_total 未定义于 _run）；S6 挂起态应整体跳过检查；极短段（<1s）会切换风暴 → 最小段长校验。
6. **[MEDIUM] F6：测试缺口 7 项**——失败切换不重触发、S7 恰一次+幂等、S2 语义钉死（可直接抓 F2）、
   并发终态一致性、分辨率行为定义（_validate_segment 只查路径不查分辨率；resolution 缓存永不失效）、
   **时钟注入 self._clock**（_video_idx 与调度共用——把 F13 单时钟原则落到代码）、/status paused。

### ENG DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               否(F1)  N/A   DISAGREE→修正
  2. Test coverage sufficient?         否(F6)  N/A   DISAGREE→修正
  3. Performance risks addressed?      是      N/A   CONFIRMED
  4. Security threats covered?         是      N/A   CONFIRMED
  5. Error paths handled?              否(F2/F3) N/A DISAGREE→修正
  6. Deployment risk manageable?       是(条件) N/A  部分确认
═══════════════════════════════════════════════════════════════
```

## 工程修正 E1-E6（替代/强化 S1-S8）

| # | 修正 | 内容 |
|---|------|------|
| E1 | DataPack.switch_segment 顺序+锁 | 内部钉死 load→reset→ensure_loaded(0)→最后发布（失败保持旧段）；内部锁防并发 double-load；S1c 保持 live 读禁快照 |
| E2 | flush(keep_reserve=0)+reset_preroll | 切换与推流两路径统一（F2 存量 bug 一并修）；RUNBOOK 如实写"尾帧全弃+0.16s preroll" |
| E3 | 调度失败不风暴 | try 包整个循环体；switch 失败仍重锚时钟（重启当前段）；连续失败熔断 → /status degraded；日志限频 |
| E4 | audio_start 原子化 | 标记消费+put 原子化（或异常回滚标记）；S7 重置带门控（pcm 非空且未标记）；/status 加 last_error/degraded |
| E5 | 调度检查钉帧首 | _process_frame 之前；作用域用 self.target_fps 与同一 _video_start_time；S6 挂起跳过；最小段长校验（<1s 拒绝） |
| E6 | 测试+时钟注入 | F6 的 7 项缺口并入测试计划；self._clock 注入被 _video_idx 与调度共用（单时钟落码） |

## 决策审计表（Decision Audit Trail）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否 |
|---|------|------|------|------|------|------|
| D1 | CEO | 模式 SELECTIVE EXPANSION | autoplan override | — | 延续性工程 | — |
| D2 | CEO | 0C-bis 方案A（RenderLoop 内嵌纯状态机） | Mechanical | P5/P4 | 否 B 独立线程（违反单时钟 F13）、C 数据层错位 | B、C |
| D3 | CEO | E3 sync_to / E4 /status 并入核心 | Mechanical | P2 | 不并入则循环回旧位 | — |
| D4 | CEO | E1 loops / E2 运行时API / 过渡 / 多角色 → TODOS | Mechanical | P3 | 需求未出现 | — |
| D5 | CEO | 复核确认 8 项声部发现（含"0.16s 冻结"作废） | Mechanical | P1 | 代码核实 | — |
| D6 | DX | D1-D4 修正并入（nargs="+" 采纳/状态语义/校验两层/保留字规则） | Mechanical | P1 | 2 中 4 低全成立 | — |
| D7 | ENG | E1-E6 修正并入（顺序+锁/flush(0)/失败重锚熔断/audio_start 原子化/帧首检查/时钟注入） | Mechanical | P1 | F1-F3 三个 HIGH 代码核实 | — |
| D8 | ALL | 切换边界体验 = 尾帧全弃 + 0.16s preroll（无交叉淡化） | Taste→门 | — | 过渡效果已列 TODOS；用户可门上否决 | — |

**UNRESOLVED DECISIONS: 0**（唯一 taste 项 D8 的替代方案已列 TODOS，默认受控硬切）

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` Phase 1 | 范围与战略 | 1 | issues_open→closed | 声部 8 发现（1 CRITICAL 既有竞态被放大；边界描述作废）；S1-S8 |
| Codex Review | 未运行 | auth 缺失 | 0 | unavailable | 降级 [subagent-only] |
| DX Review | `/autoplan` Phase 2.5 | 开发者体验 | 1 | issues_open→closed | 6 发现（2 中）；D1-D4（nargs="+" 采纳） |
| Eng Review | `/autoplan` Phase 3 | 架构与测试（必需门） | 1 | issues_open→closed | 6 发现（3 HIGH，含存量 flush+preroll 空操作 bug）；E1-E6；测试产物落盘 |
| Design Review | 跳过 | 无 UI 范围 | 0 | skipped | — |

- **CROSS-MODEL:** 校验层次（CEO 4.2 × DX 2）、状态语义（CEO 3.2 × DX 1）、解析健壮性（CEO P2 × DX 3/5）——三主题两阶段独立命中
- **VERDICT:** CEO + DX + ENG CLEARED（E1-E6 + D1-D4 全部并入）— ready to implement

NO UNRESOLVED DECISIONS

---

# CEO 双声部（Step 0.5）

CODEX auth 缺失 → [subagent-only]。

### CLAUDE SUBAGENT（CEO — 战略独立挑战）核心发现

1. **[CRITICAL] 既有崩溃竞态被调度器放大**：`CharacterDataPack.switch_segment`
   （sequence_frame_pack.py:680-681）**先发布 `current_segment` 后 `_load_segment`**（10-200ms 窗口，
   np.load+listdir）；`_process_frame` 拿 pack 引用后**无锁**读 `_segment_cache[self.current_segment]`
   → 窗口内 KeyError → **`_run` 无 try/except（render_loop.py:172-173）→ 生产线程静默死亡**，
   /status 仍显示 running。调度器把"手动偶发"升级为"每小时自动试一次"。且方案的
   "advance 理论无异常源 N/A"、"切换走已验证全路径"两处登记与代码相反——switch_segment 可抛
   ValueError/FileNotFoundError/KeyError 无一被接。
   **主审复核：成立**（switch_segment 先发布后加载 ✓；_run 无防护 ✓）。修复：
   (a) switch_segment 先 load 后发布；(b) _run 的 _process_frame 包 try/except（记日志继续）；
   (c) 每帧只读一次 total 并复用。
2. **[HIGH] 边界行为描述不诚实**：switch_segment **不会 re-arm preroll**（只有 process_full_audio
   调 reset_preroll）——方案 §4"0.32s 冻结"与主审修正的"0.16s 冻结"**都是虚构机制**。真实 seam =
   flush 丢最多 11 帧（**旧段结尾前跳 ~0.44s**）+ 硬切新段 + 浅缓冲(5) 期撞 GPU p99 尖峰 →
   underrun 重发抖动。
   **主审复核：成立**（switch_segment 无 reset_preroll ✓；log14 实测 "Flushed 11 frames"+"Preroll:
   waiting 4" 是音频 push 路径的）。修复：**switch_segment 中加 output_streamer.reset_preroll()**
   （与 process_full_audio 对齐，受控 ~0.16s 平滑重启 + 消灭 underrun 窗口）——二选一中选此项。
3. **[HIGH] sync_to+锁不修竞态本质**：advance 决策（index+1）与 switch 执行之间的窗口，手动切换
   插入 → 序列错位一格。修复：调度路径 check+advance+switch 收进同一 `_segment_lock` 临界区，
   **取锁后重读 _video_start_time 重验 elapsed**（刚被手动切换重锚则 elapsed≈0 → 放弃 advance）；
   switch_segment 区分来源（调度发起不 sync_to，手动发起锁内 sync_to）。
4. **[HIGH] auto 模式零校验**：segments 发现仅 isdir——垃圾目录/半导出段成"段"，且
   **UnityCaptureSender 按第一段分辨率构造一次**，异分辨率段 → sender 每帧异常被吞 → 冻结。
   修复：启动对生效序列每段 `_validate_segment` + 分辨率比对，fail-fast。
5. **[MEDIUM] auto 排序码位序**：将来加段 "6" 排在 "55" 后（1,11,22,33,44,55,6）→ 用户可见顺序
   bug。修复：自然排序（复用 `_numeric_sort_key` 思路）。
6. **[MEDIUM] sync_to 序列外段语义未定义**：手动切到 segments 存在但不在 sequence 里的段。
   定义：调度挂起，直到手动切回序列内段。
7. **[LOW-MED] flush 可能丢 audio_start 标记帧** → _pending_pcm 永不播放。修复：switch_segment
   时若音频 pending 未触发则重置 _pending_audio_start=True（下帧重新标记）。
8. [LOW] stdout 非 UTF-8 locale 下中文段名打印可二次崩溃 → errors='replace' 兜底；
   注入时序钉死 start() 前。

### CEO DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                   否(P1冻结虚构) N/A DISAGREE→修正
  2. Right problem to solve?           是       N/A   CONFIRMED
  3. Scope calibration correct?        部分     N/A   部分确认
  4. Alternatives sufficiently explored? 是     N/A   CONFIRMED
  5. Robustness covered?               否(1.1/4.2) N/A DISAGREE→修正
  6. 6-month trajectory sound?         部分     N/A   部分确认
═══════════════════════════════════════════════════════════════
```
8 项发现主审复核全部确认。**Failure Modes Registry 两处登记作废重写**（"0.16s 冻结已目检"、
"advance 无异常源 N/A"——都是未验证断言）。

## 方案修正 S1-S8（并入任务）

| # | 修正 | 内容 |
|---|------|------|
| S1 | 崩溃竞态根治 | switch_segment 先 load 后发布；_process_frame 包 try/except（日志+继续）；每帧单次读 total |
| S2 | 边界受控重启 | switch_segment 加 output_streamer.reset_preroll()（~0.16s 受控重启，消灭 underrun 窗口）；RUNBOOK 如实写"前跳+0.16s 重启" |
| S3 | 调度原子性 | advance+switch 收进 _segment_lock，取锁后重验 elapsed；switch_segment 来源区分（调度不 sync_to，手动 sync_to） |
| S4 | 启动全量校验 | 生效序列每段 _validate_segment + 分辨率一致性 fail-fast（覆盖 list 与 auto） |
| S5 | 自然排序 | auto 序列用 _numeric_sort_key 思路（"6"排在"55"后→前排） |
| S6 | 序列外段语义 | 手动切到序列外段 → 调度挂起至切回序列内段（/status 显示 paused） |
| S7 | audio_start 保全 | switch_segment 时 pending 未触发则重置 _pending_audio_start=True |
| S8 | 打印兜底 | 段名打印 errors='replace'；注入时序钉死 start() 前 |
