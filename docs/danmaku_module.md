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
2. **重跑复制**：`python Scripts/copy_danmaku.py` —— 副本整体替换为上游最新（注意：`danmaku_listener/` 会被 rmtree，登录态/关键词/注册表在副本外不受影响）
3. **契约漂移检查**：`python Scripts/export_contract.py --check`
4. **hook 存在性断言**：冒烟脚本首步断言 `DanmakuBridge._broadcast` 等被子类 hook 的成员签名仍在（私有方法不受上游 API 承诺保护，防同步后静默断流）
5. **冒烟**：重跑阶段 1

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

## 商用前置警示

进程内形态**仅限个人使用**。商用前必须重新评估隔离形态（回退方案 B：修复 serve 接线的托管子进程，见整合计划第 3 节 A/B/C 决策记录）与监听账号隔离（专用监听账号，严禁客户主账号）——见 TODOS.md S9。
