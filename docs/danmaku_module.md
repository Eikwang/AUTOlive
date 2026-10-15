# DanmakuListener 弹幕监听模块

AUTOlive 内置的直播弹幕监听能力（12 平台，进程内运行）。组件副本位于 `danmaku_listener/`（上游 `D:\AI\DanmakuListener` 的复制范式，`COPY_MANIFEST.json` 记录版本戳）。

- 控制面：WebUI「基础设置-平台配置」页——添加/删除房间、启停监听、屏蔽关键词、活跃房间指定
- 数据面：弹幕/礼物/进场 → 对话链（my_handle），点赞/统计 → 计数路径
- 进程内运行：无 serve 子进程、无 8765 端口；引擎崩溃不拖垮主程序（per-engine 隔离 + 服务级守护）

## 三阶段上手（阶段 0 先行）

**阶段 0｜环境自检**（runtime312 环境）：

```bat
D:\AI\EDTalk\runtime312\python.exe -m pip check
```

预期输出 `No broken requirements found`。组件钉版依赖已实测就位：protobuf 7.35.1 / quickjs 1.19.4 / aiohttp 3.14.3 / websockets 16.1.1 / pydantic 2.13.4。

**阶段 1｜冒烟（目标 <2 分钟）**：

```bat
D:\AI\EDTalk\runtime312\python.exe -X utf8 tests\smoke_danmaku.py
```

预期 `[PASS] 进程内端到端冒烟通过（…TTHW=…s）`——进程内 Bridge → 队列桥 → 活跃路由 → stub 对话链全链打通。

**阶段 2｜单平台到对话链**（双路线）：
1. 启动系统（WebUI「运行系统」），打开「基础设置-平台配置」
2. 打开总开关「启用监听」
3. **路线 A（有 B 站账号）**：平台选「哔哩哔哩」→ 填房间号或直播间链接 → 添加 → 扫码弹窗 → 登录后自动开始监听
   **路线 B（无账号）**：平台选「斗鱼」或「虎牙」（免登录）→ 填房间号 → 添加 → 直接开始监听
4. 预期：房间行显示绿色「监听中」+ 生命信号（最近消息 N 秒前）；数字人开始回应弹幕

**阶段 3｜全配置**：多平台添加房间（未活跃房间仅监听不互动）→ 关键词屏蔽 → 点选「设为活跃」指定互动房间。

## 升级流程（上游组件更新）

1. **版本核对**（第 0 步）：`python -m danmaku_listener contract` —— 记录当前契约版本
2. **拷贝前核对**：`git status --porcelain danmaku_listener/ docs/danmaku_contract/` —— 确认除 `__pycache__` 等已知生成物外无未跟踪文件（rmtree 对未入库产物不可逆）
3. **重跑复制**：`python Scripts/copy_danmaku.py` —— 副本整体替换为上游最新（注意：`danmaku_listener/` 会被 rmtree，登录态/关键词/注册表在副本外不受影响）；异常回滚 `git checkout HEAD -- danmaku_listener/ docs/danmaku_contract/`
4. **本地补丁重放**（见下方台账）——干净覆盖会丢失下列本地改动
5. **契约漂移检查**：`python -m danmaku_listener contract`（版本核对）
6. **hook 存在性断言**：冒烟脚本首步断言 `DanmakuBridge._broadcast` 等被子类 hook 的成员签名仍在（私有方法不受上游 API 承诺保护，防同步后静默断流）
7. **冒烟**：`pytest tests/` 全量 + `tests/smoke_danmaku.py`

> 本次同步（2026-10-14）：上游 v0.8.0 + HEAD `d92edec`（含未发布虎牙未开播 GAP 修复）。上游正式发布 v0.8.1 时核对一次该修复是否存活。
> smoke_danmaku 覆盖边界：契约数据面冒烟——v0.8.0 新链路（快手 STEALTH_JS 伪装、重登编排生命周期）仅由上游测试与人工实测保障，无本地回归网。

### 本地补丁台账（2026-10-14 建立——每次升级后逐条核对重放，防漏放）

| 文件 | 改动 | 意图 | 重放验证 |
|---|---|---|---|
| `danmaku_listener/config/settings.py` | +`send_blocked_keywords: str = ""` 字段 | 发送屏蔽词独立词表（AUTOlive 8926b9fe，通用需求） | `pytest tests/test_send_guard_keywords.py` |
| `danmaku_listener/senders/wiring.py` | SendGuard 改用独立 `send_blocked_keywords`（不复用 `bridge._blocked_keywords`） | 同上——短词子串误拦根治 | 同上 |

