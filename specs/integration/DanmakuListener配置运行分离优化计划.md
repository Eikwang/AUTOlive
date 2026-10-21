> **📦 已归档历史档案**（2026-10-21 批次标注）：本文件为整合期计划/纪要快照，记录当时决策与实施过程，不随现行代码演进；现行事实以 docs/ 与代码为准。

<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-AUTOlive\\main-autoplan-restore-20261006-r2.md" -->
# DanmakuListener 配置面与运行面分离优化计划

> **状态：APPROVED（2026-10-06 终门裁决 A——配置面与运行面分离计划批准实施；写者分配矩阵/配置期登录三平台范围/platforms 白名单语义/三态分流全部裁决闭环）。**

> /autoplan SOURCE_PLAN（2026-10-06 第二轮，用户实测反馈 4 项）。第一轮（进程内整合）已批准实施；本轮为 UX 反馈迭代：**参数配置与运行逻辑分离**——配置期（未运行系统）即可完成添加房间/登录保存 cookie/关键词屏蔽；运行期才做监听。

## Implementation plan

## 1. 用户原始需求（4 项实测问题）

1. **移除"平台登录"独立按钮**——登录应在添加直播间后自动弹出，用户登录后自动保存 cookie（DanmakuListener 逻辑）；独立按钮无存在意义
2. **关键词屏蔽在配置期失败**——提示"无法连接主程序控制面"；关键词是纯配置项（写配置文件），不应受运行状态限制——系统就是配置完成后再启动
3. **"房间管理-平台启用"开关位置错误**——正确形态：添加房间时选平台，房间列表中每行显示开关（启用/停用该房间监听）+ 删除按钮；平台级开关应移除
4. **添加直播间后立即显示房间列表并检查登录状态**——未登录自动弹登录页并保存 cookie；启动系统后对（已启用的）房间进行监听。**DanmakuListener 上游已更新平台登录检测优化（login_gate.py 统一登录门 + 全平台登录增强），需同步更新副本**

**核心目的：参数配置与运行逻辑分离。**

## 2. 现状核实（2026-10-06 实地核查）

### 2.1 上游优化（D:\AI\DanmakuListener 工作区，未提交——af9124c 之后）

- **新增 `danmaku_listener/engines/login_gate.py`**：统一登录门基建——`LOGIN_COOKIE_NAMES`（各平台登录判定 cookie 名表：taobao/1688=unb、jd=pt_key+pt_pin、xiaohongshu=web_session、douyu=dedeuserid、huya=yyuid 等多候选）、`has_login_cookie()` 判定、登录窗口预算（LOGIN_WAIT_TIMEOUT=300s、LOGIN_BUDGET=2 次）、事件词表（login.first_login/relogin_triggered/timeout_budget_exhausted/degraded_detected）
- **registry.py**：PLATFORM_WARNINGS 全面更新——**全平台"首次添加自动弹登录窗口"**（douyu/huya/jd/xiaohongshu/taobao/1688 全部加上登录增强；taobao 从"轮询直连"改为"mtop 双通道受控页面+需登录"）
- 修改：controlled_base.py、douyu/huya/jd/taobao/xiaohongshu 引擎（登录闭环接入）
- 评审文档：docs/plans/2026-10-06-platform-login-gate-review.md（今日）
- 既有：bilibili/kuaishou/douyin 登录闭环（*_login.py）已在 v0.4.0
- **同步方式**：`Scripts/copy_danmaku.py` 重跑（复制工作区文件，含未提交改动；rmtree 副本后全量复制）

### 2.2 AUTOlive 侧现状（第一轮实施后）

- **配置面与运行面耦合的根源**：房间/关键词的全部操作都走 `POST/DELETE /danmaku/*`（main.py 进程 FastAPI）——main.py 未运行（enabled=false 或未点运行系统）时服务不存在 → 503 → 前端提示"无法连接主程序控制面"（用户实测问题 2）
- **webui 进程已有配置写盘通道**：`set_config_callback`（layout.py，set + save_config 双回滚）——前端所有配置项的既有落盘方式，**房间/关键词可以走同一通道直写 config.json**
- **"平台登录"按钮**：`danmaku_login.py` `_build_entry()` 挂载（layout.py 侧边栏）——独立入口，与添加房间流程脱节（用户问题 1）
- **平台启用开关区**：`platform_config.py` 卡片②顶部 12 平台紧凑开关行（P2-0 ⑥）——用户裁定位置错误（问题 3）
- **登录现状**：添加房间（start=True）→ Bridge `_login_gate`（仅 bilibili/kuaishou/douyin 有登录闭环）→ NEEDS_LOGIN 事件 → 弹窗。**配置期（服务未启动）无任何登录能力**
- **cookie 目录**：`cookie_dir`（config 单源，默认仓库根 cookie/）——webui 与 main.py 进程共享路径 ✓（登录态文件可跨进程接力）

## 3. 核心架构决策：配置面直写 + webui 进程登录服务

```
[配置面——webui 进程，配置期完整可用]
「基础设置-平台配置」页
 ├─ 房间管理：添加/删除/每房间启用开关 → set_config_callback 直写 config.json
 │   （既有双回滚通道；真相源=config.rooms[]；不经 /danmaku API）
 ├─ 关键词屏蔽：增删/开关 → set_config_callback 直写 config.json
 └─ 登录服务（新增 danmaku_login_service.py，webui 进程内轻量后台）：
      添加房间后自动检测登录态（login_gate.has_login_cookie + 平台 cookie 名表）
      → 未登录弹登录窗口（组件 run_login_flow，独立 asyncio 线程）
      → cookie 保存至 cookie_dir（跨进程接力：main.py 运行时读取同目录）

[运行面——main.py 进程，仅运行系统启动后]
 /danmaku/* 收缩为运行时面：房间监听启停、事件流、状态
 启动时按 config.rooms[].enabled 注入监听（已有语义）
 /danmaku 的房间/关键词写操作端点退役（配置面接管）
```

