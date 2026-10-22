# 动作编排 RUNBOOK（Action Choreography）

> /autoplan 2026-10-21 批次 P1 交付（义务 F3/F4/WP-5/WP-7/C1-1/C5-1/F2 对应章节）
> 完整设计：编排器子图见 `docs/flowchart-detail.md`（只读历史归档，含编排器子图与锚点）+ 主干图 `docs/flowchartTD.md`（动作编排=事件订阅边，2026-10-22 uc4a 裁决）+ `.gstack/tmp/autoplan/action-orchestration-plan.md`

## 1. 功能与启用

事件驱动的数字人画面编排：打赏/关键词/点歌 → 片段序列（enter→main→exit）+ 情绪分级。

**启用步骤（方式一：管理页，推荐）**：webui → 数字人-画面设置 → 「动作编排」卡片 →
刷新可用段 → 配规则 → 点「应用」（=校验+热生效+写回 config，序列执行中会拒绝 409）。
状态条实时显示执行中规则/步骤/情绪档/计数/降级。

**启用步骤（方式二：手编 config）**：
1. EDTalk realtime_serve 运行中（编排前置 = `is_edtalk_active`，本地播放模式不适用——§3 边界）
2. `config.json` 新增 `choreography` 节（结构见 config.example.json 示例规则）
3. `enable: true` + 重启主进程（**开关生效时机 = 配置装载，与全系统一致，无热更**）
4. 观察启动日志：`[编排] EDTalk 就绪` + `DEFAULT 初始化` + 规则生效/禁用清单

**段名发现**：段名 = EDTalk 角色素材目录名（character_dir 子目录），或 `GET /status` 的 `available_segments`（管理页「刷新可用段」同源）。规则引用不存在的段名 → 该规则 disabled + 资产缺口报告（三段式日志列出缺失清单，义务 F2）。

## 2. 配置速查

| 键 | 缺省 | 说明 |
|---|---|---|
| `enable` | false | 总开关；false = 订阅点短路直通，全链零行为变化 |
| `default_mode` | rotate | single（池首）/ rotate（顺序轮播）/ random（每次恢复打乱下发） |
| `default_segments` | [] | 默认画面段名池；空池 = 不接管默认轮播（EDTalk 自身 --segments 生效） |
| `min_retrigger_s` | 2 | 每规则最短再触发间隔（防连点风暴；被节流触发不计数） |
| `min_sequence_hold_s` | 5 | 全局序列最小展示时间：hold 内他键触发忽略（防画面乒乓，F5） |
| `step_timeout_s` | 60 | audio 步骤缺省完成上限（F1 看门狗：超时强制推进+降级计数） |
| `rules[].trigger_type` | — | gift / keyword / song（entrance/follow 预留，P1 不订阅） |
| `rules[].match` | — | gift=礼物名全等；keyword=子串（NFKC+去空白+casefold 规范化）；song=可空=任意点歌 |
| `rules[].min_price` | 0 | 仅 gift：最低价值门槛，与答谢链 lowest_price 同口径 |
| `rules[].sequence` | — | enter/main/exit 三步：segment+switch_at+duration_s+audio{mode,path} |
| `rules[].emotion_tiers` | 可选 | max_count 严格递增；超出末档用最高档（缺省=单档恒用基础素材） |

**关键校验**：无 audio 的步骤必须 `duration_s > 0`（否则规则拒绝启用——拦截单槽 pending 静默跳段，FB-2）。`reaction_segment` 旧键已 deprecated（由编排规则取代，配置面保留不删）。

## 3. 语义与边界声明（必读）

- **完成观测仅支持 EDTalk 激活场景**（WP-5）：观测点 = playback EDTalk push 路径的 `push_full` 返回（单飞行句完成判定）。builtin 的 `play()` 是入队背压非播放结束，pygame 路径语义不同——非 EDTalk 模式编排器整体禁用。
- **计时起点 = 切换请求被 EDTalk 接受时**（CL-1）：`switch_at=segment_end` 的边界等待会计入实际显示时长（显示时长 ≥ duration_s）。衔接片段（enter 等短素材）建议 segment_end；长段默认池勿用作序列段。
- **file 音轨完成精度**受 `_wait_flight_complete` 启发式限制（EDTalk 完成判定启发式，宁迟勿截）。
- **tts 音轨与周期答谢通道**（WP-3/WP-4）：编排答谢经注入引用复用原生 `_thanks_generate`，E-2 每分钟限流窗口与周期通道全共享。**注意**：`thanks.gift.periodic_trigger.enable=true` 时答谢进周期缓存延后播报，画面按 F1 上限（duration_s 或 60s）推进——语音与画面的同步在该组合下降级为超时驱动。
- **误匹配局限**（CL-2）：完成关联键 = (queue_type, content)+晚于入队时间。他链路同 content 音频可能提前触发完成判定 → 画面略早切换（EDTalk 边界兜底）。
- **手动切段竞争**（WP-7）：webui 手动切段（画面设置页）会覆盖/清除编排 pending（单槽 last-write-wins）——**直播期间勿手动切段**。条件 3 拼接中若旧 exit 的 segment_end 请求尚在 pending，新 enter 请求会覆盖之（旧 exit 丢失，直接入场）——短衔接片段窗口极小，已知边界。

## 4. 合规表述与浸泡验收（F3/F4）

**合规假设（标注）**：事件驱动的互动反馈是"非无人直播"的互动性证据。本节为**假设，不承诺平台政策效果**——各平台对录播/低质的判定标准不同，本功能以画面多样性 + 互动反馈降低重复内容风险。

**浸泡期可观测信号**（P2 投入裁决依据）：
1. 浸泡场次的人均礼物触发编排次数（编排序列启动日志计数 / 场次人数）
2. 主播主观评价（画面是否提升互动氛围、是否被观众注意到）

**浸泡验收清单**（P1 合并后实播验证）：
- [ ] 最小可跑资产集：1 条规则 3 段，完整触发→enter→main→exit→回默认轮播闭环
- [ ] 条件 3 拼接场景：两次不同礼物连发（[旧 exit, 新 enter, 新 main] + hold 窗口行为）
- [ ] 情绪分级：同礼物连发跨档（素材组切换可见）
- [ ] F1 看门狗：移走 file 音频文件复现超时推进（降级日志出现，画面不卡死）
- [ ] 关闭回滚：enable=false 重启 → 全链零行为变化

## 5. 故障排查

| 症状 | 三段式 |
|---|---|
| 规则未生效 | problem=规则被拒或 disabled；cause=结构校验/段名缺口（见启动日志三段式）；fix=按日志逐条修正 |
| 画面停在序列段不回默认 | problem=恢复轮播重试耗尽；cause=/segment/schedule 连续失败；fix=查 EDTalk 服务后手动切回或重启 |
| 步骤跳得太快 | problem=降级计数增长；cause=切段失败或音频未入队（file 门拦截/文件缺失）；fix=查 play_audio.enable 与文件路径 |
| 编排器无响应 | problem=worker 未启动；cause=enable=false 或 EDTalk 客户端缺失；fix=检查配置与 EDTalk 启用状态 |

**回滚**：`choreography.enable=false` + 重启（分钟级）；模块隔离，git revert 可整体退役。