> 台账首条即词表分离——上游 PR 提回收编后本表该项退役（TODOS.md 2026-10-14 项）。

## 手动重登（v0.8.0 会话刷新编排，2026-10-14）

- **一次登录双载体齐活**：房间行「登录」按钮触发重登编排——抖音单窗双喂（profile 登录→storage_state 域白名单导出喂监听 cookie）、虎牙 takeover 可见窗同步导出监听存档；其余平台单载体一次覆盖监听+发送（上游 12 平台会话拓扑审查实证：双载体仅抖音/虎牙）。**无需二次登录**；旧的「发送登录」入口已随此语义移除（D2a 终门裁定 2026-10-14）。
- **异步两段式**：`POST /danmaku/relogin` 同步段即时回执（relogin_id/rooms_stopped/carriers_cleared）→ 后台编排推进登录与恢复 → 前端轮询 `GET /danmaku/relogin/status?id=`。5 态状态机=1 进行态（recovering，前端每 3s 轮询）+4 终态（completed/login_required/failed/superseded，停轮询+对应文案）；轮询上限 5 分钟。
- **接口约束**：`/danmaku/relogin` 专用超时 75s（同步段最坏 supersede join 15s+发送会话 close 20s+清理 >35s，高于通用 30s）；TimeoutError 单列 `reason_code=control.timeout`（编排可能仍在进行，勿盲目重试）；status 端点读取经服务循环通道（REGISTRY 单事件循环访问约束——禁止线程池直调 deepcopy）；未知 id（记录被 _gc 淘汰：上限 10 条/3600s，或进程重启）=found=False 终止语义；读取异常=可重试语义（重试一次后仍失败前端继续轮询）。`get_relogin_status` 为消费侧认定的稳定接口——上游改动需同步核对。

## 排障（problem + cause + fix）

| 症状 | 原因 | 修复 |
|---|---|---|
| 房间「收流停滞」 | 直播未开播 / 平台风控 / 网络断 | 确认开播；查看事件环 GAP/ROUTE_FAILED 提示的 reason_code；重启该房间 |
| 房间「待登录」 | 无登录态或 cookie 过期（B站/快手/抖音） | 点行内「去登录」重新扫码；登录后自动恢复 |
| 守护置红（胶囊红） | 引擎组连续 3 次致命异常 | 服务状态条胶囊 → 重启（清零防护计数）；查 `logs/services/` |
| web_console 启动报 EADDRINUSE | 8080 被占用 | 释放端口或改 `danmaku_listener.web_console_port` |
| 控制面 503 | 服务未启用（enabled=false） | 平台配置页开启总开关，或 config.json `danmaku_listener.enabled=true` 后重启 |

事件环 reason_code 与本表锚点一一对应（冒烟校验）。

## config.json `danmaku_listener` 节字段表

| 字段 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `enabled` | bool | false | 总开关（持久；false 时托管不启动、前端操作禁用） |
| `component_root` | str | danmaku_listener | 组件副本目录 |
| `login_entry` | bool | true | 登录扫码弹窗常驻入口 |
| `web_console` | bool | false | 组件浏览器控制台（调试用，**无鉴权仅本机，勿长开**） |
| `web_console_port` | int | 8080 | 调试通道端口（高冲突端口，启动前占用检测） |
| `queue_maxsize` | int | 500 | 业务队列上限（满载按背压同序丢弃） |
| `stall_threshold_seconds` | int | 60 | 收流停滞判定阈值 |
| `cookie_dir` | str | cookie | 登录态目录（**必须落在 danmaku_listener/ 之外**；可指向外部目录复用登录态） |
| `platforms` | map | 6 true | 12 平台启用开关（持久；关闭联动 stop 该平台运行中房间） |
| `rooms` | list | [] | 房间权威源 `[{platform, room_id, enabled, active}]`（**active 至多一个**——互动房间显式指定） |
| `keywords` | list | [] | 屏蔽关键词（持久；仅过滤弹幕内容） |
| `keyword_filter_enabled` | bool | true | 关键词过滤总开关 |