**写者分配矩阵（Eng F-ENG-1/2 critical 裁决）**：双进程各自持有内存 config 快照（main.py 的 Config 类级缓存无热重载；webui 的 Settings.save() 全量 dump 会覆盖回滚 main.py 运行期写入——"os.replace 兜底"只防撕裂不防丢失更新）。裁决：
- **运行期（服务在线）**：房间/关键词/开关写**只由 main.py 执行**——全部经窄端点（磁盘 RMW `_write_config_section` + **同步刷新 main.py 内存节**：原地更新 `Config.config["danmaku_listener"]` 类级缓存 dict + Bridge 内存态），webui 只发请求；`_active_room`/`_section` 读内存即新值
- **配置期（服务离线）**：只由 webui 直写（set_config 通道）
- **webui save() 节保护**：dump 前把磁盘上 `danmaku_listener` 节读出替换内存该节（该节唯一 main.py 写者；其他节维持 webui 内存）——防全量 dump 回滚；补并发回归测试（两 RMW 交错不丢对方写入）
- `_active_room` 对窄端点新添加 active 房间的回归用例必须存在

决策依据：
- 用户明示"参数配置与运行逻辑分离"；配置写盘走 webui 既有 `set_config_callback` 通道（复用，原则 P4 DRY）——零新增服务依赖
- 登录需要 playwright 浏览器弹窗，必须在本地进程跑——webui 进程独立跑登录（组件 run_login_flow 可直接 import，cookie 落共享 cookie_dir，运行时 main.py 的引擎按平台读取同目录登录态——组件 login_gate 已按平台定义判定 cookie）
- 上游登录门优化同步后，运行时加房间的 `_login_gate` 与配置期登录服务**共用同一套组件登录闭环**（原则 P4：组件已实现的不重建）

代价（明示）：
- webui 进程跑 playwright 登录浏览器——与 main.py 运行时引擎可能并发访问同一 persistent profile（Playwright profile 目录锁）——缓解：登录服务在启动监听前完成（配置期），运行期登录请求提示"该平台房间监听中，先停止再登录"（Design/Eng 详审）
- 配置真相（config.rooms）与组件注册表（bridge._rooms）双源依旧——运行面注入以 config 为权威（既有裁定，风险 6 语义延续）

## 4. 模块一：上游同步 + 副本更新

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P1-1 | 上游同步：重跑 `Scripts/copy_danmaku.py`（复制工作区含 login_gate.py 等未提交优化）→ `COPY_MANIFEST.json` 版本戳更新；hook 存在性断言扩展（`_broadcast`/`_rooms_file`/`_load_blocked_keywords` + **login_gate 模块接口**：`LOGIN_COOKIE_NAMES`/`has_login_cookie`）；上游评审文档 docs/plans/2026-10-06-platform-login-gate-review.md 归档进 COPY_MANIFEST 记录（Eng F-ENG-14）；**同步前上游 stash patch 存档 + manifest 记录上游基线 commit af9124c（CEO F8）**；单测与冒烟全过 | danmaku_listener/、COPY_MANIFEST.json | 冒烟 PASS；login_gate import 可用；`git status` 副本变化符合上游 diff 清单 |

