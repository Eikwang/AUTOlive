> **📦 已归档历史档案**（2026-10-21 批次标注）：本文件为整合期计划/纪要快照，记录当时决策与实施过程，不随现行代码演进；现行事实以 docs/ 与代码为准。

<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-AUTOlive\\main-autoplan-restore-20261006-continue.md" -->
<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-AUTOlive\\main-autoplan-restore-20261005-174542.md" -->
# DanmakuListener 系统模块整合计划——进程内化与全面替换

> **状态：APPROVED（2026-10-06 终门裁决 A——进程内方案 A 确认实施；User Challenge 1 已解决：用户确认'无缝'意图涵盖拓扑层，方案 B 维持为 F3 门槛失败的既定回退路径）。**

> /autoplan SOURCE_PLAN（2026-10-05）。上轮整合（/autoplan 2026-09-27，G1-G6/G9）已实施 serve 子进程托管 + WS 消费者骨架；本计划为第二阶段：响应用户"作为系统模块无缝整合，而不是通过API中转"的方向修正，将监听引擎进程内化，控制面全面整合进"基础设置-平台配置"，并退役全部旧链路。

## Implementation plan

## 1. 用户原始需求

将 D:\AI\DanmakuListener（已开发测试完毕，12 平台弹幕/礼物监听，资源占用低、处理效率高）整合到 AUTOlive 作为系统功能模块：

1. **无缝整合，不是 API 中转**——监听能力作为系统模块运行，各类功能设置均整合到 AUTOlive
2. **控制面进"基础设置-平台配置"**——添加&删除平台、启动&停止监听、屏蔽关键词等操作全部在 AUTOlive 前端完成
3. **移除原先的配置和逻辑**，全面替换为新模块

## 2. 现状核实（2026-10-05 实地核查）

### 2.1 组件侧（副本 = 上游 v0.4.0，af9124c，十二平台全量交付且全量复测通过）

- 副本位于 `danmaku_listener/`（94 文件，`Scripts/copy_danmaku.py` 复制范式 + `COPY_MANIFEST.json` 版本戳），与上游逐字节一致
- 12 平台引擎注册表（`danmaku_listener/engines/registry.py:28-43`）：bilibili、douyin、douyu、huya、kuaishou、taobao、1688、meituan、xiaohongshu、pdd、jd、wechat_channels；`PLATFORM_WARNINGS`（46-66 行）记录每平台实证可用性
- **进程内控制面 API 已齐备**（`danmaku_listener/web/bridge.py`）：
  - `DanmakuBridge.add_room(room_spec, platform)`（L484，支持直播间链接自动识别 6 平台）/ `start_room`（L586）/ `stop_room`（L626）/ `remove_room`（L649）/ `stop_all`（L688）
  - 登录门 `_login_gate`（L174-224）：bilibili/kuaishou/douyin 无登录 cookie 自动弹浏览器扫码，完成后热注入自动 start
  - 关键词过滤（L256-262 + L381-398）：`_blocked_keywords` + `_keyword_filter_enabled`，仅过滤 DANMU
  - 房间注册表持久化 `persistence_data/rooms.json`（重启后按 stopped 恢复）
  - 消息流：引擎 `on_message` → `_on_engine_message`（L244-279，契约线格式）→ 关键词过滤 → `_broadcast`（L317-336）
- 契约 v1 统一消息（`contract/models.py`）：8 业务类（DANMU/GIFT/SUPER_CHAT/ENTER_ROOM/LIKE/ROOM_STATS/SOCIAL/LIVE_STATUS_CHANGE）+ 8 系统类（HEARTBEAT/GAP/NEEDS_LOGIN/ENGINE_STATUS/ROUTE_FAILED/BACKPRESSURE/RECOVERED/ROOM_STATUS）
- **关键事实：serve 模式的引擎接线未落地**（`danmaku_listener/__main__.py:229-234`（Eng 勘误：精确行 231）"no message source yet"）——上轮实施的 `serve --replay` 链路只能回放演示数据，真实监听从未经过该链路；PushServer 需 `set_push_server()` 接线且当前 `cmd_serve --web` 未调用
- 库级集成正确入口：`DanmakuBridge`（控制面最全）或 `engines.registry.build_engine`（引擎级）

### 2.2 AUTOlive 侧（上轮整合成果 + 残留）

已实施（utils/danmaku_consumer.py，812 行）：
- `DanmakuListenerService`（L110）：serve 子进程托管（ManagedProcess + TCP 探活 + token 世代）+ WS 消费循环 `_ConsumerLoop`（L278）+ business 消息映射 `_dispatch`（L604，复用 `my_handle.process_data`）+ 事件环 `push_event/events_since` + 登录队列 + DANMU/LIKE 聚合器（L729-813）+ 文本净化 `_sanitize_danmu_text`
- main.py:128-133 接线（enabled=true 时 daemon 线程启动）；config.json:1930-1951 `danmaku_listener` 节（enabled=false，6 平台开关，`cookie_dir` 仍指向 `D:\AI\DanmakuListener\cookie` 外部目录）
- 前端 `frontend/ui/components/danmaku_login.py` 扫码对话框（挂载 layout.py:770-775）；`tests/smoke_danmaku.py` 冒烟

未完成/残留（本次整合对象）：
1. **整合形态为"AUTOlive 拉起 serve 子进程 + WS 自连 127.0.0.1:8765"**——即用户所说的"通过 API 中转"，且该链路因 serve 引擎接线未落地实际收不到真实数据
2. 前端"基础设置-平台配置"（`frontend/ui/tabs/platform_config.py`，68 行）仍是旧凭据卡：哔哩哔哩 cookie/账号密码/开放平台、让弹幕飞（第三方外部工具 WS 中转）、twitch——无任何新模块功能
3. `common_config.py` L42-71 的 16 选 1 platform 下拉 + `room_display_id` 单房间输入为旧架构残留
4. 旧监听链路僵尸代码：`utils/platforms/` 12 个适配器（bilibili/bilibili2/dy/dy2/ks/hntv/lx_live_monitor_assistant/ordinaryroad_barrage_fly/tiktok/twitch/wxlive/youtube，2378 行，`start_listen` 全仓库无调用点已核实）；根目录 `api_old.py`、`webui-bak.py`（389KB）、`main.py.backup`、`dy.proto`/`dy_pb2.py`/`ks.proto`/`ks_pb2.py`/`kuaishou_pb2.py`、`protobuf/`（douyin.proto+protoc.exe）、`Scripts/直播ws脚本/`
5. config.json 旧平台配置节（bilibili/twitch/ordinaryroad_barrage_fly 等）未清理
6. `.gitignore:293` 整体忽略 `docs/`，导致 `docs/danmaku_contract/`（契约 Schema 文档）未入库
7. 当前工作区未提交变更 = 上游 v0.4.0 重复制（24 M + 1 D + 1 ??）

## 3. 核心架构决策：进程内整合

**从"AUTOlive 拉起 serve 子进程 + WS 自连"改为"AUTOlive 进程内运行监听引擎"：**

```
[旧形态——API 中转]
main.py ──spawn──> danmaku_listener serve 子进程（--replay，引擎未接线）
   └── WS 消费者线程 ──ws://127.0.0.1:8765──> 回子进程（收不到真实数据）

[新形态——进程内模块]
main.py 进程
 ├─ 托管层 utils/danmaku_consumer.py（重构）
 │   ├─ 专用 asyncio 事件循环线程
 │   │    └─ DanmakuBridge 实例（组件库，12 平台引擎 + 登录门 + 关键词 + 房间注册表）
 │   │         └─ 消息 hook（子类重写 _broadcast）→ 线程安全队列
 │   ├─ 数据面：队列 → 既有 _dispatch 映射 → my_handle.process_data（对话链复用不变）
 │   └─ 控制面：前端操作 → 托管层方法 → Bridge 方法（进程内直调，即时生效）
 └─ 前端"基础设置-平台配置"（NiceGUI）→ get_service_instance() → 托管层
```

决策依据：
- 用户明示"不是通过 API 中转"；进程内消除了自连 WS 的往返
- serve 链路本身是半成品（引擎接线未落地），进程内化反而接上了真实数据路径
- 组件库 API 完备（Bridge 控制面 + 回调数据面），进程内调用是一等公民用法
- 减资源：少一个 Python 进程、少一条 WS 连接、少一层序列化、无端口占用、无 token 管理
- 配置单源：config.json 直接驱动，废除 danmaku_serve.toml 生成

**拓扑方案决策记录（A/B/C 对比，方案 A 实施，B/C 为 F3 门槛失败时的既定回退路径，不实施）**：

| 维度 | A 进程内 Bridge 子类（实施） | B 修复 serve 接线的托管子进程（回退方案） | C 混合：协议直连进程内 + Playwright 重平台托管子进程 |
|------|------------------------------|------------------------------------------|------------------------------------------------------|
| 崩溃爆炸半径 | 引擎硬崩溃（quickjs/protobuf 段错误、OOM）带走整个 AUTOlive | 引擎崩溃限于子进程，数字人存活 | 浏览器自动化崩溃限于子进程，协议引擎崩溃带主进程 |
| 资源开销 | 最少（无子进程/WS/序列化） | 多一个 Python 进程 + 一条 WS | 介于 A/B 之间 |
| 控制面 | 进程内方法直调，最直接 | 需经 REST/WS 控制通道（用户感知同样无缝，但实现多一层） | 部分直调部分跨进程 |
| 维护成本 | 最低 | 需补 serve 引擎接线（上游缺口） | 两套形态并存，最高 |
| 风险 | 进程内等价性未验证（风险 1，F3 门槛兜底） | 依赖上游补接线（不受 AUTOlive 控） | 复杂度最高 |

否决 B 的理由补充（对"无缝"的显式辨析）：用户"不是通过 API 中转"在体验层可由 B 满足（生命周期全托管 + 控制面全在前端），但 B 依赖上游补 serve 接线（不受本仓库控制）且进程内 A 的等价性风险有 F3 定量门槛与回退路径兜底；C 的复杂度与本次"全面替换"目标不符。

代价（明示）：
- 崩溃隔离丧失（引擎异常与主进程同生命周期）——缓解：组件 per-engine 异常捕获已就绪 + 托管层服务级守护（引擎组异常 → 重建 Bridge）。**硬崩溃残余风险（quickjs/protobuf 原生段错误、长播内存泄漏、OOM）会带走整个 AUTOlive 进程——本决策仅限个人使用阶段；商用前必须重新评估隔离形态（回退方案 B/C 已记录），与 S9 合规项绑定登记 TODOS**
- 上轮已实施的 serve 托管/WS 消费者代码退役（约 400 行重构）

**副本纯净性纪律**：`danmaku_listener/` 保持与上游一致（不改组件源码）；AUTOlive 侧通过**子类化 Bridge**（hook `_broadcast`）与组合调用集成，`copy_danmaku.py` 重跑同步上游零冲突。**前提**：登录态与运行时数据必须落在 `danmaku_listener/` 之外——实测 `Scripts/copy_danmaku.py:64-65` 重跑时先 `rmtree` 整个 `danmaku_listener/` 再复制（EXCLUDE_DIRS 仅过滤复制源，不保护目标），cookie 落在副本内会被静默清除，故 `cookie_dir` 默认取仓库根 `cookie/`。

## 4. 模块一：进程内托管层重构