**旧键迁移映射**：`ws_port`/`ws_bind`/`require_token`/`token_injection`/`token_file_path`/`legacy_adapter_retired` → 已废除（进程内化后无对应概念）；`room_id` → `rooms[].room_id`；`cookie_dir` 旧外部路径仍兼容（不迁移则登录态继续用原目录）。

## 托管层 API（utils/danmaku_consumer.py）

控制面经 main.py 进程 FastAPI `/danmaku/*` 暴露（前端唯一通道）；进程内直调签名如下。**参数序与组件 Bridge 一致**：`add_room(room_spec, platform)` room_spec 在前；`start_room/stop_room/remove_room(platform, room_id)` platform 在前。

| 方法 | 用途 | 持久语义 | 可能错误 |
|---|---|---|---|
| `add_room(room_spec, platform="", start=True)` | 添加房间（链接自动识别） | 写回 config.rooms | 400 格式/409 重复/room.platform_disabled |
| `remove_room(platform, room_id)` | 删除房间 | 写回 config.rooms | 404 |
| `start_room(platform, room_id)` | 启动监听 | 运行时态（重启按 enabled 恢复） | 404/400/500/login_required |
| `stop_room(platform, room_id)` | 停止监听 | 运行时态 | 404 |
| `add_keyword(kw)` / `remove_keyword(kw)` | 关键词管理 | 写回 config.keywords | keyword.invalid |
| `set_keyword_filter(enabled)` | 过滤总开关（显式 set） | 写回 config | — |
| `set_service_enabled(enabled)` | 服务总开关 | 写回 config.enabled | — |
| `set_platform_enabled(platform, enabled)` | 平台开关（false 联动 stop） | 写回 config.platforms | — |
| `get_rooms()` / `get_status()` | 状态查询（含生命信号/队列/守护计数） | 只读 | — |

`start=False` 为预配置逃生门：仅写 config 不启动（下次重启按 enabled 恢复）。

## 12 平台就绪与登录态矩阵

| 平台 | 引擎 | 登录态 |
|---|---|---|
| 哔哩哔哩 bilibili | protocol 直连（游客可听六类消息） | storage_state（扫码闭环） |
| 斗鱼 douyu | TCP STT 直连 | 无需（游客可听） |
| 抖音 douyin | Web WS 直连（quickjs 签名） | cookies.json（**礼物仅推登录观众**） |
| 虎牙 huya | WS + Tars | 无需（游客可听） |
| 快手 kuaishou | WS + protobuf | storage_state（web 强制登录态） |
| 淘宝 taobao | mtop 双通道轮询 | 无需 |
| 1688 | 受控页面拦截 | persistent profile |
| 美团 meituan | HTTP 轮询 | 无需 |
| 小红书 xiaohongshu | 受控页面 WS | persistent profile |
| 拼多多 pdd | 受控页面 Titan 四层解码 | persistent profile |
| 京东 jd | 受控页面咚咚 IM | persistent profile |
| 视频号 wechat_channels | 受控后台轮询 | storage_state（扫码常规路径） |

登录态文件统一在 `cookie_dir`（默认仓库根 `cookie/`）。**注意**：同步/备份工具会把该目录的登录凭据带出机器——请勿将其纳入网盘/git。

## 控制面 API 与端点退役对照（R2 配置运行分离）

**写者分配矩阵**：配置期（main.py 未运行）→ webui 直写 config.json；运行期（running）→ 房间开关/关键词经窄端点（main.py 磁盘 RMW + 内存节刷新 + 联动启停，即时生效）。

| 旧端点 | 状态 | 替代路径 |
|---|---|---|
| `POST /danmaku/rooms` | **410 Gone** | webui 平台配置页直写 config.rooms |
| `DELETE /danmaku/rooms/{p}/{r}` | **410 Gone** | webui 删除（运行期自动先停监听） |
| `POST /danmaku/keywords` / `DELETE /danmaku/keywords/{kw}` / `POST /danmaku/keywords/toggle` | **410 Gone** | webui 直写 config.keywords（运行期自动热生效） |
| `POST /danmaku/platform/{p}/toggle` | **410 Gone** | platforms 节=可添加平台白名单；监听启停走房间级开关 |
| `POST /danmaku/rooms/{p}/{r}/enabled` | **新增（窄端点）** | 请求 `{enabled: bool}` → 响应 `{success, listening, room?, status?, error?}`；404 房间不存在；幂等 |
| `POST /danmaku/keywords/reload` | **新增（窄端点）** | 无请求体——一律从磁盘 config 读（防注入）；响应 `{success, count, enabled}` |
| `POST /danmaku/service/toggle` | 增强 | enabled=true 且服务未构建时在线构建启动（503 死路修复） |