## 5. 模块二：配置面直写（webui 进程，核心重构）

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P2-1 | **房间管理配置面直写**：添加房间 → `set_config_callback("danmaku_listener", "rooms", ...)` 直写 config.rooms（配置期不在运行时 add_room）；删除同理；每房间 enabled 开关（取代平台级开关）；room_id 归一用 `platform_parser.extract_from_link` 纯函数（短链原文保存，运行时注入引擎归一——已知限制）。**写前校验（CEO F5 + DX 发现 6）**：重复房间拒绝、平台枚举合法（PLATFORM_ENGINES 键集合∩platforms 白名单）、房号/链接非空、链接格式试解析（extract_from_link 失败时提示"无法识别的链接格式，将按原文保存，运行时可能无法监听"由用户确认）——webui 写前校验函数 + 单测。**enabled 默认值（DX 发现 1）：添加即 enabled=true**（与空态引导/验收 2 对齐——防首次上手静默失败），文案表补"已保存·待启动"初始态。**写失败路径（DX 发现 5）**：写盘失败 → toast（问题+原因+重试）+ 开关/徽章不翻转 + 写失败注入用例。**拆子任务（DX 发现 2）**：写前校验函数 → 配置面直写 → 运行态分流/TOCTOU 三步小步 TDD；验收命令具体化（`pytest tests/test_danmaku_consumer.py -k room_config` 等）**运行态开关分流（CEO F1 critical + Eng F-ENG-5 三态化）**：分流判定显式三态——① 连接失败 → main.py 未运行 → 直写 config；② `/danmaku/status` 返回 enabled=false → 服务未构建 → 直写 config + 提示"启动系统后生效"；③ running → 窄端点 `POST /danmaku/rooms/{platform}/{room_id}/enabled`（config-first：main.py 磁盘 RMW + 刷新内存节 + 联动 start/stop 即时生效）。**503 死路修复（F-ENG-5）**：/danmaku/service/toggle 在服务未构建（None）时补"可构建"分支（直接 build + start，无需重启 main.py）——用户运行中开总开关不再被 503 卡死。TOCTOU 回退链：窄端点失败 → 重查 status → 按三态分流**TOCTOU 回退链（Design M-2）**：窄端点连接失败 → 重查 /danmaku/status → 确认离线则降级直写 config，仍在线则报错 toast + 重试按钮。**运行期生效矩阵（Design M-1 critical，三格全定义）**：① 运行中添加房间 → 直写 config + 窄端点 enabled=true（可寻址启动时未注入的房间，注入并 start）→ 即时生效，UI 提示"已保存并开始监听"；② 运行中删除 → 两步走：先窄端点 enabled=false（停监听）再删配置——禁止"删了还在跑"；③ 运行中改关键词 → 直写 config + 窄端点 `POST /danmaku/keywords/reload`（Bridge 内存态从 config 热载——关键词过滤是运行时行为，必须热生效；核实起点 tests/test_config_hotreload.py）。**每格必须有 UI 反馈，禁止静默** | frontend/ui/tabs/platform_config.py | 配置期添加/删除/开关全部生效并持久化；**新增验收：运行中切换房间开关 → 该房间监听即时启停且 config 同步**（F1 回归用例）；校验函数单测（重复/非法平台/空房号） |
| P2-2 | **关键词配置面直写**：增删/开关 → set_config_callback 直写 config.keywords/keyword_filter_enabled；移除对 /danmaku/keywords 的依赖；运行期经 reload 窄端点热生效（M-1 ③）。**关键词单向流契约（Eng F-ENG-4）**：config.keywords 唯一权威 → reload 注入 bridge 内存 → 外置文件（persistence_data/blocked_keywords.json）仅为组件内部缓存——启动注入后以 config 为准覆写外置文件（约定固化）；reload 端点**禁止 body 传词表**（一律从磁盘 config 读——契约测试断言，Eng F-ENG-11）。**错误文案目录（DX 发现 4）**：全部错误路径（校验拒绝/TOCTOU 降级/登录超时失败/互斥/410/写失败）统一挂 docs/danmaku_module.md 锚点；S-2 文案表扩展覆盖"操作被拒"类 toast（重复房间/非法平台/空房号/链接无法识别） | frontend/ui/tabs/platform_config.py | 配置期关键词增删/开关全部生效（问题 2 根治）；错误文案目录覆盖全部错误路径 |
| P2-3 | **平台启用开关区移除**：卡片②的 12 平台开关行删除；**`platforms` 节语义裁决（CEO F6）：保留为"可添加平台白名单"（引擎可用性过滤，仅约束添加时的平台枚举；不再承载运行启停）**；**grep 审计完成（Eng F-ENG-3）**：消费者唯一=`_inject_rooms`（danmaku_consumer.py:787-808 启动注入过滤）+ `add_room._platform_enabled` + `room_skip_disabled_platform` 事件——**三处同步退役**：`_inject_rooms` 移除平台过滤（**rooms[].enabled 成为唯一注入裁决**）、platform_disabled 分支与事件退役（fix_hint 死胡同）、`_PLATFORM_STATES.platform_disabled` 死状态清理（Eng F-ENG-9）；**验收用例：platforms 值 false + room.enabled=true 的房间仍注入**；房间列表行内开关接管启停语义 | frontend/ui/tabs/platform_config.py、config.json | 平台开关区不渲染；房间行内开关（问题 3 根治）；platforms 消费者审计清单落 Review record |
| P2-4 | **房间列表重构**：数据源 = config.rooms（配置真相：房间号/平台/启用开关/删除按钮）+ 运行态叠加（服务在跑时：监听中/已停止/生命信号——经 /danmaku/status 只读叠加）；未运行系统时显示"未监听（启动系统后开始）"态。**叠加 join 规则（Eng F-ENG-6）**：join 键 = platform + 归一 room_id 优先；无法归一的短链条目挂"待运行确认"态 + best-effort 原文匹配；ghost 条目（运行态有配置无）不渲染；验收含短链用例。**room_spec 长度上限 200 字符（Eng F-ENG-9）**。**GET /danmaku/keywords 保留**（数据源=运行态 Bridge 内存，文档注明语义，Eng F-ENG-9）。**页面规格（Design D-1/D-2/D-4）**：卡片顺序=房间管理（主对象）在前、关键词屏蔽在后；添加表单=平台选择器（分组下拉、中文名+图标，仅渲染白名单内引擎可用平台，常用平台前置）→ 房号/链接输入 → 确认按钮；关键词卡=全局过滤开关 + 词条列表（输入添加、行内删除，**无词条级开关**）。**行内三状态视觉位（Design D-3）**：房号+平台为主信息、登录态用徽章（三级：未登录灰可点登录 / 已登录·待运行确认蓝 / 运行中已验证绿——Design J-3）、运行态用状态点+文字、启用保持标准开关。**状态文案表（Design S-2）**：全部新状态给中文文案（待登录/登录中/登录超时/已保存·待启动/监听中/已停止/收流停滞）；动词统一——"启动系统"（运行）、"开始/停止监听"（房间）。**空状态（Design M-4）**：三步引导"选平台 → 填房号 → 扫码登录"；关键词空态一句话+添加入口。**过渡态（Design M-5）**：检测登录态中…/浏览器启动中…/保存中…逐步反馈。**批量位预留（Design A-2）**：列表头部预留批量启停控件位（本期不实现） | frontend/ui/tabs/platform_config.py | 配置期与运行期列表均正确渲染；运行态叠加不影响配置操作；状态文案表覆盖全部状态 |

## 6. 模块三：webui 进程登录服务（配置期登录闭环）

