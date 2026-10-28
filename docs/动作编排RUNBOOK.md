# 动作编排 RUNBOOK（Action Choreography）

> /autoplan 2026-10-21 批次 P1 交付（义务 F3/F4/WP-5/WP-7/C1-1/C5-1/F2 对应章节）
> 2026-10-23 补完批次更新（GAP-1/2/3/4+F1 竞态+模拟触发端点——见文末 §6）
> 完整设计：编排器子图见 `docs/flowchart-detail.md`（只读历史归档，含编排器子图与锚点）+ 主干图 `docs/flowchartTD.md`（动作编排=事件订阅边，2026-10-22 uc4a 裁决）+ `.gstack/tmp/autoplan/action-orchestration-plan.md`
>
> **文档地位声明（DX M1）**：`docs/动作编排设计.md` 为需求讨论**历史存档**（其帧级架构/八端点表/pydantic 模型/switch_mode 三值/repeat_tiers 已被段级实现裁决替代）——本 RUNBOOK 与 `config.example.json` choreography 节 + `utils/orchestration/api.py` 实况为**权威结构**，实施以 autoplan 义务块为准。

## 1. 功能与启用

事件驱动的数字人画面编排：打赏/关键词/点歌 → 片段序列（enter→main→exit）+ 情绪分级。

**启用步骤（方式一：管理页，推荐）**：webui → 数字人-画面设置 → 「动作编排」卡片 →
刷新可用段 → 配规则 → 点「应用」（=校验+热生效+写回 config，序列执行中会拒绝 409；
**初始关闭状态点「应用」启用=worker 热启动（GAP-1 修复），返回体含 worker_started/ready 字段**）。
状态条实时显示执行中规则/步骤/情绪档/计数/降级/重排待执行/步剩余秒（GAP-4）。
配置后可用「模拟触发」（§5）即时验证，无需等待真实礼物。

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
| `reset_after_s` | 300 | 情绪分级计数时间窗口（GAP-2）：超过该秒数未尝试触发则计数清零重新累计；锚点=最后一次尝试（含被节流拒绝的）；0=禁用衰减；负值/非数值回退缺省 300 并告警 |
| `rules[].trigger_type` | — | gift / keyword / song（entrance/follow 预留，P1 不订阅） |
| `rules[].match` | — | gift=礼物名全等；keyword=子串（NFKC+去空白+casefold 规范化）；song=可空=任意点歌 |
| `rules[].min_price` | 0 | 仅 gift：最低价值门槛，与答谢链 lowest_price 同口径 |
| `rules[].sequence` | — | enter/main/exit 三步：segment+switch_at+duration_s+audio{mode,path} |
| `rules[].emotion_tiers` | 可选 | max_count 严格递增；超出末档用最高档（缺省=单档恒用基础素材） |

**关键校验**：无 audio 的步骤必须 `duration_s > 0`（否则规则拒绝启用——拦截单槽 pending 静默跳段，FB-2）。`reaction_segment` 旧键已 deprecated（由编排规则取代，配置面保留不删）。

## 3. 语义与边界声明（必读）

- **完成观测支持 EDTalk 激活场景与 builtin 播放器**（WP-5 + G1 2026-10-28）：EDTalk 观测点 = playback EDTalk push 路径的 `push_full` 返回（单飞行句完成判定）；builtin 观测点 = 播放器完成回调（`_fire_completion` 载荷携带被播条目 type/content，audio_core 注册转发 `on_audio_complete`，A3 死链清理后重新注册）。两路互斥不双报。pygame/外部模式语义不同——该模式下编排器整体禁用。
- **计时起点 = 切换请求被 EDTalk 接受时**（CL-1）：`switch_at=segment_end` 的边界等待会计入实际显示时长（显示时长 ≥ duration_s）。衔接片段（enter 等短素材）建议 segment_end；长段默认池勿用作序列段。
- **file 音轨完成精度**受 `_wait_flight_complete` 启发式限制（EDTalk 完成判定启发式，宁迟勿截）。
- **tts 音轨与周期答谢通道**（WP-3/WP-4）：编排答谢经注入引用复用原生 `_thanks_generate`，E-2 每分钟限流窗口与周期通道全共享。**注意**：`thanks.gift.periodic_trigger.enable=true` 时答谢进周期缓存延后播报，画面按 F1 上限（duration_s 或 60s）推进——语音与画面的同步在该组合下降级为超时驱动。
- **误匹配局限**（CL-2）：完成关联键 = (queue_type, content)+晚于入队时间。他链路同 content 音频可能提前触发完成判定 → 画面略早切换（EDTalk 边界兜底）。
- **手动切段竞争**（WP-7）：webui 手动切段（画面设置页）会覆盖/清除编排 pending（单槽 last-write-wins）——**直播期间勿手动切段**。条件 3 拼接中若旧 exit 的 segment_end 请求尚在 pending，新 enter 请求会覆盖之（旧 exit 丢失，直接入场）——短衔接片段窗口极小，已知边界。

## 4. 合规表述与浸泡验收（F3/F4）

**合规假设（标注，2026-10-23 防封归因声明强化——CEO native 发现1）**：事件驱动的互动反馈是"非无人直播"的互动性证据之一。**动作编排=互动反馈与画面多样性手段之一，不承诺防封效果**——平台对录播/数字人的判定涉及画面指纹、报备状态等多因素，编排引擎不改变底层素材像素复用。本节为假设，不承诺平台政策效果。
**平台报备前置检查（CEO native 发现11）**：开播前确认目标平台的数字人/虚拟人报备要求——未报备虚拟人的限流/封禁风险是政策性的，编排功能无法化解。
**浸泡期实测基线指标（CEO native 发现2，实测前采集一周基线数据）**：1) 封禁/限流间隔（对比编排启用前后）2) 平均观看时长 3) 礼物触发互动率（编排序列启动次数/场次人数）。