410 响应体为 RFC 9457 problem+json：`{type, title, status, detail, cause, fix, instance}`。

## 配置期登录（webui 进程登录服务）

- **支持平台**：哔哩哔哩 / 快手 / 抖音（组件 *_login.py 可 import 闭环）
- **其余平台**：启动系统后首次监听时按引导自动登录（运行期 login_gate 全平台覆盖）
- 登录态判定为**首登引导**；运行期以引擎事件为唯一真相源
- 同平台多房间共享登录态；登录窗口 300s（可在 danmaku_listener.login_wait_timeout 配置）

## 商用前置警示

进程内形态**仅限个人使用**。商用前必须重新评估隔离形态（回退方案 B：修复 serve 接线的托管子进程，见整合计划第 3 节 A/B/C 决策记录）与监听账号隔离（专用监听账号，严禁客户主账号）——见 TODOS.md S9。

## 发送通道（v0.7.0 合并新增，2026-10-12）

- 发送经组件 web_console 四宫格（`http://127.0.0.1:8086`，`danmaku_listener.web_console=true`）：房间列表 + 启动/删除/发送/登录按钮；发送等状态变更路由强制 Bearer token（`persistence_data/send_token.txt`；页面首次发送 prompt 一次并 localStorage 缓存）。web_console 无页面级鉴权且仅绑 127.0.0.1。
- 回执四态（线格式小写）：`sent` / `failed`（reason_code+fix_hint+docs_anchor 三段式）/ `dry_run`（已记录未实发，不得视为已实发）/ `unknown`（可能已送达——禁止盲重）。对账：`GET /api/send-results?request_id=`（鉴权同 POST）。预算 60s，超时走对账。
- **一键回退开关**：`config.json` 的 `danmaku_listener.send_enabled_platforms` 置空（`""`）即整体关闭发送，监听不受影响（改后重启生效）；逐平台停发从该键移除对应平台名。
- **发送屏蔽词（2026-10-13 词表分离）**：`danmaku_listener.send_blocked_keywords`（逗号分隔，默认空=发送不拦词）——发送守卫的独立词表，与接收侧弹幕过滤 `keywords` 分离（接收过滤≠发送限制；此前复用接收词表导致短词如 "sb" 子串匹配误拦正常发送，全平台 KEYWORD_BLOCKED）。改后重启监听生效（关再开「启用监听」）。
- **风控基线**：限速 3s/平台（虎牙 35s 内置兜底，可被 `send_min_interval_overrides` 胜出，但下调须对证风控实测——虎牙 10-14s 实测失败）；连续失败 5 次熔断；`sent` 回执证明发出、不证明未被平台影子限流——实发可见性人工抽检为例行运维节奏。
- **浸泡门槛（R8）**：上线后连续 7 个直播时段主进程零"组件致重启"方视为整合完成。日志锚点：`[danmaku_listener]`（守护重建/停机）、`[send-guard]`（熔断/限速/overrides 解析）、sender 异常栈（审计 result 行 reason_code）、`web_console_error` 事件；**发送常驻会话异常单列计数**（守护重建计数仅覆盖监听 Bridge）。两周内 ≥2 次主进程异常（组件致）→ 自动复议独立进程形态（UC-1 复议，输入=R8 故障域数据）。
- 运维详单：上游 `D:\AI\DanmakuListener\docs\ops\send-runbook.md`（不随拷贝，仅供对证）。

## fastrag-python 本地补丁台账（2026-10-15 EDTalk 整合批次，Eng F-E10 纪律）

| # | 文件 | 变更 | 原因 | 上游 HEAD |
|---|---|---|---|---|
| P1 | fastrag-python/config.py | KB_DIR/INDEX_DIR 改指父级（knowledgebase/kbindex_py 统一） | 数据位置与 rust 版统一+python 索引格式隔离 | （fastrag 无 git 仓，副本基线 2026-07-24） |
| P2 | fastrag-python/server.py | uvicorn host 0.0.0.0→127.0.0.1 | Eng F-E7 高危绑定收敛 | （同 P1） |
| P3 | fastrag-python/config.py | 末尾加载 config_override.py 外部覆盖 | 参数页落盘重启即生效（F-E10 优先方案） | （同 P1） |