| # | 任务 | 目标文件/位置 | 验收 |
|---|------|--------------|------|
| P3-1 | **登录服务**（新增 `frontend/ui/components/danmaku_login_service.py`）：webui 进程内轻量后台。**配置期登录范围裁决（Eng F-ENG-13 high）：仅 bilibili/kuaishou/douyin 三平台**（组件 `*_login.py` 的 `has_login_cookie()`/`run_login_flow()` 可直接 import 且签名已知；其余 6-9 平台的登录入口埋在引擎类内部/需运行期参数——不可 import，**行内徽章显示"启动系统后首次监听时登录"**，运行期引擎 `login_gate` 全平台登录闭环覆盖——上游已优化）。**平台登录元数据单源（Eng F-ENG-12）**：webui 侧 `_PLATFORM_LOGIN_META` 常量（三平台 cookie 名表+登录态文件路径，标注来源与上游同步点；`LOGIN_COOKIE_NAMES` 无 bilibili/kuaishou/douyin——不依赖它，否则恒判未登录误弹窗）。`check_login(platform)` 判定登录态文件 + cookie 名表；`run_login(platform)` 独立 asyncio 线程跑组件 `run_login_flow`（可见浏览器扫码）→ cookie 保存 cookie_dir；完成事件经状态对象供 UI 轮询。**生命周期（Eng F-ENG-8）**：模块级单例；`onboard_room` 按 config.rooms 幂等去重；状态机增"浏览器启动失败"终态（无头环境/RDP 下 headless=False launch 失败可参照 login_gate window_closed 分支）；webui 退出钩子关浏览器。**跨进程登录互斥（Eng F-ENG-7）**：webui 登录发起前重查 `/danmaku/login_state`（active 非空或 queue>0 → 提示"运行期登录进行中"）。**运行期互斥**：main.py 服务在跑且该平台有运行中房间 → **可操作提示（Design J-2）**："该平台房间监听中" + 一键动作「停止该房间并登录」（窄端点停 → 发起登录）。**Cookie 格式对照矩阵（CEO F3）**：三平台 cookie 名表 × 落盘格式对照进 docs/danmaku_module.md；**storage_state JSON fixture 判定单测（Eng F-ENG-10，不弹浏览器）**；手动验证两种落盘格式各一例。**profile 并发方案对比（CEO F10）**：时序分离 vs 每进程独立 user-data-dir + cookie 同步——设计评审裁决；维持时序分离则"运行期重登"登记 TODOS。**日志纪律（Eng F-ENG-11）**：cookie 值一律 login_gate.mask_cookie 掩码；**日志事件对齐上游 login.* 词表（DX 发现 9），状态机每次迁移结构化日志** | frontend/ui/components/danmaku_login_service.py | 配置期登录（三平台）→ 浏览器弹出 → 扫码 → cookie 落盘（两种格式各验证一例）；对照矩阵落盘；状态机各态可达；fixture 判定单测过 |
| P3-2 | **添加房间后自动登录检测**：添加房间成功（config 写回）→ `onboard_room(platform, room)` 编排（CEO F2 单入口）→ 自动调 `check_login(platform)` → 未登录自动弹登录窗口（用户问题 4）。**登录态粒度 = 平台级（Design S-1 high）**：cookie 同平台多房间共享——check_login 结果按平台缓存，列表渲染引用平台级状态 + 行内 tooltip"同平台共享登录态"；登录成功同平台全部行徽章翻转。**登录状态机（Design M-3）**：排队中/启动浏览器中/等待扫码/保存中/成功/失败（用户关闭浏览器）/超时——**同一时刻仅一个登录流（串行队列）**；用户关闭 → failed 态 + 行内"重试登录"入口；**非模态（Design A-1）**：登录后台跑，页面状态 chip 呈现，期间配置操作照常可用；**浏览器置前（Design J-1）**：登录浏览器强制置前 + webui 同步提示"请完成扫码登录" + 登录成功浏览器自动关闭 | frontend/ui/tabs/platform_config.py | 添加未登录平台房间 → 自动弹窗 → 扫码 → cookie 保存 → **同平台**列表徽章翻转（问题 4 根治）；状态机各态可达 |
| P3-3 | **"平台登录"按钮移除**：`danmaku_login.py` 的 `_build_entry`/侧边栏挂载点（layout.py）删除；登录入口统一为"添加房间后自动弹窗"；`login_entry` 配置键退役；弹窗本体保留（登录流程复用） | frontend/ui/components/danmaku_login.py、frontend/ui/layout.py、config.json | 侧边栏无"平台登录"按钮（问题 1 根治）；弹窗在自动登录流程中正常出现 |
| P3-5 | **端点退役文档（DX 发现 7 high）**：docs/danmaku_module.md 增端点退役对照表（旧端点 → 410 → 替代路径示例）；410 响应体规范（RFC 9457 problem+json 形态：problem+cause+fix+docs 锚点）；消费者 grep 审计清单（测试之外全部调用方）落 Review record；新登录入口与旧"平台登录"按钮行为差异说明（DX 发现 8c）；手改 config.json 为受支持逃生口的文档化（DX 发现 8a）；登录超时预算参数化到 danmaku 配置节（`login_wait_timeout`/`login_budget`，默认沿用上游 300s/2——DX 发现 8b，P1-1 同步时确认与上游常量不冲突） | docs/danmaku_module.md | 对照表/410 规范/审计清单/逃生口文档齐备 |
| P3-4 | **运行面端点收缩**：退役房间增删（POST/DELETE /danmaku/rooms）与关键词写端点——**统一返回 410 Gone + 一致性测试断言（CEO F9 防复活）**，相关旧测试同步删除/迁移；保留运行时面：start/stop_room、status、events、login_state（运行态部分）、**新增窄端点 `POST /danmaku/rooms/{platform}/{room_id}/enabled`**（F1：写配置 + 联动该房间启停；**契约定义（DX 发现 3）**：请求体 `{enabled: bool}`、响应 `{success, room, listening}`、错误码 404 房间不存在/400 平台不匹配/500 写配置失败、幂等（重复同值调用返回当前态）；**契约一致性测试**仿 410 断言做法，契约落 docs/danmaku_module.md）；**新增关键词热载端点 `POST /danmaku/keywords/reload`**（同契约规范）；`danmaku_consumer.py` 对应方法收缩（`set_platform_enabled` 退役）。**前置核查（CEO F4）**：枚举 main.py 运行期全部 config.json 写路径及触发时机——若存在运行中写，房间/关键词写收敛为读-改-写加文件级重试 | utils/danmaku_api.py、utils/danmaku_consumer.py | 退役端点 410 断言；窄端点联动启停验证；main.py 写路径枚举记录于 Review record；main.py 启动按 config.rooms[].enabled 注入验证 |