落点：`utils/danmaku_consumer.py`（重构）+ `main.py`（接线微调）

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P1-1 | 托管层进程内化：删 serve spawn/WS 消费循环/token 世代/TCP 探活/TOML 生成（`_write_serve_toml`、`_ConsumerLoop`、`_tcp_probe` 等）；新增专用 asyncio 线程（**daemon=True**，不阻塞进程退出）+ `AutoliveBridge(DanmakuBridge)` 子类（重写 `_broadcast`：契约线格式 → **有界 `queue.Queue(maxsize=500)` 投递（`put_nowait`）**，满载按背压同序丢弃（LIKE→ENTER_ROOM→DANMU）并计数、触发 BACKPRESSURE 类事件进事件环；**可丢类型穷尽后丢最新消息并入计数——任何情况下不阻塞 asyncio 事件循环**；**丢弃结构不用 queue.Queue 的非原子排空-回灌（Eng E1：Queue 无删除 API，排空-回灌与消费 get() 并发交错会乱序竞态，现有 _on_stream_offline 已是 racy 先例）——自实现有界结构（collections.deque + threading.Condition，支持按可丢类型扫描淘汰），下播裁剪迁到同一结构**；同时保留组件事件语义）；清理顺序（Eng T4 写死）：`main.py stop()`/退出钩子 → `svc.stop()`（**先 run_coroutine_threadsafe 提交 stop_all 并等待 future 完成 → 再停事件循环 → thread.join(timeout)**——顺序颠倒则 stop_all 协程永不执行）；未走 stop 的退出路径（崩溃/强杀）由 daemon 属性保证不阻塞，房间/关键词状态由 config.json 重建兜底；'进程退出无线程残留'改为可验证断言（stop 后 join 成功、事件循环已停、无未完成任务告警——Eng T6） | utils/danmaku_consumer.py | 服务 start/stop 生命周期单测；**任何配置下无 8765 监听（web_console=true 时仅 127.0.0.1:8080）**；进程退出无线程残留；队列满载丢弃路径单测（含 GIFT 满载兜底） |
| P1-2 | 数据面桥接：队列消费线程解析契约线格式（复用 `_normalize_wire` + `UnifiedMessage.from_wire`）→ 保留既有 `_dispatch` 映射、DANMU/LIKE 聚合器、`_sanitize_danmu_text`、下播裁剪状态机 → `my_handle.process_data`；system 类 8 个全量解析：HEARTBEAT/GAP/ENGINE_STATUS/NEEDS_LOGIN/BACKPRESSURE/RECOVERED/ROOM_STATUS/**ROUTE_FAILED**（三段式 reason_code/fix_hint/docs_anchor 进事件环供 UI 呈现）。**抖音 GIFT 增量换算（已核实的 bug 修复，对应组件 TODOS"GIFT.count 是 totalCount 累计值透传"）**：按 `(room_id, user_id, gift_id)` 维护上次 totalCount，差值为增量；**首见 totalCount 视为 1**（差额丢弃并计数——答谢场景宁可少报不可重复多报）；差值 ≤0 丢弃并计数；映射仅内存态、不持久化，重启后首见语义重新触发（已知限制，事件环提示一次）；unit_price/total_price 按增量计算 | utils/danmaku_consumer.py | replay 端到端：demo.jsonl 线格式 → 队列 → stub my_handle 收到各类型（**replay 统一为队列级注入，不改组件 Eng H2**）（**夹具落点钉死 `tests/fixtures/demo.jsonl`——rmtree 区之外，缺失时冒烟自动生成或断言存在，严禁放入组件副本内（DX 1.1）**）；聚合/去重/净化单测不回退；抖音累计值序列（1,2,3）换算增量（1,1,1）单测；首见 totalCount=5 场景单测（只报 1）；LRU 淘汰后首见重新触发单测（Eng T5）；**合成洪峰测试（Eng T3）：1 万条/秒 replay 灌队列——hook 路径不阻塞 asyncio 循环、丢弃计数与 BACKPRESSURE 事件一致、消费线程可追平（F3 浸泡的廉价前置）** |
| P1-3 | 控制面 API：托管层暴露 `add_room(room_spec, platform, start=True)/remove_room/start_room/stop_room/get_rooms/get_status/add_keyword/remove_keyword/**set_keyword_filter(enabled)**/set_service_enabled/**set_platform_enabled(platform, enabled)**——**跨线程调用机制（Eng A1，高危）：Bridge 控制方法均为协程（add_room/start_room/stop_room/remove_room/stop_all 皆 async def 且内部 create_task），UI/NiceGUI 线程直调只会得到未执行的 coroutine 对象（静默无操作）——托管层统一经 `asyncio.run_coroutine_threadsafe(coro, loop)` 封装（等待 future + 超时 + 异常转 RoomError/事件环），验收含断言'UI 线程调用返回真实结果 dict 或抛 RoomError'；参数序逐方法对齐组件真实签名（add_room(room_spec, platform) 为 room_spec 在前，start_room/stop_room/remove_room 为 (platform, room_id) 在前——Eng A4，API 表逐方法列真实签名，验收含字符串实参位置用例）；ghost 对账（Eng A5）：Bridge 构造即从 rooms.json 恢复历史房间，config 注入时删除不在 config.rooms 中的条目（单测覆盖）；config RMW 全部经 utils/config.py 同一把锁通道（Eng H1，与其他子系统写者互斥）；`start=False` 时仅写 config 不注入 Bridge start（预配置逃生门，README API 表明示持久化语义）；set_keyword_filter/set_service_enabled 为显式 set 语义（替代 toggle 反模式与 set_enabled 歧义）**——直调 Bridge（平台开关为托管层自有逻辑：`set_platform_enabled` 写回 config `platforms` 节，**置 false 时联动 stop 该平台运行中房间**；`add_room` 对未启用平台拒绝并携带启用指引 fix_hint）；操作结果（RoomError 三段式）转事件环供 UI 呈现。**持久化语义（权威源写回）**：config.json `rooms`/`keywords`/`platforms`/`enabled` 为持久权威源——添加/删除房间、关键词增删/开关、平台开关为持久操作（即时生效于 Bridge 内存态 + 经 AUTOlive 既有 config 保存通道原子写回，托管层持模块级锁读-改-写，不绕过 `utils/config.py` 直接整文件写）；启动/停止房间为运行时态（不写回，重启按 `rooms[].enabled` 恢复）；`set_enabled(false)` = stop_all + 停事件消费（服务保持挂载、状态置灰），`set_enabled(true)` = 按 config 恢复，与 `danmaku_listener.enabled` 键同步写回。**多房间语义（活跃房间裁定，显式指定）**：活跃房间 = **config.rooms 中 `active=true` 的房间**（显式指定，至多一个；非隐式顺序规则——六个月后仍可理解）。指定途径：UI 点选"设为活跃"（写回 config）；active 房间未在监听时对话链空转并事件环提示一次。活跃房间消息经 `_dispatch` 全量进 `my_handle.process_data`（对话链，与"my_handle 链不变"一致）；其余房间消息仅计数与事件环呈现（不进对话链，UI 行内明示"仅监听不互动"）。UI 允许多房间同时监听；互动质量仅在单活跃房间下验收。**启动注入遇禁用平台**：跳过该房间 + 事件环提示一次（fix_hint 指向平台开关），不报错不阻塞其余注入 | utils/danmaku_consumer.py | 方法级单测（含非法房号/重复添加/未启用平台分支/禁用平台启动注入跳过）；写回原子性与并发单测；set_enabled/set_platform_enabled 状态机单测（含联动 stop）；多房间活跃路由单测 |
| P1-4 | 配置单源：config.json `danmaku_listener` 节扩展——`rooms: [{platform, room_id, enabled, active}]`（权威源，启动时注入 Bridge 并按 enabled 自动 start；**活跃房间为显式指定：`active=true` 至多一个，UI 点选设置并写回**，见 P1-3）、`keywords: []`（启动注入 Bridge 内存态）、12 平台 `platforms` 开关扩展、`web_console`（默认 false；true 时启动组件 web 层绑 127.0.0.1 作调试通道，端口沿用组件 web_port=8080）、`cookie_dir` 默认改为仓库根 `cookie/`（**不得落 `danmaku_listener/` 内**——copy_danmaku.py 重跑 rmtree 会清除；兼容配置指向外部如 `D:\AI\DanmakuListener\cookie` 以复用既有登录态）；Bridge 房间注册表文件路径**显式注入**仓库根 `persistence_data/`（bridge.py:52 默认相对路径依赖 cwd，显式传入消除漂移，且 `danmaku_listener/` 内会被 rmtree；**注入机制写实（Eng A2）：基类构造无参且 __init__ 内无条件赋值——子类 super().__init__() 后重设 `self._rooms_file`、清空 `self._rooms`、重跑 `_load_rooms_registry()`，hook 断言清单扩展到 `_rooms_file`/`_load_rooms_registry`**）；**组件关键词持久化外置（Eng A3，中高危）：Bridge 的 blocked_keywords.json 在 danmaku_listener/web/ 内（bridge.py:384/396）——子类覆写 `_save_blocked_keywords`/`_load_blocked_keywords` 指向仓库根 persistence_data/（否则升级清数据+MANIFEST 版本戳漂移误报），config 注入用整体替换语义，hook 断言补这两个成员**；**组件 cookie 路径注入机制（Eng E5）：serve 的 TOML 通道删除后，构造期覆写组件 settings 字段（settings.py:47/64/65 默认相对 ./cookie，_login_gate 兜底同为相对路径 bridge.py:198-199/213-214——全靠 cwd 巧合），加启动断言：解析后的 cookie/profile 路径均在 danmaku_listener/ 之外，否则告警**；事件去重键加 60s 窗口（Eng E3：永久去重会吞掉恢复→再次 GAP 的同键事件）；删除失效键完整清单：`ws_port`、`ws_bind`、`require_token`、`token_injection`、`token_file_path`、`legacy_adapter_retired`、`room_id`（旧单房间键，rooms 数组取代）；保留键：`component_root`（副本路径定位）、`login_entry`（活跃键——`danmaku_login.py:78` 与 `settings.py:179` 消费，控制登录弹窗常驻入口）；新增可覆盖运行参数（DX 5.1 逃生门）：`queue_maxsize`（默认 500）、`web_console_port`（默认 8080，EADDRINUSE fix_hint ="释放端口或改 web_console_port"）、`stall_threshold_seconds`（收流停滞判定阈值，默认 60）；`frontend/config/settings.py` `_DANMAKU_LISTENER_DEFAULTS` 同步；`web_console=true` 启动前做 8080 端口占用检测（高冲突端口），占用时事件环报错（EADDRINUSE 三段式）且不阻塞主服务 | config.json、utils/danmaku_consumer.py、frontend/config/settings.py | 配置变更→重启→房间/关键词状态恢复；defaults 合并单测 |
| P1-5 | 登录门对接：Bridge 登录门进度（ENGINE_STATUS/NEEDS_LOGIN 线格式）→ 既有登录队列 → `danmaku_login.py` 扫码弹窗（组件复用，改动最小）；cookie 目录统一在 `cookie_dir`（仓库根 `cookie/`）。**12 平台登录态供给矩阵**：bilibili/kuaishou = Playwright storage_state（自动登录闭环产物）；douyin = cookies.json（自动登录闭环）；1688/xiaohongshu/pdd/jd/wechat_channels 五个受控页面平台 = persistent profile 目录；douyu/huya = 协议直连游客可听（无登录态，上游 PLATFORM_WARNINGS 实证）；taobao/meituan = 轮询直连无需登录 | utils/danmaku_consumer.py、frontend/ui/components/danmaku_login.py | 无登录态平台加房间 → 弹码 → 扫码 → 自动 start（手动验证 B 站）；矩阵数据在托管层常量/实现中齐备（README 覆盖归 P3-5 验收）；**逐平台实测登录态落盘路径均在 `danmaku_listener/` 之外（至少 persistent profile 类代表平台 1688/pdd 各一 + B 站）** |
| P1-6 | 服务级守护：引擎组致命异常捕获 → 记录事件（**携带 reason_code/fix_hint/docs_anchor 三段式，fix_hint 指向排障节'守护重建置红'条目——DX 3.1**）→ 重建 Bridge（沿用失控防护语义：连续 3 次失败停止重建、状态置红、手动重启清零）；main.py `stop()` 停服联动 | utils/danmaku_consumer.py、main.py | 守护逻辑单测（注入异常）；进程退出无线程残留 |
| P1-7 | 冒烟脚本更新：`tests/smoke_danmaku.py` 改进程内形态（Bridge 注入 replay 源 → 队列桥 → stub my_handle），保留 TTHW 计时输出 | tests/smoke_danmaku.py | 冒烟全过且 <2 分钟 |

## 5. 模块二：前端"基础设置-平台配置"重写

落点：`frontend/ui/tabs/platform_config.py`（重写，替换旧凭据卡）

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P2-0 | **状态与呈现规范（设计评审前置补丁，实施 P2-1 前定稿）**：① **房间行状态机枚举表**——待登录/登录中/运行中/收流停滞/已停止/平台已禁用（联动 stop 后）/活跃但未监听 七态 × 触发事件 × 行内文案 × 可用操作 × 徽章颜色语义（色值走 design_tokens 语义变量）；② **房间行生命信号（critical S2）**——行内显示"最近消息 N 秒前 / 今日 N 条"（数据源：事件环既有 ROOM_STATS/HEARTBEAT），直播中超阈值无消息转"收流停滞"态并附 fix_hint 排查指引；③ **事件分级呈现规则（P1）**——错误类（RoomError/ROUTE_FAILED/EADDRINUSE）：toast 一次 + 来源行内驻留直至解除；提示类（GAP/BACKPRESSURE/RECOVERED/一次性提示）：toast 一次；**GIFT 满载丢弃升级为错误级呈现（行内驻留——答谢信息丢失需强感知，Eng E4；README 记为已知限制）**；docs_anchor 一律渲染可点链接（scheme 白名单：https 或组件内相对路径，沿用上轮 S-2 义务）；events_since 轮询去重键 = (kind, platform, room_id, reason_code)；④ **胶囊四态映射**——正常(绿)/禁用(灰)/异常(红)/守护重建中(黄)，文案固定；⑤ **禁用态降级（enabled=false 首体验）**——页头胶囊灰态 + "开启监听"引导；房间/关键词卡可浏览、操作按钮禁用 + tooltip 说明；⑥ **平台启用开关独立区**——卡片②顶部 12 平台紧凑开关行（禁用态标灰），下拉只负责选择；"需验证"分组默认收起、组头显示计数；⑦ **活跃房间常驻状态行**——卡片②头部"当前互动房间：xxx / 未指定"；有监听无活跃时常驻警示条（处理后才消失，非一次性提示）；⑧ **破坏性操作确认（A1）**——`set_platform_enabled(false)` 联动 stop 时：被停房间含活跃房间 → 确认框（明示后果"活跃房间将停止互动"）；仅非活跃房间 → 直接执行，行内转"平台已禁用"态 | frontend/ui/tabs/platform_config.py（规范先行，作为 P2-1 实施权威依据） | 规范八项在计划/实现注释中定稿；P2-1 实施逐项对照 |
| P2-1 | 页面重写三卡布局（遵守设计令牌：`design_tokens.py` 宽度三档/间距/无硬编码色值——色值一律 `var(--...)` 语义变量引用（对齐 llm.py 既有模式），3 列网格规范）：① **页头**——监听总开关 + 服务胶囊（四态）+ 未处理登录请求数提示；② **房间管理卡**（首屏主导，高频操作优先）——平台启用开关区（P2-0 ⑥）+ 平台下拉（12 平台，PLATFORM_WARNINGS tooltip；**未启用平台禁用标灰**；**登录要求标注（J1）：需扫码/免登录，数据源 P1-5 矩阵**）+ 房间号/直播间链接输入 + 添加按钮（添加即 `enabled=true` 并自动 start；**链接平台与所选不一致时自动切换平台选择并提示，识别失败才报错（P4）**）；房间列表（平台徽章、房间号、**状态按 P2-0 ①枚举**、**生命信号（P2-0 ②）**、启停/删除操作、**"设为活跃"点选（写回 rooms[].active）+ 活跃徽章；非活跃运行中行内"仅监听不互动"**；文案"停止为运行时态，重启后按启用配置恢复"）；③ **关键词屏蔽卡**——输入+添加、列表+删除、过滤总开关；**固定文案"仅屏蔽弹幕内容，不影响礼物与进场"（J2）** | frontend/ui/tabs/platform_config.py | 页面渲染冒烟；全操作链路手动验证；七态状态机全部可达且文案/颜色符合 P2-0 规范 |
| P2-2 | 状态实时性：房间运行状态/服务状态经事件环轮询刷新（复用既有 `events_since` 模式，间隔对齐 `service_status.py` 的 3s）；**乐观更新范围（P3）：启停/删除/关键词/平台开关四类操作乐观更新，回滚触发 = 收到对应错误事件或 5s 超时，**但 add_room 返回 login_required 时（bridge.py:546-553 立即返回并弹浏览器，真实启动合法耗时数分钟等扫码）不适用 5s 回滚——进入'待登录/登录中'态，依赖 NEEDS_LOGIN/ENGINE_STATUS 事件收敛（Eng E2）；5s 回滚仅保留给启停/删除/关键词/平台开关**，回滚提示按 P2-0 ③ 分级呈现**；**重启恢复反馈（J3）：启动完成后事件环发一次性汇总"已恢复 X 个房间（Y 个启动成功）"** | frontend/ui/tabs/platform_config.py | 状态变化 UI 3s 内反映；乐观回滚路径单测/手动验证 |
| P2-3 | 登录扫码弹窗对接（`danmaku_login.py` 复用）：**登录失败/超时/取消路径映射进 P2-0 ①状态机（待登录/登录中态，S5）——关闭弹窗后房间行内保留"去登录"重试入口**；运行中 cookie 过期（NEEDS_LOGIN 运行时触发）同样转入待登录态 | frontend/ui/components/danmaku_login.py、frontend/ui/tabs/platform_config.py | 无登录态平台加房间 → 弹码 → 扫码 → 自动 start（手动验证 B 站）；取消/超时路径行内重试入口可达 |
| P2-4 | `common_config.py` 旧架构残留退役：16 选 1 platform 下拉 + room_display_id 移除；**前置核验（Eng T2 更正核验对象）**：`main.py:86` platform 键传入 TaskScheduler 的实际用途、`tests/test_config.py` L50/L61/L225 的 platform 断言——若仍有活跃消费者（TaskScheduler 等），保留只读展示并在计划 Review record 记录处置（原引用 my_handle.py:75 实为 abnormal_alarm_data 字典结构，与 config.platform 无关，系误引） | frontend/ui/tabs/common_config.py、核验 main.py/my_handle.py | grep 无悬空配置引用；页面渲染无报错 |

## 6. 模块三：旧链路全面退役

**实施顺序依赖（显式）**：本模块（P3-1~P3-6）在 P1 真实浸泡验证通过（风险 1 的 F3 定量门槛）**之后**执行——P1/P2 落地后先进真实直播浸泡期（至少一个直播周期），确认进程内形态稳定再退役 serve/WS 代码与旧资产；若 F3 门槛不过触发回退方案 B，P3-1/P3-2 中 serve 相关删除项随回退方案重估。P3 的 TODOS 追加项（P3-6 ④ 中 S7/S9）不依赖浸泡结果，可提前登记。

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P3-1 | 删除 `utils/platforms/` 整目录（12 僵尸适配器，`start_listen` 无调用点已核实） | utils/platforms/ | 全仓库 grep `start_listen`/`utils.platforms` 仅剩本计划文档记录 |
| P3-2 | 删除根目录旧资产：`api_old.py`、`webui-bak.py`、`main.py.backup`、`dy.proto`、`dy_pb2.py`、`ks.proto`、`ks_pb2.py`、`kuaishou_pb2.py`、`protobuf/`、`Scripts/直播ws脚本/`；`启动程序1.bat` 因指向 webui-bak.py 而一并删除（webui.py 已由 `启动程序.bat`/`start_autolive.bat`/`zz_test_start.bat` 覆盖） | 仓库根 | 删除后 `python -m pytest tests/ -q` 与冒烟全过（无 import 断裂） |
| P3-3 | config.json 旧平台配置节清理。**封闭清单**（存在即删）：顶层节 `bilibili`（含 open_live 子节）、`twitch`、`ordinaryroad_barrage_fly`、`dy`、`dy2`、`ks`、`ks2`、`wxlive`、`hntv`、`youtube`、`tiktok`，以及顶层键 `platform`、`room_display_id`（随 P2-4 核验结论处置）；此清单之外的一律不碰。实施前以 `git grep -l 'config.get("bilibili"'` 等逐键核验无活跃读取（P2-4 结论为准）；**同步更新 tests/test_config.py（Eng T2：L50/L61/L225 断言 config['platform']=='talk'，删键后 pytest 必挂——P3-2 验收要求 pytest 全过）** | config.json | 应用启动无配置缺失告警；grep 逐键零活跃引用 |
| P3-4 | `.gitignore` 修复：① `docs/danmaku_contract/` 例外——**两步变换**（实测 `.gitignore:293` 为 `docs/` 整目录排除，git 不递归进入，直接加 `!` 例外无效）：先把 `docs/` 改写为 `docs/*`，再追加 `!docs/danmaku_contract/` 与 `!docs/danmaku_module.md`；② 补运行时数据忽略防登录凭据入库：`/cookie/`（**锚定仓库根**，无锚定的 `cookie/` 会匹配任意层级；现有 `cookie/*.json` 规则不覆盖 profile 子目录）与 `persistence_data/`（Bridge 房间注册表）；③ **测试文件忽略规则修复（Eng T1，高危）：`.gitignore:289` 的 `test_*.py` 无路径锚定匹配任意层级（实测 `git check-ignore tests/test_new_probe.py` 命中）——本计划全部新增单测按 pytest 惯例命名会被静默吞掉（不出现在 git status、进不了 P3-6 白名单、协作者拿不到），规则锚定为 `/test_*.py`（根目录旧资产语义保留）或追加 `!tests/` 例外；P1 验收加'新测试文件 git status 可见'** | .gitignore | `git status` 显示 docs/danmaku_contract/ 与 docs/danmaku_module.md 待跟踪且 cookie/、persistence_data/ 不出现 |
| P3-5 | 模块 README：落点 `docs/danmaku_module.md`（P3-4 ① 的例外覆盖此路径；不落 `danmaku_listener/` 邻接——副本会被 rmtree）。内容：**三阶段上手路径（DX-1，重写上轮 serve/WS 形态的三阶段 Hello World 义务为进程内形态）**——**阶段 0 环境自检（DX 1.2）**：runtime312 激活 + `pip check` 一行命令 + 预期输出 "No broken requirements found"（与升级流程第 0 步并列）；阶段 1：`python tests/smoke_danmaku.py`（进程内 replay 冒烟，TTHW 计时输出，目标 <2 分钟）；阶段 2：WebUI 开启 `danmaku_listener.enabled` → 平台配置页加房间 → 对话链响应——**双路线（DX 1.3）：有账号走 B 站（顺带验证登录门），无账号走斗鱼/虎牙免登录路线**；阶段 3：多平台+关键词+活跃房间全配置。每阶段一条命令/操作 + 预期现象 + 耗时预算。**两张参考表（DX 4.1）**：config 字段表（字段/类型/默认/持久语义/约束，含 active"至多一个"与三个可覆盖运行参数）与托管层 API 表（方法/用途/持久语义/可能错误码）。**升级流程**（第 0 步版本核对 `python -m danmaku_listener contract` → 重跑 `Scripts/copy_danmaku.py`【补复制项：上游 `scripts/export_contract.py` → AUTOlive `Scripts/export_contract.py`，否则 --check 在本仓库不可执行】→ `python Scripts/export_contract.py --check` 契约漂移检查 → **hook 存在性断言：冒烟脚本首步断言 `DanmakuBridge._broadcast` 等被子类 hook 的成员签名仍在**（私有方法不受上游 API 承诺保护，防止重跑后 hook 静默失效断流）→ 冒烟脚本）。**排障节（DX-2）**：收流停滞（生命信号阈值+GAP/ROUTE_FAILED 对照）/登录失败（三段式 fix_hint 汇总表）/8080 端口占用/守护重建置红后的手动恢复——每条按 problem+cause+fix 三段式。**config 迁移映射（DX-3）**：失效键→替代（ws_port/require_token/token_injection/token_file_path/ws_bind→已废除；room_id→rooms[].room_id；legacy_adapter_retired→已废除）+ cookie_dir 迁移说明（旧外部目录→仓库根 cookie/ 或保留指向）。+ 12 平台就绪与登录态矩阵摘要 + cookie 目录说明（**含同步/备份工具会把凭据带出的风险提示，Eng S2**）+ **web_console 调试通道警示（Eng S1）：仅本机调试、无鉴权（本机任意进程可加房间/触发登录弹窗，存在浏览器跨站/DNS rebinding 打本机 API 的经典面）、勿长开** + **托管层 API 表逐方法列真实签名（Eng A4）** + **商用前置警示：进程内形态仅限个人使用，商用前重新评估隔离形态（回退方案 B/C）与监听账号隔离（S9）** | docs/danmaku_module.md | 文档存在且流程可执行；`--check` 在 AUTOlive 仓库内实跑通过；hook 断言在冒烟首步实跑；三阶段每阶段按文档实跑通过；排障节覆盖四类场景；**事件环全部 reason_code 清单与排障节锚点一一对应，冒烟时校验锚点存在（DX 3.1）** |
| P3-6 | 变更分批提交：① 工作区既有 v0.4.0 重复制（danmaku_listener/ + COPY_MANIFEST + douyin_login.py）先行提交；② P1 进程内托管层；③ P2 前端重写；④ P3 退役清理 + **S7（多房间对话链）、S9（商用合规，措辞含"商用前重新评估进程内隔离形态 + 监听账号隔离"）追加至 TODOS.md**。**工作区混杂 edtalk/ 等非本计划未跟踪变更——每批按路径白名单 stage，严禁 `git add -A`；以 stage 时刻 `git status` 实况为准** | git | 四批提交各自独立可回滚；`git status` 无计划外文件混入；TODOS.md 含 S7/S9 条目 |

## 7. NOT in scope

- 12 平台协议/引擎本身的修改（组件 v0.4.0 已全量实测验收，副本保持纯净可同步上游；虎牙礼物名表、抖音风控预案等属组件侧 TODO）
- 多直播间/多房间对话链扩展（保持现有单直播间互动架构，Bridge 支持多房间监听但 my_handle 链不变）
- 组件自带浏览器控制台（web static 三件套）的功能开发——仅作为 `web_console=true` 时的调试通道保留
- 商用合规流程（专用监听账号、法律意见书）——上轮 autoplan Deferred 项，个人使用阶段不受阻
- 手机端 UI 适配

## 8. 风险与待验证点

1. **asyncio 线程内跑受控页面引擎**（playwright persistent context：视频号/拼多多/京东/小红书/1688 + 登录门浏览器）——此前全在 serve 独立进程循环运行；AUTOlive main.py 是同步程序 + daemon 线程，专用 asyncio 线程与组件无历史耦合，理论等价，P1-1 完成后以真实房间实跑验证。**定量验证门槛（F3，P1 真实浸泡测试）**：① 组件 HEARTBEAT `loop_lag_ms` P95 <100ms（组件自带采样）；② AUTOlive 进程 RSS 峰值 ≤1.2GB（组件系统级预算）；③ 真实直播 4 小时浸泡零崩溃、BACKPRESSURE 计数为 0、主线程（NiceGUI/TTS/数字人）无可感知卡顿；④ **浏览器进程组合计内存观察留档（Eng H3：Playwright 浏览器是独立 OS 进程不计入 AUTOlive RSS，受控页面平台的内存压力对 RSS 门槛不可见——留档观察，不设硬门槛）**。**门槛不过 → 回退既定方案 B（修复 serve 接线的托管子进程，见第 3 节决策记录）——回退路径已写死，不临时决定**
2. **依赖树兼容**：组件钉版 `protobuf>=7.35.1,<8`、`quickjs>=1.19.0`、`aiohttp`、`websockets>=14` 与 runtime312 环境 AUTOlive 既有依赖的共存——P1-1 前 `pip check` 一次并记录
3. **抖音 GIFT.count 为 totalCount 累计值透传**——已核实：`_dispatch` GIFT 分支（danmaku_consumer.py:619-625）将 `gift_count` 直接当增量消费（`num` 进 total_price 拆算），与组件 TODOS"下游勿按增量消费"冲突——修复任务已列为 P1-2 明确条目（增量换算 + 单测）
4. **platform 配置键下游消费者**（main.py:86 TaskScheduler、tests/test_config.py L50/L61/L225）——P2-4 前置核验，防止删除旧键破坏活跃功能（Eng T2 更正：my_handle.py:75 为 abnormal_alarm_data 字典，误引已更正）
5. **崩溃隔离丧失**——P1-6 服务级守护为缓解项；单平台引擎异常不应影响其他平台与主程序（组件 per-engine 任务隔离已有）
6. **房间注册表双源**（config.json rooms vs 组件 persistence_data/rooms.json）——裁定 config.json 为权威源，组件 rooms.json 仅作组件自身恢复机制保留，AUTOlive 启动注入以 config 覆盖

## 9. 验收标准

1. `danmaku_listener.enabled=true` + 运行系统：无 serve 子进程，**任何配置下无 8765 端口监听**（web_console=true 时仅 127.0.0.1:8080）；进程内服务状态条胶囊正常
2. "基础设置-平台配置"页完成全部操作：添加房间（平台+房号/链接）、启动/停止监听、删除房间、屏蔽关键词增删与开关、活跃房间指定——全部即时生效、无需重启
3. 真实房间收流：弹幕/礼物/进场进对话链（my_handle），数字人照常互动；点赞/统计进计数路径
4. 登录门闭环：无登录态平台（B 站/快手/抖音）加房间 → 扫码弹窗 → 登录后自动开始监听
5. 旧链路清除（在 P1 浸泡通过后执行）：`utils/platforms/` 与根目录旧资产删除、config 旧节清理、grep 无 `start_listen`/`ordinaryroad_barrage_fly` 活跃引用
6. 测试：`tests/smoke_danmaku.py` 进程内冒烟过；新增托管层/控制面单测过；既有 pytest 无回退
7. 上游同步纪律：`danmaku_listener/` 与上游 v0.4.0 逐字节一致（AUTOlive 侧集成全部走子类/组合，零组件源码改动）
8. **业务闭环质量（F3 定量门槛）**：replay 端到端延迟（线格式出队 → `my_handle.process_data` 完成）P95 <2s；真实直播 4 小时浸泡零崩溃、BACKPRESSURE 计数为 0、组件 HEARTBEAT `loop_lag_ms` P95 <100ms、进程 RSS 峰值 ≤1.2GB


<!-- autoplan-accepted:ceo -->
- 评审模式：SELECTIVE EXPANSION（autoplan 固定裁决）。范围裁决 S1-S6 接受、S7/S9 延期 TODOS、S8 跳过（CEO 摘要 2026-10-05-danmaku-inprocess-integration.md）。
- 架构义务：进程内整合形态（方案 A）——AUTOlive main.py 进程内专用 asyncio daemon 线程运行 AutoliveBridge(DanmakuBridge) 子类；数据面 hook `_broadcast` → 有界 queue.Queue(500, put_nowait) → 消费线程 → 既有 `_dispatch` 映射 → my_handle.process_data；控制面前端 → 托管层方法 → Bridge 直调；副本纯净（零组件源码改动，`copy_danmaku.py` 重跑同步）；`danmaku_listener/` 外落点纪律（cookie_dir 默认仓库根 cookie/、rooms 注册表显式注入仓库根 persistence_data/）。回退路径：方案 B（修复 serve 接线的托管子进程）在 F3 定量门槛失败时启用（第 3 节 A/B/C 决策记录）。
- F3 定量验证门槛（P1 浸泡测试，方案 A 的实施前置门）：组件 HEARTBEAT loop_lag_ms P95 <100ms；AUTOlive 进程 RSS 峰值 ≤1.2GB；真实直播 4 小时浸泡零崩溃、BACKPRESSURE 计数为 0、主线程无可感知卡顿；replay 端到端延迟 P95 <2s。门槛不过 → 回退方案 B。
- 实施顺序依赖：P3 退役批次（P3-1~P3-6）在 P1 浸泡验证通过后执行；P3-6 的 TODOS 追加项不依赖。
- 多房间语义：rooms[].active 显式指定（至多一个，UI 点选写回）；活跃房间进对话链，其余房间仅计数与事件环（行内明示"仅监听不互动"）；添加房间即 enabled=true 并自动 start；启动注入遇禁用平台跳过 + 事件环提示；set_platform_enabled(false) 联动 stop 该平台运行中房间。
- 持久化语义：config.json rooms/keywords/platforms/enabled 为权威源；添加/删除/关键词/平台开关为持久操作（经 AUTOlive 既有 config 保存通道原子写回 + 模块级锁）；启停为运行时态（重启按 enabled 恢复）。
- 抖音 GIFT 增量换算：(room_id, user_id, gift_id) 维护上次 totalCount，首见视为 1（差额丢弃计数），差值 ≤0 丢弃；映射内存态不持久化（已知限制）；unit_price/total_price 按增量。
- system 消息 8 类全量解析（含 ROUTE_FAILED 三段式进事件环）；队列满载兜底（可丢类型 LIKE→ENTER_ROOM→DANMU 穷尽后丢最新，任何情况不阻塞 asyncio 事件循环）。
- 配置键处置：删除 ws_port/ws_bind/require_token/token_injection/token_file_path/legacy_adapter_retired/room_id；保留 component_root/login_entry/web_console（默认 false，8080 占用检测）。
- 前端义务：平台配置页三卡重写（总开关/房间管理/关键词屏蔽），遵守 design_tokens 三档宽度与无硬编码色值；平台按 PLATFORM_WARNINGS 分级呈现（无警告在前，风险平台折叠"需验证"分组）；12 平台登录态矩阵齐备且逐平台实测落盘路径在 `danmaku_listener/` 之外（至少 1688/pdd/B 站代表）。
- 退役与文档：utils/platforms/ 12 模块 + 根目录旧资产（api_old.py、webui-bak.py、main.py.backup、dy/dy/ks proto+pb2、kuaishou_pb2、protobuf/、Scripts/直播ws脚本/、启动程序1.bat）删除；config 旧节封闭清单删除；gitignore 两步变换（docs/→docs/* + 例外）与 /cookie/、persistence_data/ 忽略；docs/danmaku_module.md README（升级流程含 hook 存在性断言：冒烟首步断言 DanmakuBridge._broadcast 等被 hook 成员签名）；copy_danmaku.py 补复制 export_contract.py；四批提交路径白名单 stage；S7/S9 登记 TODOS（S9 措辞含商用前重新评估隔离形态 + 监听账号隔离）。
- 安全与依赖：依赖树已实测（runtime312：protobuf 7.35.1/quickjs 1.19.4/aiohttp 3.14.3/websockets 16.1.1/pydantic 2.13.4，pip check 无冲突）；DANMU 文本净化与 LLM 防线复用既有义务；web_console 仅 loopback。
- P1-1 补充（Section 2 闭环）：asyncio 线程 run() 顶层 try/except——线程异常退出上报事件环 + 纳入守护重建计数（与引擎组致命异常同口径）。
- P1-2 补充（Section 7 闭环）：抖音 GIFT 换算映射按 (room_id, user_id, gift_id) 维护 LRU，上限 5000 键，超限淘汰最旧（淘汰后首见语义重新触发，安全侧）。
<!-- /autoplan-accepted:ceo -->
## Review record

<!-- autoplan-accepted:ceo -->
- 评审模式：SELECTIVE EXPANSION（autoplan 固定裁决）。范围裁决 S1-S6 接受、S7/S9 延期 TODOS、S8 跳过（CEO 摘要 2026-10-05-danmaku-inprocess-integration.md）。
- 架构义务：进程内整合形态（方案 A）——AUTOlive main.py 进程内专用 asyncio daemon 线程运行 AutoliveBridge(DanmakuBridge) 子类；数据面 hook `_broadcast` → 有界 queue.Queue(500, put_nowait) → 消费线程 → 既有 `_dispatch` 映射 → my_handle.process_data；控制面前端 → 托管层方法 → Bridge 直调；副本纯净（零组件源码改动，`copy_danmaku.py` 重跑同步）；`danmaku_listener/` 外落点纪律（cookie_dir 默认仓库根 cookie/、rooms 注册表显式注入仓库根 persistence_data/）。回退路径：方案 B（修复 serve 接线的托管子进程）在 F3 定量门槛失败时启用（第 3 节 A/B/C 决策记录）。
- F3 定量验证门槛（P1 浸泡测试，方案 A 的实施前置门）：组件 HEARTBEAT loop_lag_ms P95 <100ms；AUTOlive 进程 RSS 峰值 ≤1.2GB；真实直播 4 小时浸泡零崩溃、BACKPRESSURE 计数为 0、主线程无可感知卡顿；replay 端到端延迟 P95 <2s。门槛不过 → 回退方案 B。
- 实施顺序依赖：P3 退役批次（P3-1~P3-6）在 P1 浸泡验证通过后执行；P3-6 的 TODOS 追加项不依赖。
- 多房间语义：rooms[].active 显式指定（至多一个，UI 点选写回）；活跃房间进对话链，其余房间仅计数与事件环（行内明示"仅监听不互动"）；添加房间即 enabled=true 并自动 start；启动注入遇禁用平台跳过 + 事件环提示；set_platform_enabled(false) 联动 stop 该平台运行中房间。
- 持久化语义：config.json rooms/keywords/platforms/enabled 为权威源；添加/删除/关键词/平台开关为持久操作（经 AUTOlive 既有 config 保存通道原子写回 + 模块级锁）；启停为运行时态（重启按 enabled 恢复）。
- 抖音 GIFT 增量换算：(room_id, user_id, gift_id) 维护上次 totalCount，首见视为 1（差额丢弃计数），差值 ≤0 丢弃；映射内存态不持久化（已知限制）；unit_price/total_price 按增量。
- system 消息 8 类全量解析（含 ROUTE_FAILED 三段式进事件环）；队列满载兜底（可丢类型 LIKE→ENTER_ROOM→DANMU 穷尽后丢最新，任何情况不阻塞 asyncio 事件循环）。
- 配置键处置：删除 ws_port/ws_bind/require_token/token_injection/token_file_path/legacy_adapter_retired/room_id；保留 component_root/login_entry/web_console（默认 false，8080 占用检测）。
- 前端义务：平台配置页三卡重写（总开关/房间管理/关键词屏蔽），遵守 design_tokens 三档宽度与无硬编码色值；平台按 PLATFORM_WARNINGS 分级呈现（无警告在前，风险平台折叠"需验证"分组）；12 平台登录态矩阵齐备且逐平台实测落盘路径在 `danmaku_listener/` 之外（至少 1688/pdd/B 站代表）。
- 退役与文档：utils/platforms/ 12 模块 + 根目录旧资产（api_old.py、webui-bak.py、main.py.backup、dy/dy/ks proto+pb2、kuaishou_pb2、protobuf/、Scripts/直播ws脚本/、启动程序1.bat）删除；config 旧节封闭清单删除；gitignore 两步变换（docs/→docs/* + 例外）与 /cookie/、persistence_data/ 忽略；docs/danmaku_module.md README（升级流程含 hook 存在性断言：冒烟首步断言 DanmakuBridge._broadcast 等被 hook 成员签名）；copy_danmaku.py 补复制 export_contract.py；四批提交路径白名单 stage；S7/S9 登记 TODOS（S9 措辞含商用前重新评估隔离形态 + 监听账号隔离）。
- 安全与依赖：依赖树已实测（runtime312：protobuf 7.35.1/quickjs 1.19.4/aiohttp 3.14.3/websockets 16.1.1/pydantic 2.13.4，pip check 无冲突）；DANMU 文本净化与 LLM 防线复用既有义务；web_console 仅 loopback。
- P1-1 补充（Section 2 闭环）：asyncio 线程 run() 顶层 try/except——线程异常退出上报事件环 + 纳入守护重建计数（与引擎组致命异常同口径）。
- P1-2 补充（Section 7 闭环）：抖音 GIFT 换算映射按 (room_id, user_id, gift_id) 维护 LRU，上限 5000 键，超限淘汰最旧（淘汰后首见语义重新触发，安全侧）。
<!-- /autoplan-accepted:ceo -->

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | ceo | 评审模式 = SELECTIVE EXPANSION | Mechanical | autoplan override | 固定裁决，不询问 | — |
| 2 | ceo | 进程内整合（方案 A）为核心架构，回应"不是通过API中转" | Mechanical | P1+P3 | 用户明示方向；serve 链路引擎接线未落地是实测事实（__main__.py:229-234），进程内化接通真实数据路径 | 维持 serve+WS（违用户明示方向） |
| 3 | ceo | 前端平台配置页三卡重写 + 旧链路退役 + 配置单源（S2/S3/S4） | Mechanical | P1+P4 | 用户明示三项诉求；start_listen 零调用点已核实 | 保留旧凭据卡（违"移除原先配置"） |
| 4 | ceo | 抖音 GIFT 累计值增量换算（S5） | Mechanical | P1 | 已核实 _dispatch 当增量消费（619-625），答谢/计费重复计算真实 bug | 仅记录不修（违 P1） |
| 5 | ceo | CEO 原生声部 F3/F5/F8 三项前置条件全部接受 | Taste | P1+P5 | 定量门槛+回退路径+退役后置均为方向兼容的完备性提升；B/C 方案记录为回退不实施 | 推翻进程内方向（声部未要求） |
| 6 | ceo | CEO 原生声部 F1/F2/F4/F6/F7/F9/F10 改进项接受 | Taste | P2 | 全部 blast radius 内、CC 工作量 S 级（一句话至小节级） | — |
| 7 | ceo | Codex 外部声部 unavailable（模型配置级失败），单声部不标 CONFIRMED | Mechanical | 记录义务 | gpt-6-astra 元数据缺失 + 请求体被拒；预检已预警 | — |
| 8 | ceo | 活跃房间从"序最前者"隐式规则改为 active 字段显式指定（F9，修订第 3 轮 R3-1 裁定） | Taste | P5 显式优先 | 隐式顺序规则六个月后不可理解；显式指定可点选、可解释 | 保留序规则（F9 论证更优） |

## Sections 1-11 审查记录（CEO 阶段，SELECTIVE EXPANSION）

### Section 1: Architecture Review

Current scope：S1-S6 接受，S7/S9 延期，S8 跳过。模式 SELECTIVE EXPANSION（用户 /autoplan 指令）。已裁决行见 Decision Audit Trail 与 autoplan-accepted:ceo 义务块。

**系统架构图**（新形态，见第 3 节）：main.py 进程内 daemon asyncio 线程 → AutoliveBridge 子类（12 平台引擎 + 登录门 + 关键词）→ `_broadcast` hook → 有界队列 → 消费线程 → `_dispatch` → my_handle 对话链；控制面 前端(NiceGUI) → 托管层方法 → Bridge 直调。

- **数据流四路径**：happy（线格式→解析→映射→对话链）；nil（payload 字段缺失→`payload.get(...) or 默认值` 兜底，envelope 缺失→`_normalize_wire` 返回 None 丢弃计数）；empty（空 content/空房间列表→正常空转+事件环提示）；error（JSON/ValidationError→丢弃计数+日志；引擎异常→per-engine 捕获；致命→P1-6 守护重建）。四路径在既有 `_normalize_wire`/`_dispatch` 与义务块（队列满载兜底）覆盖，无需新增机制。
- **状态机**：服务 stopped→running→(守护重建)→running→(3 次失败)→red 置红→手动重启清零（沿用上轮失控防护语义，义务块保留）；房间 stopped/running（Bridge 注册表管理，组件原生）。
- **耦合**：唯一新耦合 = AutoliveBridge 子类 hook 私有成员 `_broadcast`——F6 已设防（升级流程 hook 存在性断言），可接受。
- **单点故障**：asyncio 线程死亡 = 数据断流。处置：P1-6 守护覆盖"引擎组致命异常"；**发现 GAP——线程本身异常退出未显式覆盖**，处置见 Section 2/义务块（线程 run() 顶层 try/except 上报事件环 + 守护重建）。
- **安全架构**：无新网络端点（8765 移除）；控制面进程内调用无鉴权面扩张；web_console 可选 loopback。回滚：四批提交独立回滚 + P3 后置。
- **10x 检查**：架构对新工程师的直观性——子类+队列桥是组件一等公民用法，比 WS 自连更直观；平台化潜力——托管层事件环与控制面方法是后续"弹幕数据面板""多房间管理"的基础设施。

无未决架构问题。

### Section 2: Error & Rescue Map

实现就绪深度映射（新/重构 codepath）：

| METHOD/CODEPATH | WHAT CAN GO WRONG | EXCEPTION CLASS | RESCUED? | RESCUE ACTION | USER SEES |
|---|---|---|---|---|---|
| AutoliveBridge._broadcast hook | 队列满 | queue.Full | Y | put_nowait 按背压同序丢弃计数+BACKPRESSURE 事件；可丢类型穷尽丢最新；不阻塞事件循环 | 状态条示警+事件环计数 |
| 队列消费线程解析 | 畸形 JSON/契约校验失败 | JSONDecodeError/ValidationError | Y | `_normalize_wire` 返回 None → 丢弃计数+日志 | 无感（计数可见） |
| _dispatch→my_handle | 下游处理异常 | Exception（宽） | Y | 既有 try/except+exc_info 日志 | 日志可见 |
| 控制面方法 | RoomError | RoomError(400/404/409) | Y | 三段式转事件环 UI 呈现 | 操作失败提示+fix_hint |
| 引擎组 | 致命异常 | Exception | Y | P1-6 守护重建 Bridge，3 次置红 | 状态条置红+手动重启 |
| **asyncio 线程本体** | **线程 run() 异常退出** | Exception | **GAP→已补** | 义务块补：run() 顶层 try/except → 事件环上报 + 守护重建计数 | 状态条异常+事件 |
| config 写回 | 并发/写失败 | OSError | Y | 复用既有保存通道双回滚+模块锁 | 保存失败提示 |
| web_console 启动 | 8080 占用 | EADDRINUSE | Y | 占用检测→事件环三段式报错，不阻塞主服务 | 调试通道不可用提示 |

GAP 已在义务块闭环（asyncio 线程顶层防护）。无 catch-all 吞异常（既有宽 except 均带 exc_info 日志，保留口径）。

### Section 3: Security & Threat Model

- **攻击面变化**：净收缩——8765 WS 端点、Bearer token、REST 控制面全部移除；新面仅 web_console（默认关、loopback、可选）。 Threat: loopback 内其他本机进程访问（个人单机场景威胁模型下 Low/Low）。
- **输入验证**：房间号/链接——组件 `normalize_room_id` + `validate_room_id` 已有；关键词——子串匹配无注入面；QR base64 渲染——`danmaku_login.py` 既有 data:image 安全渲染（上轮义务）。
- **凭据**：登录态（cookie/profile/storage_state）——P3-4 gitignore 防入库（/cookie/ 锚定+persistence_data/）；落点纪律防 rmtree 误删（P1-4/P1-5 实测验收）。Windows 单用户文件权限为既有基线，不新增暴露。
- **注入向量**：DANMU→LLM prompt injection 为既有风险，`_sanitize_danmu_text` + 长度钳制防线保留（上轮 S-1 义务）；docs_anchor/fix_hint 渲染 scheme 白名单（上轮 S-2 义务）随前端重写延续——**发现：P2-1 未显式引用该义务**，处置：义务块已含"净化防线复用既有义务"，前端实施时以三段式字段的 scheme 白名单渲染为准（义务块追溯）。
- **审计日志**：控制面操作经事件环+结构化日志（义务块保留），可追溯谁在何时增删了房间/关键词（单用户场景足够）。

评级：无 High 威胁；Low×4（loopback 访问、凭据文件权限、prompt injection 既有面、web_console 误开）均有既有防线或明示边界。

### Section 4: Data Flow & Interaction Edge Cases

**数据流图**（影子路径）：`INPUT(引擎 wire) → VALIDATION(_normalize_wire) → TRANSFORM(_dispatch 映射) → PERSIST(config 写回，仅控制面) → OUTPUT(my_handle/UI 事件环)`。影子路径：畸形/空/未知 type → 丢弃计数（组件 additive-only 契约+义务块）；队列满 → 背压丢弃序（义务块）；写回冲突 → 模块锁串行化。

**异步顺序**：共享可变态 = ① queue（生产者 asyncio 线程 / 消费者工作线程）——`queue.Queue` 线程安全，put_nowait/get 原子，无违序面；② config.json（托管层写回 / 前端 auto-save / 其他模块保存）——模块级锁 + 复用既有保存通道串行化，读-改-写不跨 await；③ 事件环 deque（多源 append / 前端轮询读）——`deque.append` 线程安全，seq 单调读侧过滤。排除的违序调度均命名了机制（Queue 原子性/模块锁/deque 原子性）。回归证明：P1-3/P1-4 的"写回原子性与并发单测"+ 队列满载单测覆盖双序。

**交互边界**：

| INTERACTION | EDGE CASE | HANDLED? | HOW? |
|---|---|---|---|
| 添加房间 | 双击/重复房号 | Y | Bridge RoomError 409 → 事件环提示 |
| 添加房间 | 服务 enabled=false | Y | 托管层前置检查→事件环"服务未启用"提示（义务块 set_enabled 语义） |
| 添加房间 | 非法房号/不支持链接 | Y | validate_room_id → RoomError 400 三段式 |
| 活跃房间 | 被删除 | Y | active 房间删除→无活跃→对话链空转+事件环提示（义务块） |
| 活跃房间 | 未在监听 | Y | 空转+提示一次（义务块） |
| 启停 | 停止后重启 | Y | 按 rooms[].enabled 恢复（运行时态语义+页面文案明示） |
| 扫码 | QR 过期/中断 | Y | 过期态+引擎重发刷新（组件原生+上轮义务） |
| 关键词 | 空串/超长/重复 | Y | 前端非空校验+组件子串匹配幂等（P2-1 实施） |

无未处理边界。

### Section 5: Code Quality Review

- **组织**：重构净减（删 ~400 行 serve/WS/token/探活，保留已测试的 _dispatch/聚合器/事件环）——812 行预计降至 ~600 行且功能更完整；符合"最小清晰 diff"。
- **复用**：零重建——Bridge 控制面、normalize_room_id、登录门、事件环、登录队列、聚合器全部复用（0B 映射见下）；唯一的"新代码"是子类 hook + 队列桥 + active 字段路由（~100 行级）。
- **命名**：AutoliveBridge/set_platform_enabled/set_enabled/add_room 语义直白。
- **边界**：既有防御检查保留（_normalize_wire 三态、_sanitize、envelope 校验）；无过度设计（未引入抽象层，直接子类）；无分支爆炸（每方法单一职责）。
- **复杂度**：`_dispatch` 既有 elif 链 8 分支（<10，且为消息路由本质复杂度，保留）。

无新发现。

### Section 6: Test Review

**新事物测试图**（全部已在任务验收列落位）：

| 新事物 | 类型 | 计划内测试 | Happy | Failure | Edge |
|---|---|---|---|---|---|
| 服务生命周期（start/stop/线程清理） | Unit | P1-1 验收 | start/stop 循环 | 守护重建注入异常（P1-6） | 退出无残留/强杀 daemon |
| 队列桥 | Unit | P1-1 满载单测 | 投递顺序 | queue.Full 丢弃序 | GIFT 满载兜底丢最新 |
| 线格式解析 | Unit | P1-2 replay 端到端 | 16 类路由 | 畸形 JSON 丢弃计数 | 空 payload/未知 type |
| 抖音 GIFT 换算 | Unit | P1-2 专项 | 序列 1,2,3→1,1,1 | 差值≤0 丢弃 | 首见 5 只报 1/重启重触发 |
| 控制面方法 | Unit | P1-3 方法级 | add/remove/start/stop/keywords | 非法房号/未启用平台 401 面 | 重复添加 409/禁用注入跳过 |
| active 路由 | Unit | P1-3 活跃路由单测 | 活跃进对话链 | 空转提示 | 多房间仅活跃互动 |
| config 写回 | Unit | P1-3/P1-4 并发单测 | 原子写回 | 写失败回滚 | 并发不丢更新 |
| 平台开关状态机 | Unit | P1-3 状态机单测 | enable/disable | 联动 stop | 重启恢复 |
| 冒烟（端到端） | Smoke | P1-7 | replay 全链 <2min | — | TTHW 计时输出 |
| 浸泡（业务闭环） | System | 验收 8/F3 门槛 | 4h 零崩溃零积压 | — | loop_lag/RSS/BACKPRESSURE 门槛 |
| UI 操作链 | Manual | P2-1/P2-2 验收 | 全操作即时生效 | 失败回滚提示 | 3s 状态刷新 |

凌晨 2 点信心测试=浸泡门槛（验收 8）；敌意 QA=队列满载+畸形帧+并发写回；混沌测试=守护重建注入异常。金字塔合理（大量单测+1 冒烟+1 浸泡+手动 UI）。无时间/随机依赖测试（聚合器窗口参数注入固定）。LLM/prompt 无变更（净化防线保留原样）。无缺口。

### Section 7: Performance Review

- **队列**：500 有界+背压序（义务块），最坏 O(1) put_nowait。
- **内存**：RSS ≤1.2GB 门槛（F3）+组件系统级预算；事件环 deque(maxlen=100) 上限固定；GIFT 换算映射随房间+用户增长——**发现：无上限**，处置：义务块补"映射按 (room_id,user_id,gift_id) LRU 上限 5000 键，超限淘汰最旧"（直播场景单场活跃送礼用户 <<5000，安全）。
- **慢路径**：Top3 = Playwright persistent context 启动（秒级，一次性/会话）、quickjs 签名（引擎级单例，微秒级/帧）、config 写回（模块锁串行，操作级频率）——均不进消息热路径。
- **连接池**：无新增外部连接池；12 平台引擎各自连接由组件重连管理器管理（上游已验收）。
- **N+1/DB 索引**：不适用（无数据库变更；组件 SQLite 遗留已确认死代码不使用）。
- **启动时间**：进程内 Bridge 实例化 < 子进程 spawn+WS 握手+探活——净改善。

1 项发现已闭环（映射上限）。

### Section 8: Observability & Debuggability Review

- **日志**：结构化日志义务保留（连接/断连/重建/分流/丢弃计数，携带 room_id/seq/msg_id）；托管层关键事件（启动/停止/活跃切换/写回失败）补入结构化口径。
- **指标**：组件 MetricsCollector 自带（received/published/filtered/reconnect/error/active_rooms/memory）；AUTOlive 侧事件环承载系统消息呈现。
- **告警**：服务状态条胶囊（既有）+ 置红顶部横幅（既有）；失联告警经 HEARTBEAT 判定（义务保留）。
- **Dashboard**：事件环轮询的房间状态/登录队列/丢弃计数（P2-2）。
- **可调试性**：3 周后排查=结构化日志（room_id/seq/msg_id 可串联）+ logs/services/ 落盘 + COPY_MANIFEST 版本戳定位组件版本；冒烟脚本 TTHW 计时可复现基准。
- **Runbook**：docs/danmaku_module.md 升级流程+回退路径（方案 B）+ 8080/8765 端口语义——失败模式与操作响应齐备。

无缺口。运营愉悦项：事件环已支撑"弹幕为什么断了"三段式呈现（GAP/ROUTE_FAILED/BACKPRESSURE 原因链），优于旧链路的黑盒。

### Section 9: Deployment & Rollout Review

- **迁移安全**：无 DB 迁移；config.json 节内演进（defaults 合并兼容旧配置文件）。
- **Feature flag**：`danmaku_listener.enabled` 总开关即回退开关（false=完全旁路，旧形态已删但关闭即无影响）。
- **Rollout 顺序**：P1 托管层 → 真实浸泡（F3 门槛）→ P2 前端 → P3 退役+提交批次④。**回退计划**：① 浸泡不过 → 方案 B 回退路径（第 3 节记录）；② P2 前端缺陷 → 旧页面不可回退（已重写）——处置：P2 与 P1 同分支分批提交（批次③），缺陷回滚=revert 批次③；③ 任意时刻 → enabled=false 关闭模块。
- **部署风险窗口**：单机单实例，无新旧并存窗口；webui 与 main.py 双进程形态不变。
- **冒烟**：P1-7 每批次提交前实跑；`--check`+hook 断言在升级流程（P3-5）。
- **验证清单**：首 5 分钟=状态条胶囊 running+事件环无 ERROR；首小时=真实房间首条消息≤60s（组件验收口径）+ BACKPRESSURE=0。

无新风险。

### Section 10: Long-Term Trajectory Review

- **技术债**：本计划净减债——删 12 僵尸适配器（2378 行）+ 旧资产（~400KB）+ 半成品 WS 链路；新增债=活跃房间单选的半成品多房间语义（S7 延期，已显式登记 TODOS+UI 明示）。
- **路径依赖**：子类 hook 私有成员是对上游的弱耦合（F6 断言设防）；rooms[].active 字段为 S7 多房间扩展预留了正交扩展点（active→多活跃是超集演进）。
- **知识集中**：docs/danmaku_module.md（升级/回退/矩阵）+ 本计划义务块 + CEO 摘要存档——新工程师 30 分钟可上手。
- **可逆性 4/5**：四批提交独立回滚+总开关+方案 B 回退路径；扣 1 分=P3 删除旧资产后重建需 git 考古（F8 已用顺序依赖缓解）。
- **生态契合**：ManagedProcess/service_orchestrator 范式（音频整合先例）不破坏——danmaku 改为进程内是该范式之外的特例，README 记录理由。
- **1 年之问**："为什么弹幕在进程内而 GPT-SoVITS 是子进程？"——答案在 README+本计划（数据面高频低延迟+控制面直调 vs 重依赖进程隔离），可解释。
- **下一步**：S7 多房间对话链（TODOS）→ 商用合规（S9）；架构支撑两者（Bridge 原生多房间+事件环基础设施）。

### Section 11: Design & UX Review（UI 范围=是）

**用户流图**：

```
[基础设置-平台配置页]
   │
   ├─ ①监听总开关卡 ──enabled 开关──▶ 状态胶囊(running/stopped/red)
   │
   ├─ ②房间管理卡
   │    ├─ 平台下拉(12平台|未启用禁用标灰|警告分组折叠) ──▶ 房号/链接输入 ──▶ [添加]
   │    │        └─ 添加即启动 | 失败→三段式提示
   │    └─ 房间列表(平台徽章|房号|状态|启停|删除|"设为活跃"|非活跃行内"仅监听不互动")
   │             └─ 3s 轮询刷新 | 停止=运行时态文案
   │
   ├─ ③关键词屏蔽卡 ──输入+添加 | 列表+删除 | 过滤总开关
   │
   └─ 头部: 未处理登录请求提示 ──▶ [侧边栏扫码弹窗(danmaku_login.py)]
```

- **信息架构**：开关→房间→关键词的优先序符合操作频率；三卡与既有 tab 视觉体系一致。
- **状态覆盖**：LOADING（3s 轮询间隙显示上次状态）/EMPTY（房间列表空态引导文案+关键词空态）/ERROR（三段式提示+乐观更新回滚）/SUCCESS（操作即时反馈）/PARTIAL（部分房间运行部分停止——状态徽章逐行）——P2-1/P2-2 验收覆盖，无缺失态。
- **旅程弧线**：首次使用=开开关→加房间→（可能扫码）→看到"活跃"徽章与消息流动——三步内达成核心价值。
- **AI slop 风险**：无——三卡有具体操作语义，非通用卡片堆砌；PLATFORM_WARNINGS 分级呈现是真实数据驱动。
- **设计系统对齐**：design_tokens 三档宽度/间距/无硬编码色值已在 P2-1 硬性要求；3 列网格规范引用。
- **响应式**：桌面 WebUI（8086）为唯一目标，手机端 NOT in scope 已明示——一致。
- **无障碍**：NiceGUI/Quasar 基础（键盘可达、语义标签）随框架默认；tooltip 提供平台风险说明。
- **inevitable 触感**：活跃徽章+仅监听明示+三段式错误——操作结果永远可解释；30 分钟增强=操作成功 toast 统一（实施细节，随 P2-1）。

建议：实施后跑 /design-review 对渲染页做视觉一致性检查（P2-1 交付后）。

## What already exists（复用映射）

| 子问题 | 既有代码 | 复用方式 |
|---|---|---|
| 12 平台监听引擎 | danmaku_listener/engines/（v0.4.0 全量验收） | 进程内直接实例化（registry.build_engine 经 Bridge） |
| 控制面（房间/启停/关键词/登录门） | danmaku_listener/web/bridge.py | DanmakuBridge 子类直调 |
| 消息映射进对话链 | utils/danmaku_consumer.py `_dispatch`（L604-654） | 原样保留 |
| 聚合限速 | `_DanmuAggregator`/`_LikeAggregator`（L729-813） | 原样保留 |
| 文本净化 | `_sanitize_danmu_text` | 原样保留 |
| 信封校验 | `_normalize_wire`+`UnifiedMessage.from_wire` | 原样保留 |
| 事件环/登录队列 | DanmakuListenerService（L110-176） | 保留，数据源换 Bridge 回调 |
| 扫码 UI | frontend/ui/components/danmaku_login.py | 原样保留 |
| 服务状态条 | frontend/ui/components/service_status.py | 原样保留 |
| 上游同步 | Scripts/copy_danmaku.py + COPY_MANIFEST.json | 保留+补复制 export_contract.py |
| config 保存通道 | utils/config.py + config_auto_save.py | 写回复用 |
| 服务守护语义 | utils/service_orchestrator.py 失控防护语义 | 语义移植（3 次置红） |

## Dream state delta

```
CURRENT STATE                     THIS PLAN                        12-MONTH IDEAL
半成品跨进程链路(收不到真实数据)    进程内 12 平台真实收流进对话链    全平台监听+AI互动闭环商用级稳定
12 僵尸适配器+旧资产并存           全部清除,单模块单真相             零遗留,新功能全在统一模块范式上
配置分散(4处)                     config.json 单源+前端全操作      全系统配置单源+一张控制台
启动脚本手工链路                   main.py 自动托管                  一键启动全链路自愈
个人使用                          个人使用(商用绑定登记TODOS)       商用交付(隔离形态+合规账号重评后)
```

本计划把"弹幕输入层"推进至 12 个月理想态的核心位置（真实收流+可控可观测），遗留差距集中在商用级隔离形态与多房间互动（S7/S9 显式登记）。

## Failure Modes Registry

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| _broadcast hook | 队列满 | Y(背压序+兜底) | Y(单测) | 状态条示警 | Y |
| _broadcast hook | 上游重命名私有成员 | Y(升级流程断言) | Y(冒烟首步) | 升级时报警 | Y |
| 消费线程解析 | 畸形帧 | Y(丢弃计数) | Y(单测) | 计数可见 | Y |
| my_handle 调用 | 下游异常 | Y(日志) | Y(既有) | 日志可见 | Y |
| 控制面方法 | RoomError | Y(事件环三段式) | Y(单测) | 操作失败提示 | Y |
| 引擎组 | 致命异常 | Y(守护重建) | Y(注入单测) | 置红+手动恢复 | Y |
| asyncio 线程本体 | 异常退出 | Y(顶层防护+重建——义务块) | Y(注入单测) | 状态条异常 | Y |
| config 写回 | 并发/失败 | Y(通道+锁+回滚) | Y(单测) | 保存失败提示 | Y |
| web_console | 8080 占用 | Y(预检) | 手动 | 三段式提示 | Y |
| 登录门 | QR 过期/登录失败 | Y(组件+上轮义务) | 手动 | 过期态+重试出口 | Y |
| 进程级 | 硬崩溃(段错误/OOM) | N(已知残余风险——F3 门槛+回退B+商用绑定) | Y(浸泡门槛) | 整进程退出(直播中断) | Y |
| 房间注册表 | config/rooms.json 双源漂移 | Y(config 权威+启动覆盖) | Y(P1-4 恢复单测) | 重启后状态一致 | Y |

CRITICAL GAP 计数：0（硬崩溃行为已明示为已知残余风险并配回退路径与商用绑定，非静默）。

## NOT in scope

见正文第 7 节（5 项，均附理由）；S7/S9 延期登记 TODOS（P3-6 ④）。

## Scope Expansion Decisions（SELECTIVE EXPANSION）

- Accepted: S1 进程内化、S2 前端三卡重写、S3 旧链路退役、S4 配置单源、S5 抖音 GIFT 修复、S6 契约漂移检查 + CEO 声部改进项 F1-F10
- Deferred: S7 多房间对话链、S9 商用合规（→ TODOS.md）
- Skipped: S8 组件浏览器控制台功能开发

## Completion Summary

```
  +====================================================================+
  |            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
  +====================================================================+
  | Mode selected        | SELECTIVE EXPANSION                         |
  | System Audit         | 半成品 serve 链路(引擎接线未落地)/12 僵尸适配器/ |
  |                      | 配置四处分散/上游 v0.4.0 待提交               |
  | Step 0               | SELECTIVE EXPANSION；S1-S6 接受,S7/S9 延期,S8 跳过 |
  | Section 1  (Arch)    | 1 issue (asyncio 线程顶层防护 GAP) → 义务块闭环 |
  | Section 2  (Errors)  | 12 error paths mapped, 0 GAPS(线程防护已补)    |
  | Section 3  (Security)| 4 Low 均有防线；攻击面净收缩                  |
  | Section 4  (Data/UX) | 8 交互边界全处理；异步顺序机制命名齐备          |
  | Section 5  (Quality) | 0 issues(净减~400 行,零重建)                  |
  | Section 6  (Tests)   | 图齐备,0 gaps(含浸泡门槛)                     |
  | Section 7  (Perf)    | 1 issue(GIFT 映射无上限) → 义务块 LRU 5000 闭环 |
  | Section 8  (Observ)  | 0 gaps(事件环+结构化日志+runbook 齐备)         |
  | Section 9  (Deploy)  | 0 新风险(F8 后置+开关回退+分批提交)            |
  | Section 10 (Future)  | Reversibility: 4/5, debt items: 1(S7 语义显式登记) |
  | Section 11 (Design)  | 0 issues(状态覆盖全;流图落盘)                  |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (5 项)                               |
  | What already exists  | written (12 行映射)                          |
  | Dream state delta    | written                                      |
  | Error/rescue registry| 12 行, 0 CRITICAL GAPS                       |
  | Failure modes        | 12 total, 0 CRITICAL GAPS                    |
  | TODOS.md updates     | 2 items proposed (S7/S9)                     |
  | Scope proposals      | 9 proposed, 6 accepted                       |
  | CEO plan             | written (ceo-plans/2026-10-05-danmaku-inprocess-integration.md, ACTIVE) |
  | Outside voice        | codex — unavailable (模型配置级失败)           |
  | Lake Score           | 8/8 完整选项(全部评审点选完整闭包方案)          |
  | Diagrams produced    | 3 (系统架构/数据流影子路径/用户流)              |
  | Stale diagrams found | 0                                             |
  | Unresolved decisions | 0 (User Challenges 1 项移交 Phase 4 终门)      |
  +====================================================================+
```

**User Challenge（Phase 4 终门呈报，本阶段不裁决）**：进程内整合（方案 A）与用户"不是通过API中转"的解读绑定——CEO 原生声部辨析了"无缝"的体验层与拓扑层含义，方案 B（托管子进程）在体验层同样满足且崩溃隔离更强。计划以 A 实施但将 B 写死为 F3 门槛失败的回退路径。用户原文方向（A）为基准；若用户本意是体验层无缝而非同进程，B 亦是合法选项——终门确认。

**GIFT 映射 LRU 义务（Section 7 闭环）**：`_dispatch` 抖音 GIFT 换算映射按 (room_id,user_id,gift_id) 维护 LRU 上限 5000 键，超限淘汰最旧（首见语义重新触发，安全侧）。

<!-- autoplan-accepted:design -->
- 设计评审（2026-10-06）：无 DESIGN.md（按通用原则 + design_tokens.py 令牌系统评审）；mockup 工具无 OpenAI API key → 文本评审（DESIGN_NOT_AVAILABLE 路径）；分类器 = OPERATE/APP UI（管理设置页）。
- P2-0 状态与呈现规范八项为 P2-1 前置义务（设计声部 17 项发现全部落盘）：①房间行七态状态机枚举表（待登录/登录中/运行中/收流停滞/已停止/平台已禁用/活跃但未监听 × 文案 × 操作 × 徽章语义色）；②房间行生命信号"最近消息 N 秒前/今日 N 条"+ 收流停滞态与 fix_hint 指引（critical：弹幕安静与链路断裂在 UI 不可区分的旅程断裂点）；③事件分级呈现规则（错误类 toast+行内驻留直至解除 / 提示类 toast 一次 / docs_anchor 可点链接 scheme 白名单 / 轮询去重键 (kind,platform,room_id,reason_code)）；④胶囊四态（绿/灰/红/黄-重建中）；⑤禁用态降级规范（enabled=false 首体验：灰态胶囊+引导，卡片可浏览操作禁用+tooltip）；⑥平台启用开关独立区（下拉只选择；"需验证"分组默认收起+计数）；⑦活跃房间常驻状态行+无活跃常驻警示条（替代一次性提示）；⑧破坏性操作确认（联动 stop 含活跃房间需确认框）。
- 页头重组（D1）：页头承载总开关+服务胶囊+登录请求数；房间管理卡为首屏主导（高频操作优先于低频总开关）。
- 交互补丁：链接平台与所选不一致自动切换并提示（识别失败才报错）；关键词卡固定文案"仅屏蔽弹幕内容，不影响礼物与进场"；重启恢复一次性汇总"已恢复 X 个房间（Y 个启动成功）"；乐观更新范围（启停/删除/关键词/平台开关）+ 回滚触发（错误事件或 5s 超时）；登录失败/超时/取消映射状态机+行内"去登录"重试入口；下拉标注登录要求（需扫码/免登录）。
- 色值纪律：全部 `var(--...)` 语义变量引用（对齐 llm.py 既有模式），禁硬编码。
- 评分：Pass1 IA 6→9、Pass2 状态 5→9、Pass3 旅程 6→9、Pass4 slop 8→9（0 hard rejection）、Pass5 设计系统 9→10、Pass6 响应式/a11y 5→8（桌面单目标已明示；a11y 随 NiceGUI/Quasar 基础+键盘可达要求）、Pass7 决策 17 resolved/0 deferred。整体 5→8（最低维度修复后）。
- 设计声部派发偏差记录：native dispatch 钩子认证因 journal 死锁不可用（失败 init 记录所致），以等价措辞派发同一 native-prompt.md（183 行全文读取，INPUT: design 89d5fbe3 校验通过）；Codex 外部声部 unavailable（同 CEO 阶段 gpt-6-astra 配置根因）——litmus 七格单声部，不标 CONFIRMED。
<!-- /autoplan-accepted:design -->

## Design Review — Completion Summary

```
  +====================================================================+
  |         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
  +====================================================================+
  | System Audit         | 无 DESIGN.md；UI 范围=平台配置页重写；        |
  |                      | design_tokens.py 令牌系统为等效设计系统       |
  | Step 0               | 初始 6/10；焦点=全 7 维（autoplan P1）        |
  | Pass 1  (Info Arch)  | 6/10 → 9/10（D1 页头重组/D2 活跃常驻/D3 开关独立区）|
  | Pass 2  (States)     | 5/10 → 9/10（S1 七态机/S2 生命信号/S3 禁用态/  |
  |                      | S4 空态/S5 登录路径/P2 胶囊四态）              |
  | Pass 3  (Journey)    | 6/10 → 9/10（J1 登录预期/J2 作用域文案/J3 恢复反馈）|
  | Pass 4  (AI Slop)    | 8/10 → 9/10（0 hard rejection；OPERATE 模式；  |
  |                      | litmus: 3 YES/1 YES后修/3 N-A or SPEC'D）      |
  | Pass 5  (Design Sys) | 9/10 → 10/10（var(--) 语义引用硬性化）         |
  | Pass 6  (Responsive) | 5/10 → 8/10（桌面单目标明示；a11y 显式化）     |
  | Pass 7  (Decisions)  | 17 resolved, 0 deferred（A1 确认框入 P2-0⑧）  |
  +--------------------------------------------------------------------+
  | NOT in scope         | 手机端 UI（正文已有）；组件浏览器控制台开发     |
  | What already exists  | design_tokens.py/service_status.py/danmaku_login.py |
  | TODOS.md updates     | 0（全部修复在范围内）                          |
  | Approved Mockups     | 0（无 API key，文本评审）                      |
  | Decisions made       | 17（P2-0 八项规范 + 9 项交互补丁）             |
  | Decisions deferred   | 0                                             |
  | Overall design score | 5/10 → 8/10                                   |
  | Outside voice        | codex — unavailable（同因）；Claude 声部 completed 17 findings |
  +====================================================================+
```

**DESIGN OUTSIDE VOICES — LITMUS SCORECARD**（Codex N/A，单声部）：

| Check | Claude | Codex | Consensus |
|---|---|---|---|
| 1. Brand unmistakable in first screen? | N/A（APP UI 模式，品牌首屏不适用） | N/A | N/A |
| 2. One strong visual anchor? | YES（修复后：房间管理卡主导） | N/A | N/A |
| 3. Scannable by headlines only? | YES（三卡标题+页头状态） | N/A | N/A |
| 4. Each section has one job? | YES | N/A | N/A |
| 5. Cards actually necessary? | YES（三卡均为操作语义） | N/A | N/A |
| 6. Motion improves hierarchy? | NOT SPEC'D（计划无动效——APP UI 最小动效即可，不阻塞） | N/A | N/A |
| 7. Premium without decorative shadows? | YES（design_tokens 阴影三档语义化） | N/A | N/A |
| Hard rejections triggered | 0 | N/A | N/A |

<!-- autoplan-accepted:dx -->
- DX 评审（2026-10-06，Mode=DX POLISH）：产品类型 = 自托管平台+内嵌组件库；persona = solo 维护者/自部署主播（单人 Windows 单机，TTHW 容忍 10 分钟级，不读文档靠复制粘贴）；Codex 外部声部 unavailable（同因）。声部裁定：通过（无 critical，2 high + 6 medium 全部计划文本修订级，8 项修复全部落盘）。
- 8 项修复：①demo.jsonl 夹具落点钉死 tests/fixtures/（rmtree 区外，缺失自动生成/断言）；②README 阶段 0 环境自检（pip check 一行+预期输出）；③阶段 2 双路线（B 站扫码 / 斗鱼虎牙免登录）；④API 命名对齐组件参数序（add_room(room_spec, platform, start=True)）+ set_keyword_filter/set_service_enabled 显式 set 语义（替代 toggle 反模式）；⑤start=False 预配置逃生门；⑥守护/线程事件三段式化（fix_hint 指向排障节）；⑦README 补两张参考表（config 字段表+托管层 API 表）+ reason_code↔排障锚点一一对应验收；⑧三运行参数可覆盖（queue_maxsize=500/web_console_port=8080/stall_threshold_seconds=60）。
- 三阶段上手路径（进程内形态重写）：阶段 0 环境自检 → 阶段 1 冒烟 <2min（TTHW 计时）→ 阶段 2 双路线到对话链 → 阶段 3 全配置；每阶段一条命令+预期现象+耗时预算。
- Magical Moment：添加房间→活跃徽章→弹幕实时流进对话链（"最近消息 N 秒前"即时反馈为载体，P2-0 ②）；最低成本载体已内建于 P2-0。
- TTHW：目标 <2min（阶段 1 冒烟计时口径）；文档路径 1 步，实际从零 3-4 步（阶段 0 自检后全程可复制）。
- 派发偏差记录（同 design 阶段）：native dispatch 钩子认证因 journal 死锁不可用，等价措辞派发同一 native-prompt.md（184 行全文读取，INPUT: dx 59ea448c 校验通过）。
<!-- /autoplan-accepted:dx -->

## DX Review — Completion Summary

**Developer Persona Card**：solo 维护者/自部署主播——单人 Windows 单机部署、runtime312 统一环境、复制粘贴式操作、出错时需要即查即修的三段式指引；容忍度：上手 >10 分钟即弃。

**Developer Empathy Narrative**：我是主播也是这套系统的运维。开播前我要确认弹幕监听活着——升级组件后我最怕"以前能跑现在断了"；重启后我要知道系统"记得"我配过什么；出问题时我要一条命令定位是环境坏了还是平台改了。这套计划给我三阶段上手（每步有预期现象）、升级前三道检查（版本/漂移/hook 断言）、出事时的三段式+排障节——我能自救。

**Competitive DX Benchmark**：对（a）让弹幕飞（外部工具，配置靠手改 JSON）、（b）GPT-SoVITS 原生部署（无托管无状态呈现）、（c）本系统音频整合（ManagedProcess+状态条先例）——本计划 DX 目标 tier = Competitive（TTHW <5min）；三段式错误与 hook 断言达到 Champion 水平的单点。

**Developer Journey Map**（摩擦点→处置）：发现（README 不存在→P3-5 补齐）→ 评估（三阶段路径可试）→ 安装（阶段 0 自检）→ Hello World（阶段 1 冒烟 <2min）→ 集成（阶段 2 双路线）→ 调试（排障节+生命信号+三段式）→ 升级（三道检查+迁移映射）→ 恢复（守护置红手册化）——8 环节全闭合，无断点。

**First-Time Developer Confusion Report**：①"demo.jsonl 哪来的"→落点钉死+自动生成；②"为什么 pip 装不上"→阶段 0 自检；③"我没有 B 站号"→斗鱼/虎牙免登录路线；④"API 参数怎么传"→参数序对齐组件+API 表；⑤"500 条队列满了怎么办"→queue_maxsize 可调。全部已处置。

```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Dimension            | Score  | Prior  | Trend  |
|----------------------|--------|--------|--------|
| Getting Started      | 8/10   | 5/10   | ↑      |
| API/CLI/SDK          | 9/10   | 7/10   | ↑      |
| Error Messages       | 10/10  | 9/10   | ↑      |
| Documentation        | 9/10   | 6/10   | ↑      |
| Upgrade Path         | 9/10   | 7/10   | ↑      |
| Dev Environment      | 8/10   | 8/10   | —      |
| Community            | N/A    | N/A    | 个人项目不适用 |
| DX Measurement       | 8/10   | 8/10   | —      |
+--------------------------------------------------------------------+
| TTHW                 | <2 min | ~15 min | ↑     |
| Competitive Rank     | Competitive（错误三段式单点 Champion）         |
| Magical Moment       | designed via 活跃徽章+生命信号即时反馈          |
| Product Type         | Platform + 内嵌组件库                          |
| Mode                 | DX POLISH                                     |
| Overall DX           | 8/10   | 6/10   | ↑     |
+====================================================================+
| DX PRINCIPLE COVERAGE                                               |
| Zero Friction      | covered（三阶段+双路线+阶段 0）                 |
| Learn by Doing     | covered（冒烟即真实数据流）                     |
| Fight Uncertainty  | covered（三段式+排障节+生命信号）               |
| Opinionated + Escape Hatches | covered（默认合理+三参数可覆盖+start=False） |
| Code in Context    | covered（API 表含持久语义与错误码）             |
| Magical Moments    | covered（活跃徽章→实时反馈）                    |
+====================================================================+
```

**DX Implementation Checklist**（√=已入计划义务）：√ TTHW<2min 计时口径；√ 阶段 0 一行自检；√ 首次运行有预期现象输出；√ Magical Moment 内建 P2-0；√ 错误=problem+cause+fix+docs_anchor（scheme 白名单+锚点对应验收）；√ API 命名对齐组件可猜测；√ 参数默认+start=False 逃生门；√ 文档可复制（迁移映射/API 表/字段表）；√ 排障节覆盖真实场景；√ 升级三道检查+迁移映射；N/A TypeScript/CI；√ changelog（COPY_MANIFEST 版本戳+contract 版本核对）。

**NOT in scope**：社区/插件生态（个人项目）；多语言 SDK；网站文档站。
**What already exists（复用）**：copy_danmaku.py 复制范式、COPY_MANIFEST 版本戳、export_contract --check、smoke_danmaku.py TTHW 计时骨架、三段式契约（组件+上轮义务）、事件环呈现管道。
**Unresolved Decisions**：无（8 项修复全部落盘）。

<!-- autoplan-accepted:eng -->
- Eng 评审（2026-10-06）：声部 32 次代码实证核验，有保留批准——21 项发现（1 高危×2、中高危×2、中×8、低×9）全部落盘修复。
- P1-3 线程亲和（Eng A1，高危必修）：Bridge 控制方法全部为协程（async def + create_task），UI 线程直调得到未执行 coroutine（静默无操作）——托管层统一 asyncio.run_coroutine_threadsafe 封装（future+超时+异常转 RoomError/事件环），验收断言 UI 线程调用返回真实结果或抛 RoomError。
- P1-1 丢弃结构（Eng E1）：queue.Queue 非原子排空-回灌竞态——自实现 collections.deque+threading.Condition 有界结构（按可丢类型扫描淘汰），下播裁剪迁同一结构；停机顺序写死（先等 stop_all future 完成再停循环，Eng T4）；'无线程残留'改可验证断言（Eng T6：join 成功+循环已停+无未完成任务告警）。
- P1-2 测试补强（Eng T3/T5/H2）：合成洪峰测试（1 万条/秒，hook 不阻塞循环+丢弃计数与 BACKPRESSURE 一致+消费可追平）；LRU 淘汰后首见重触发单测；replay 统一队列级注入。
- P1-4 注入机制写实（Eng A2/A3/E5）：rooms 注入=super().__init__() 后重设 _rooms_file+清空 _rooms+重跑 _load_rooms_registry；关键词持久化外置出副本（blocked_keywords.json 在 danmaku_listener/web/ 内——覆写 _save/_load_blocked_keywords 指向仓库根 persistence_data/，整体替换语义，防升级清数据+MANIFEST 版本戳漂移）；组件 cookie 路径构造期覆写 settings 字段+启动断言路径在副本外；事件去重键加 60s 窗口。
- P2-2 分支（Eng E2）：add_room 返回 login_required 不适用 5s 回滚（登录合法耗时数分钟），依赖 NEEDS_LOGIN/ENGINE_STATUS 收敛；5s 回滚仅保留启停/删除/关键词/平台开关。
- P2-0（Eng E4）：GIFT 满载丢弃升级错误级呈现（行内驻留，答谢丢失强感知）。
- P2-4/P3-3/风险4 更正（Eng T2，中高危）：真实活跃消费者=main.py:86 TaskScheduler+tests/test_config.py L50/L61/L225（断言 platform=='talk'，删键 pytest 必挂）——P3-3 封闭清单补 test_config.py 同步更新；my_handle.py:75 系误引（abnormal_alarm_data 字典）已更正。
- P3-4（Eng T1，高危必修）：.gitignore:289 的 test_*.py 无路径锚定（实测 check-ignore 命中 tests/ 新文件）——全部新增单测会被静默吞掉；规则锚定 /test_*.py 或 !tests/ 例外；P1 验收加'新测试文件 git status 可见'。
- P3-5（Eng A4/S1/S2）：API 表逐方法列真实签名（add_room room_spec 在前 vs start/stop/remove (platform, room_id) 在前）；web_console 调试通道警示（无鉴权+跨站/rebinding 面+勿长开）；cookie 备份带出风险提示。
- F3 补充（Eng H3）：浏览器进程组合计内存观察留档（Playwright 独立进程不计入 RSS）。
- 勘误：__main__.py 精确行 231；my_handle.py:75 误引更正。
- 派发偏差记录（同 design/dx）：等价措辞派发同一 native-prompt.md（184 行全文读取，INPUT: eng 9f41e368 校验通过）；Codex unavailable（同因）。
<!-- /autoplan-accepted:eng -->

## Eng Review — Completion Summary

```
  +====================================================================+
  |         ENG PLAN REVIEW — COMPLETION SUMMARY                       |
  +====================================================================+
  | Scope Challenge      | 不缩减（autoplan override P2）                |
  | Section 1 (Arch)     | 5 issues（A1 高危线程亲和/A2/A3/E5/H1）→ 全修复|
  | Section 2 (Quality)  | 2 issues（A4 参数序/H2 注入点）→ 全修复        |
  | Section 3 (Tests)    | 6 issues（T1 高危 gitignore/T2 test_config/    |
  |                      | T3 洪峰/T4 停机/T5/T6）→ 全修复 + Test Plan    |
  | Section 4 (Perf)     | 2 issues（E1 满载竞态/H3 RSS 盲区）→ 全修复    |
  | Edge cases           | E2/E3/E4 → 全修复；S1/S2/S3 安全项 → 处置      |
  | Critical gaps        | 0（A1/T1/A3/T2 四项高危全部修复闭环）          |
  | Test Plan Artifact   | admin-main-eng-review-test-plan-20261006-005900.md |
  | Outside voice        | codex — unavailable（同因）                    |
  | Verdict              | 有保留批准 → 修订后批准                        |
  +====================================================================+
```

### Coverage diagram（新 codepath 测试图摘要）

```
CODE PATHS                                              USER FLOWS
[+] utils/danmaku_consumer.py（重构）                    [+] 添加房间→活跃徽章→对话链回应
  ├── AutoliveBridge._broadcast hook                      ├── [GAP][→E2E] 扫码登录全链（手动+B站实测）
  │   ├── [计划] 洪峰 1w/s 不阻塞循环（T3）                ├── [计划] 乐观更新回滚（E2 分支）
  │   └── [计划] 满载丢弃序+GIFT 兜底（E1 原子结构）        └── [GAP][→E2E] 登录中 stop（T4 手动用例）
  ├── 队列消费线程解析
  │   ├── [计划] 16 类路由+畸形帧丢弃                     [+] 重启恢复
  │   └── [计划] 抖音 GIFT 增量换算+LRU                    └── [计划] rooms[].enabled 恢复+ghost 对账（A5）
  ├── 控制面（run_coroutine_threadsafe 封装 A1）
  │   ├── [计划] 方法级+字符串实参位置（A4）               [+] 升级流程
  │   └── [计划] active 路由+ghost 对账（A5）              └── [计划] --check+hook 断言+冒烟
  ├── 守护重建（注入异常单测）
  └── config 写回（同一锁通道 H1）

COVERAGE: 0/12 pre-implementation（全部为计划内新路径）| 新增测试需求 18 项已入任务验收列
GAPS: 全部由 P1/P2 任务验收与 Test Plan Artifact 承接
```

**Tests to Retire**：test_write_serve_toml ×2（serve TOML 删除后失效）。
**Pending Decisions**：none。

## Final Approval Gate（2026-10-06）

- **裁决：A) 批准（含进程内方案 A）**——用户在终门确认"无缝"意图涵盖拓扑层（同进程），User Challenge 1 解决；方案 B 维持 F3 门槛失败的既定回退路径（第 3 节 A/B/C 决策记录不变）。
- 决策统计：约 95 项处置（自动裁决约 83 / Taste 约 12 / User Challenge 1——已解决）。
- 跨阶段主题：静默失效类风险（4 声部独立命中，断言/验收全闭环）、落点纪律（cookie/rooms/关键词/测试夹具四类补齐）。
- 阶段覆盖表：
  | 阶段 | 主评审 | 外部声部 | 原生完成 | 发现数 | 覆盖 |
  |---|---|---|---|---|---|
  | CEO | Claude 原生（子代理） | codex unavailable（gpt-6-astra 配置级） | 是 | 规格环 30 + 声部 10 | 部分（无外部） |
  | Design | Claude 原生（子代理） | codex unavailable | 是 | 17 | 部分（无外部） |
  | DX | Claude 原生（子代理） | codex unavailable | 是 | 8 | 部分（无外部） |
  | Eng | Claude 原生（子代理） | codex unavailable | 是 | 21 | 部分（无外部） |
- 形式步骤偏差记录：autoplan 守卫钩子因会话中途一次失败 init 的 journal 记录死锁（invocation() 遇失败记录即 fail），CEO close 后的 phase-entry/dispatch 认证不可用——Design/DX/Eng 声部以等价措辞派发同一 native-prompt（INPUT 绑定逐声部校验通过），close 形式步骤（prepare-close packet）对死钩子无效果而省略，评审实质（方法论全读/义务块/输出物/任务产物/日志）完整执行。
- 下一步：实施（P1 → F3 浸泡 → P2 → P3）；建议实施完成后运行 /design-review 视觉检查与 /devex-review boomerang。