**浸泡期可观测信号**（P2 投入裁决依据）：
1. 浸泡场次的人均礼物触发编排次数（编排序列启动日志计数 / 场次人数）
2. 主播主观评价（画面是否提升互动氛围、是否被观众注意到）

**浸泡验收清单**（P1 合并后实播验证；2026-10-23 补完批次代码交付，实测仍待执行）：
- [ ] 最小可跑资产集：1 条规则 3 段，完整触发→enter→main→exit→回默认轮播闭环
- [ ] 条件 3 拼接场景：两次不同礼物连发（[旧 exit, 新 enter, 新 main] + hold 窗口行为）
- [ ] 情绪分级：同礼物连发跨档（素材组切换可见）
- [ ] F1 看门狗：移走 file 音频文件复现超时推进（降级日志出现，画面不卡死）
- [ ] 关闭回滚：enable=false 重启 → 全链零行为变化
- [ ] （补完批次新增）热启用链：初始 enable=false → 管理页应用启用 → worker 热启动 → 模拟触发画面切换
- [ ] （补完批次新增）计数窗口：同礼物跨 reset_after_s 两次触发 → 情绪档回落第 1 档

## 5. 模拟测试（GAP-3：配置后即时验证，无需等待真实礼物）

**管理页方式（推荐）**：「动作编排」卡片 → 模拟触发分区 → 下拉选择已配置规则 → 点「模拟触发」（outline 弱化按钮与「应用」分行）。结果 notify：成功=已启动/已排队；失败=reason+fix 文案（如 `未触发[throttled]：触发过快被节流，2s 后可重试`）。

**curl 方式（copy-paste，端口按 config api_port 调整）**：

```bash
# rule_id 形态（推荐）：端点从规则配置合成载荷（gift 规则 total_price:=rule.min_price 恰好满足门槛）
curl -s -X POST http://127.0.0.1:8082/choreography/trigger \
  -H "Content-Type: application/json" \
  -d '{"rule_id": "girl_A_dance"}'
# 预期 200：{"accepted": true, "reason": "accepted", "rule_id": "girl_A_dance", "retry_after_s": null, "code": "accepted", ...}

# 裸 API 形态（total_price 缺省 0=min_price>0 规则不命中，below_min_price 诚实门槛语义）
curl -s -X POST http://127.0.0.1:8082/choreography/trigger \
  -H "Content-Type: application/json" \
  -d '{"trigger_type": "gift", "match_value": "小心心", "gift": {"gift_name": "小心心", "total_price": 0}}'
# 预期 200：{"accepted": false, "reason": "below_min_price", ...}（规则 min_price>0 时）

# 分支预期对照表：
#   200 + accepted=true  + reason=accepted/interrupted/requeued → 画面切换（观察状态条）
#   200 + accepted=false + reason=no_match            → 没有命中启用规则（检查 match/启用状态）
#   200 + accepted=false + reason=below_min_price     → 价值未达门槛（rule_id 形态已合成达标载荷，不会出现）
#   200 + accepted=false + reason=throttled{retry_after_s} → 节流（等 retry_after_s 重试）
#   200 + accepted=false + reason=hold{retry_after_s} / in_progress → 序列执行中（等待/观察）
#   200 + accepted=false + reason=disabled            → enable=false 或 worker 未运行（检查配置与启动日志）
#   403  → 非本机来源被拒（编排控制仅限本机；远程控制官方不支持，须自建反代+鉴权）
#   422  → 载荷校验失败（trigger_type 值域外/match_value 超 500 字符/非对象）
#   503  → 编排器未装配（运行系统未启动）

# 查看运行状态（GET 只读，非本机也放行——F6）
curl -s http://127.0.0.1:8082/choreography/status
```

**api_host 暴露面（CEO native 发现12）**：编排控制端点（POST rules/trigger）仅回环来源可用（`ipaddress.is_loopback`，覆盖 127.0.0.0/8 与 ::1，非本机 403）；main API 缺省绑定 `0.0.0.0`（web_server.py:307）——局域网内其他设备可读 GET status 但不能操纵编排；远程控制官方不支持。
**直播中警告（Eng F7）**：模拟触发与真实事件同链路——**直播进行中模拟触发会干扰实时编排状态（计数/hold/节流/序列打断），建议闲时使用**。

## 6. 故障排查

| 症状 | 三段式 |
|---|---|
| 规则未生效 | problem=规则被拒或 disabled；cause=结构校验/段名缺口（见启动日志三段式）；fix=按日志逐条修正 |
| 画面停在序列段不回默认 | problem=恢复轮播重试耗尽；cause=/segment/schedule 连续失败；fix=查 EDTalk 服务后手动切回或重启 |
| 步骤跳得太快 | problem=降级计数增长；cause=切段失败或音频未入队（file 门拦截/文件缺失）；fix=查 play_audio.enable 与文件路径 |
| 编排器无响应 | problem=worker 未启动；cause=enable=false 或 EDTalk 客户端缺失；fix=检查配置与 EDTalk 启用状态 |
| 已启用但状态条显示 worker 未运行 | problem=worker 启动失败或死亡；cause=见启动日志 `[编排] worker 启动失败` 三段式；fix=按日志修复后管理页重新「应用」（热启动） |
| 模拟触发未生效 | problem=模拟触发被拒绝；cause=reason 字段（no_match/below_min_price/throttled/hold/in_progress/disabled——见 §5 对照表）；fix=按响应体 fix 字段处置 |

**回滚**：`choreography.enable=false` + 重启（分钟级）；模块隔离，git revert 可整体退役。