## 7. NOT in scope

- P3 退役批次（utils/platforms/ 12 模块、根目录旧资产删除）——维持原计划：F3 浸泡通过后执行
- 多房间对话链扩展（S7，已登记 TODOS）
- 运行期"会话失效重登"的行为证据检测（login_gate 注释明示 cookie 存在性判定仅用于首登；会话失效重登依赖运行时引擎的 NEEDS_LOGIN 事件——已有链路）
- 手机端 UI

## 8. 风险与待验证点

1. **Playwright profile 并发**：配置期登录（webui 进程）与运行期监听（main.py 进程）并发访问同一 persistent profile——目录锁冲突；缓解：运行期登录互斥提示 + 登录在配置期完成（时序上分离）
2. **`platforms` 节语义降级**的兼容——已存配置的 platforms 值不丢（保留读取），UI 不再渲染开关
3. **webui 进程 import 组件引擎模块**（login_gate/registry 纯函数与常量）——确认这些 import 不拖入 playwright/quickjs 重依赖（组件 pitfall：eager-import-heavy-deps；login_gate.py 自身仅 loguru+标准库 ✓，registry 会拖 engines 包——按需 import login_gate 模块本身，避免 registry）
4. **添加房间的 room_id 归一在 webui 进程**：`normalize_room_id` 是引擎方法（拖引擎依赖）——改用 `platform_parser.extract_from_link`（纯函数）+ 添加时不下发引擎归一（链接无法静态提取的短链场景 → 保存原始链接，运行时注入时引擎归一——组件 normalize_room_id 已处理）；短链房间号在配置期显示为链接原文（可接受的已知限制）
5. **config.json 双写者**（webui set_config 与 main.py 托管层 _write_config_section）——既有双写者格局（低频短窗口），P1-3 的模块锁仅 main.py 进程内——跨进程以 os.replace 原子性兜底（既有基线）
6. **登录窗口在配置期弹出后用户不扫**——LOGIN_WAIT_TIMEOUT 300s 超时 → 状态置"登录超时"，房间保持已添加（enabled 照存），可重试

## 9. 验收标准

1. **配置期完整闭环（main.py 未运行）**：添加房间（选平台+房号/链接）→ 房间列表出现（含开关/删除）→ 自动登录检测 → 未登录自动弹窗 → 扫码 → cookie 保存 → 关键词增删/开关生效——**全程无"无法连接主程序控制面"**
2. **运行期**：点运行系统 → 已启用房间自动开始监听（既有注入语义）→ 房间列表叠加运行态 → 启停按钮工作
3. **"平台登录"独立按钮消失**；平台开关区消失
4. **上游同步**：login_gate.py 进副本；全平台 PLATFORM_WARNINGS 更新生效
5. 测试：既有 30 项托管层单测无回退（Eng F-ENG-10 核准数字：tests/test_danmaku_consumer.py 现为 30 个 test 函数）；新增配置面/登录服务单测过（**storage_state JSON fixture 判定单测不弹浏览器**；**fake service + 窄端点 handler 直测 config-first 写/联动启停/幂等/TOCTOU 降级**——Eng F-ENG-10 跨进程测试策略）；**并发写回归：webui save 后 main.py 写入不丢（F-ENG-2）**；冒烟 PASS
6. **登录态真相源契约（CEO F7）**：运行期引擎事件（NEEDS_LOGIN/握手结果）为登录态唯一真相源；配置期 cookie 存在性判定仅为**首登引导**；UI 文案区分"未登录过"（可发起登录）与"登录待运行期确认"——不承诺配置页显示的登录态保证运行期有效
7. **向导接缝（CEO F2）**：`onboard_room(platform, room)` 编排函数（添加→检测→弹窗→列表刷新收敛为单入口）——为后续"平台接入向导"留接缝
8. **上游锚点（CEO F8/F11）**：同步前请上游提交（或 stash patch 存档），COPY_MANIFEST 记录上游基线 commit；登记"副本同步成本复查点：若下次同步仍需人工对账脏改动，启动上游提交前置流程"

## Review record

<!-- autoplan-accepted:eng -->
- Eng 评审（2026-10-06 第二轮）：28 次代码核查，有条件放行——16 项发现（2 critical + 4 high + 8 medium/low + 2 项确认）全部裁决落盘。INPUT: eng c6b682d39829571c82a97eb8b10567546b079b27c3404966dfb8056f852420ed 校验 ✓。
- **F-ENG-1/2（critical，本期最大隐藏复杂度）**：双进程内存 config 分叉（main.py Config 类级缓存无热重载；webui Settings.save() 全量 dump 覆盖回滚）——**写者分配矩阵裁决**：运行期房间/关键词/开关写只由 main.py 经窄端点执行（磁盘 RMW + 同步刷新内存节 `Config.config["danmaku_listener"]` 原地更新 + Bridge 内存态），webui 只发请求；配置期只由 webui 直写；webui save() dump 前把磁盘 danmaku_listener 节读出替换内存该节（防全量 dump 回滚）；并发回归测试（两 RMW 交错不丢写入）。
- **F-ENG-5（high）**：分流三态化——连接失败/服务未构建(enabled=false)/running；**503 死路修复**：service/toggle 在 None 时补"可构建"分支（build+start 无需重启）。
- **F-ENG-13（high）**：配置期登录范围裁决=**仅 bilibili/kuaishou/douyin**（组件可 import 闭环）；其余平台徽章"启动系统后首次监听时登录"（运行期 login_gate 全平台覆盖——上游已优化）；webui 侧 `_PLATFORM_LOGIN_META` 单源常量（不依赖 LOGIN_COOKIE_NAMES——其无三平台名表，否则恒判未登录误弹窗）。
- **F-ENG-3（high）**：platforms 消费者审计完成——`_inject_rooms` 过滤/`add_room._platform_enabled`/`room_skip_disabled_platform` 事件三处退役；rooms[].enabled 唯一注入裁决；验收用例 platforms=false+enabled=true 仍注入。
- F-ENG-4：关键词单向流契约（config 唯一权威→bridge→外置文件缓存）；reload 端点禁 body 传词表（契约测试断言）。
- F-ENG-6：叠加 join 键规则（归一优先/短链"待运行确认"态/ghost 不渲染/短链验收用例）；room_spec ≤200 字符；GET keywords 保留注明语义。
- F-ENG-7：跨进程登录互斥（webui 发起前重查 login_state）。
- F-ENG-8：登录服务模块级单例/幂等去重/浏览器启动失败终态/退出钩子。
- F-ENG-9：_ROOM_STATES 死状态清理；login 参数作用域文档注明（仅 webui 侧生效）。
- F-ENG-10：测试策略——storage_state fixture 判定单测（不弹浏览器）；fake service+窄端点 handler 直测（config-first/联动/幂等/TOCTOU 降级）；并发写顺序断言；数字核准 38→30。
- F-ENG-11：reload 禁 body 词表断言；cookie 掩码（login_gate.mask_cookie）。
- F-ENG-12：平台清单单源——webui `_PLATFORM_LOGIN_META` 常量（三平台，标注同步点）；多源漂移登记已知债务（上游 login_gate 演进后收敛）。
- F-ENG-14：-k 测试命名约定写明；上游评审文档归档 COPY_MANIFEST；风险 3/4 核实确认（声部确认计划核实质量）。
- F4/F6 前置核查（CEO 义务）：platforms 唯一消费者=_inject_rooms（上方退役）；main.py 运行期 config 写路径端点收缩后仅剩 set_service_enabled 一处（写频下降，F4 强化不必要）。
- 声部派发偏差：等价措辞派发（journal 死锁），INPUT 人工核验 ✓；Codex unavailable（同因）。
<!-- /autoplan-accepted:eng -->

## Eng Review — Completion Summary（第二轮）

```
  +====================================================================+
  |      ENG PLAN REVIEW — COMPLETION SUMMARY                          |
  +====================================================================+
  | Scope Challenge      | 不缩减（autoplan override P2）                |
  | Section 1 (Arch)     | 3 issues（F-ENG-1/2 critical 双进程分叉、     |
  |                      | F-ENG-3 platforms 冲突、F-ENG-5 三态）→ 全裁决 |
  | Section 2 (Quality)  | 5 issues（F-ENG-4 单向流/6 join/7 互斥/8      |
  |                      | 生命周期/9 杂项）→ 全落任务                    |
  | Section 3 (Tests)    | F-ENG-10 四缺口 → 测试策略落盘 + Test Plan    |
  | Section 4 (Perf)     | 无新问题（写频下降）                          |
  | Security             | F-ENG-11 写端点校验/F-ENG-12 单源 → 落盘      |
  | Hidden Complexity    | F-ENG-13 登录能力缺口 → 范围裁决；F-ENG-14 一致性|
  | Critical gaps        | 0（2 critical 裁决闭环）                      |
  | 实地核查             | 28 次代码核验（danmaku_consumer/api/platform_ |
  |                      | config/login/layout/settings/上游 login_gate）|
  | Outside voice        | codex — unavailable（同因）                   |
  | Verdict              | 有条件放行 → 4 条件全部裁决闭环               |
  +====================================================================+
```

**2am Friday 清单**（声部点名的高危场景，全部有对应裁决）：态(2) 503 死路（F-ENG-5 三态+toggle 可构建）、运行中添加房间不互动（F-ENG-1 内存刷新+回归用例）、platforms=false 房间永不注入（F-ENG-3 过滤退役）、无头环境登录窗挂"登录中"（F-ENG-8 启动失败终态）。

**Test Plan 补充（Eng 第二轮）**：Fake service 集成测（config-first/联动/幂等/TOCTOU）、storage_state fixture 判定单测、并发写顺序断言、410 与窄端点契约一致性测试、platforms=false 注入用例、_active_room 窄端点回归用例。
- DX 评审（2026-10-06 第二轮，Mode=DX POLISH）：声部 9 项发现（3 high + 6 medium，0 critical）全部落盘，批准进入实施。INPUT: dx 7bba1781ffb5332b68f13924eca250ca8c5bc540bc00ec8652d103bb659a5bd8 校验 ✓。
- DX 发现 1（high）：添加房间 enabled 默认值=true（防首次上手静默失败），文案表补"已保存·待启动"初始态。
- DX 发现 2（medium）：P2-1 拆三子任务小步 TDD；验收命令具体化。
- DX 发现 3（high）：窄端点契约定义（请求/响应 schema、404/400/500 错误码、幂等语义）+ 契约一致性测试，落 docs/danmaku_module.md；关键词 reload 端点同规范。
- DX 发现 4（medium）：错误文案目录（全部错误路径挂 docs 锚点）；S-2 文案表扩展"操作被拒"类。
- DX 发现 5（medium）：配置写失败路径（toast+状态不翻转+写失败注入用例）。
- DX 发现 6（medium）：链接格式试解析失败提示确认/阻止（防静默保存）。
- DX 发现 7（high）：新增 P3-5 文档任务——端点退役对照表、410 响应体 RFC 9457 规范、消费者 grep 审计、新登录入口差异说明、逃生口文档化。
- DX 发现 8（medium）：三处逃生口——手改 config 文档化 / 登录超时预算参数化（login_wait_timeout/login_budget）/ 新登录入口差异说明。
- DX 发现 9（medium）：登录服务日志对齐上游 login.* 词表（webui 前缀），状态机迁移结构化日志。
- 声部派发偏差：等价措辞派发（journal 死锁延续），INPUT 绑定人工核验通过。
<!-- /autoplan-accepted:dx -->

## DX Review — Completion Summary（第二轮）

```
  +====================================================================+
  |              DX PLAN REVIEW — SCORECARD                            |
  +====================================================================+
  | Dimension            | Score  | Prior  | Trend  |
  |----------------------|--------|--------|--------|
  | Getting Started      | 9/10   | 6/10   | ↑      |
  | API/CLI/SDK          | 8/10   | 6/10   | ↑（契约补齐后）|
  | Error Messages       | 8/10   | 8/10   | —（文案目录补齐后 9）|
  | Documentation        | 7/10   | 9/10   | ↓（退役文档补后 9）|
  | Upgrade Path         | 8/10   | 9/10   | —      |
  | Dev Environment      | 8/10   | 8/10   | —      |
  | Community            | N/A    | N/A    | 个人项目 |
  | DX Measurement       | 8/10   | 8/10   | —      |
  +--------------------------------------------------------------------+
  | TTHW                 | 4 步用户路径（配置期闭环）目标达成           |
  | Competitive Rank     | Competitive                                 |
  | Product Type         | Platform + 内嵌组件库                        |
  | Mode                 | DX POLISH                                   |
  | Overall DX           | 8/10   | 7/10   | ↑     |
  +====================================================================+
```

**NOT in scope**：社区生态（个人项目）；API 版本化策略（单机应用）。
**What already exists**：set_config 双回滚通道、login_gate 事件词表、410 断言先例、docs/danmaku_module.md 载体。
**Unresolved Decisions**：无（9 项全部落盘）。
<!-- autoplan-accepted:design -->
- 设计评审（2026-10-06 第二轮）：无 mockup 工具（无 API key）→ 文本评审；分类器 OPERATE/APP UI。声部 15 项发现全部落盘（INPUT: design 55d993873fdc3453d61951726529456cf47fbd6219a7816f3e13a619f324bb83 实测绑定 ✓——派发词 sha 前缀误抄已由声部实测勘正并记录）。
- M-1（critical）：运行期生效矩阵三格全定义——运行中添加=直写+窄端点 enabled=true 即时注入；运行中删除=先窄端点停再删配置（禁止"删了还在跑"）；运行中改关键词=直写+`POST /danmaku/keywords/reload` 窄端点热载 Bridge 内存态（tests/test_config_hotreload.py 为核实起点）——每格有 UI 反馈禁止静默。
- S-1（high）：登录态粒度=平台级（cookie 同平台多房间共享）——check_login 按平台缓存，列表引用平台级状态+tooltip；登录成功同平台徽章齐翻。
- D-1/D-2/D-3/D-4：页面规格定稿——卡片顺序房间管理在前；平台选择器分组下拉（白名单∩引擎可用，中文名+图标，常用前置）；行内三状态视觉位（主信息/登录徽章三级灰蓝绿/运行状态点）；状态中文文案表+动词统一（启动系统/开始监听）。
- M-2：TOCTOU 回退链——窄端点失败→重查 status→确认离线降级直写，在线报错重试。
- M-3：登录状态机七态（排队/启动浏览器/等待扫码/保存中/成功/失败/超时）+ 串行队列（同时仅一个登录流）+ 失败行内重试。
- M-4/M-5：空状态三步引导；异步过渡态逐步反馈；登录成功浏览器自动关闭。
- J-1：登录浏览器强制置前 + webui 同步提示 + 行内"登录"重试按钮。
- J-2：运行期互斥提示改可操作一键「停止该房间并登录」。
- J-3：登录态三级视觉灰/蓝/绿 + tooltip。
- S-2：状态文案表 + 动词统一。
- A-1：登录流程非模态（后台跑+状态 chip，配置操作照常）。
- A-2：列表头部预留批量启停控件位（本期不实现）。
<!-- /autoplan-accepted:design -->

## Design Review — Completion Summary（第二轮）

```
  +====================================================================+
  |      DESIGN PLAN REVIEW — COMPLETION SUMMARY                       |
  +====================================================================+
  | System Audit         | 无 DESIGN.md；OPERATE/APP UI；文本评审       |
  | Step 0               | 初始 6/10（数据流规格强、界面规格留白）       |
  | Pass 1 信息架构      | 6/10 → 9/10（D-1 页面规格/D-2 选择器/D-3 视觉位）|
  | Pass 2 状态          | 5/10 → 9/10（M-1 生效矩阵 critical 闭环/      |
  |                      | M-2 TOCTOU/M-3 状态机/M-4 空态/M-5 过渡态）   |
  | Pass 3 旅程          | 6/10 → 9/10（J-1 置前/J-2 一键停止登录/       |
  |                      | J-3 三级视觉）                               |
  | Pass 4 AI slop       | 8/10 → 9/10（0 hard rejection）              |
  | Pass 5 设计系统      | 9/10 → 10/10（var(--) 语义引用延续）          |
  | Pass 6 响应式        | 8/10 → 8/10（桌面单目标）                     |
  | Pass 7 决策          | 15 resolved, 0 deferred                       |
  +--------------------------------------------------------------------+
  | Overall design score | 5/10 → 9/10                                 |
  | Outside voice        | codex — unavailable（同因）                  |
  +====================================================================+
```

<!-- autoplan-accepted:ceo -->
- 评审模式：SELECTIVE EXPANSION（autoplan 固定裁决）。范围裁决：S1 上游同步、S2 配置面直写、S3 登录服务、S4 运行面收缩全部接受（用户 4 项实测需求 + 上游同步，blast radius 内）；多房间对话链、运行期会话失效重登维持 NOT in scope。
- F1（critical，P2-1/P3-4）：运行中房间开关语义裁定为"窄端点分流"——服务运行中走 `POST /danmaku/rooms/{platform}/{room_id}/enabled`（config-first：写配置+联动启停即时生效）；服务离线直写 config（下次启动生效）；UI 按 running 状态自动分流；新增验收"运行中切换开关→即时启停+config 同步"。
- F3（P3-1）：Cookie 格式对照矩阵（login_gate cookie 名表 × run_login_flow 落盘格式，逐平台）落盘 docs/danmaku_module.md 或 fixture；手动验证覆盖两种落盘格式各一例（B站 storage_state + 斗鱼 cookies.json）。
- F4（P3-4 前置）：枚举 main.py 运行期 config.json 全部写路径；存在运行中写则收敛读-改-写加文件级重试。
- F5（P2-1）：写前校验函数（重复房间/平台枚举合法/非空）+ 单测，归 webui 写前。
- F6（P2-3）：platforms 节语义裁决为"可添加平台白名单"（仅约束添加枚举，不再承载运行启停）；grep 审计全部消费者落 Review record。
- F7：登录态真相源契约——运行期引擎事件为唯一真相源，配置期判定仅首登引导；UI 文案区分"未登录过"与"登录待运行期确认"。
- F8/F11（P1-1）：同步前上游提交或 stash patch 存档；COPY_MANIFEST 记录上游基线 commit；登记副本同步成本复查点。
- F9（P3-4）：退役端点统一 410 Gone + 一致性测试断言防复活；旧测试同步删除/迁移。
- F2（P3-2）：`onboard_room(platform, room)` 编排函数单入口（添加→检测→弹窗→刷新），为平台接入向导留接缝。
- F10（P3-1）：profile 并发方案对比（时序分离 vs 每进程独立 user-data-dir+cookie 同步）——设计评审裁决；维持时序分离则"运行期重登"登记 TODOS。
- 声部派发偏差：Claude 声部以等价措辞派发（journal 含历史失败 init 记录致守卫死锁，INPUT: ceo 50f2c160 校验通过）；Codex 外部声部 unavailable——gpt-6-astra 模型元数据缺失（InvalidParameter，与第一轮同因；**修复需用户将 ~/.codex/config.toml 的 model 改为 Codex CLI 支持的型号**）。
<!-- /autoplan-accepted:ceo -->

## CEO Review — Completion Summary

```
  +====================================================================+
  |         CEO PLAN REVIEW — COMPLETION SUMMARY（第二轮）              |
  +====================================================================+
  | Mode selected        | SELECTIVE EXPANSION                         |
  | System Audit         | 4 项用户实测问题 + 上游未提交 login_gate 优化 |
  | Step 0               | 前提全部为实测证据（用户已启动系统验证）      |
  | 声部发现             | 11 项（F1 critical / F3-F5 high / F6-F9 med / |
  |                      | F2/F10/F11 债务与接缝）——全部落盘修复         |
  | NOT in scope         | 4 项（P3 退役/S7/会话失效重登/手机端）        |
  | What already exists  | set_config 通道/login_gate/弹窗 UI/cookie_dir |
  | Dream state delta    | 配置期完整闭环→开播零配置摩擦→向导化接缝      |
  | Outside voice        | codex — unavailable（同因）                   |
  | Lake Score           | 3/3（F1 三选一取窄端点分流最完整方案）        |
  | Unresolved decisions | 0（全部裁定落盘）                            |
  +====================================================================+
```

**CEO DUAL VOICES — CONSENSUS TABLE**（Codex N/A，单声部不标 CONFIRMED）：

| Dimension | Claude | Codex | Consensus |
|---|---|---|---|
| 1. Premises valid? | 是（4 项实测证据；F4 指出"既有双写者基线"前提因本计划过期——已补核查任务） | N/A | N/A |
| 2. Right problem? | 是（F2：四件事应收敛为向导接缝——已落 onboard_room） | N/A | N/A |
| 3. Scope calibration? | F1 critical：开关语义悬空=问题 3 隐藏回归——窄端点分流修复 | N/A | N/A |
| 4. Alternatives explored? | F10：profile 分离方案补对比裁决 | N/A | N/A |
| 5. Competitive risks? | F8/F11：上游分叉与同步锚点 | N/A | N/A |
| 6. 6-month trajectory? | F7 登录真相源契约 + F9 端点合同化 | N/A | N/A |
