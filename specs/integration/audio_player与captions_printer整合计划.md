<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-AUTOlive\\main-autoplan-restore-20260925-000531.md" -->
# AUTOlive × audio_player / captions_printer 功能整合计划

> 日期：2026-09-24 ｜ 发起：用户 /autoplan 指令
> 前置事实：三代码库实地摸底（AUTOlive 直读 + audio_player/captions_printer 全量通读，2026-09-24）
> 参照：`specs/integration/EDTalk功能集成计划.md`（同类整合范本）

## Implementation plan

## 1. 用户原始需求（源输入）

将 `D:\AI\audio_player`（音频播放：插队、暂停、续播等）与 `D:\AI\captions_printer`（web 字幕显示）两个独立项目整合为系统内置功能：

- **audio_player**：核心逻辑直接整合到系统 audio 播放流程，作为系统默认功能直接使用和设置，不再通过 API 请求外部服务。功能设置显示在 **前端基础功能-音频播放**。
- **captions_printer**：集成为系统模块，直接使用和设置，不再通过 API 请求外部服务。设置显示在 **前端基础功能-web字幕打印机**。
- **整合目标**：功能完整融入系统模块，前端进行设置，随系统启动即可使用。

## 2. 现状核实（实地核查 2026-09-24）

| 事实 | 位置 | 影响 |
|---|---|---|
| 播放三分支：EDTalk 推送 / 外部 audio_player HTTP / pygame，由 `play_audio.player` 决定 | utils/audio/playback_manager.py:126-157、250-267 | 内置播放器作为分支值并入，分支结构不动 |
| 外部客户端 `AUDIO_PLAYER` 暴露 6 方法：play/pause_stream/resume_stream/skip_current_stream/get_list/clear | utils/audio_handle/audio_player.py:8-141 | 内置播放器以**同名方法鸭子类型**接入，调用点零改动 |
| `play_audio.player` 现有选项 pygame / audio_player_v2 / audio_player | config.json:62；frontend/ui/tabs/audio_play.py:44 | 新增 `builtin` 选项；旧外部模式保留兼容 |
| **web字幕打印机推送链路当前为断链**：调用 `self.common.send_to_web_captions_printer(...)` 但该方法在全仓库无定义，enable=true 即 AttributeError | playback_manager.py:74-75（调用点）| 整合即修复（改为进程内直调） |
| 字幕推送点位于播放前（dequeue 后、播放分支前），所有模式共用 | playback_manager.py:73-75 | 字幕推送统一走此钩子，EDTalk 模式下字幕照常（**补齐 EDTalk 模式无字幕的缺口**） |
| audio_player 服务端播放时把 content 推给 captions_printer（外部联动） | D:\AI\audio_player\utils\audio_play_center.py:151-154 + common.py:115-148 | 内置后**删除服务端联动**，字幕只走 AUTOlive 播放前钩子一条路（旧 UI 有互斥警告：webui-bak.py:3614） |
| 核心播放器 `AUDIO_PLAY_CENTER`（361 行）：PyAudio 流式播放 + pydub 解码 + 队列 + priority_mapping 插队 + pause/resume/skip/clear + cache | D:\AI\audio_player\utils\audio_play_center.py:16-331 | 移植主体；已知缺陷 4 处须修复（见 §4.3） |
| **"插队"只重排待播队列，不打断当前播放；无"打断当前→插播→恢复"机制** | audio_play_center.py:217-283（data_priority_insert）；打断仅手动 skip_current_stream:318-331 | 用户口述"暂停插队续播"按"暂停/插队/续播"三项既有能力解读；打断恢复机制不在本期（NOT in scope 登记） |
| `MessageQueueManager`：字幕队列 + 动态延时防叠字 + 串行节流 | D:\AI\captions_printer\app.py:37-88 | 移植主体（后端节流核心） |
| 字幕渲染：show1 渐显 / show2 打字机 / hideSubtitle 渐隐 + `\n` 解码 | D:\AI\captions_printer\js\index.js:238-376、240-244 | 前端移植物；show_mode 由前端 radio DOM 决定不同步（移植时改为 config 决定） |
| `start_delay`/`keep_time` 参数被现有代码忽略（只读 content）；无清屏 API | captions_printer app.py:177-182 | 内置后 push 接口按音频时长计算 keep_time（见 §5.3） |
| 桌面字幕窗口（tkinter）：双重排队瑕疵、硬编码 localhost:5502、依赖未列 requirements | captions_printer desktop_caption_window.py | 本期丢弃（NOT in scope） |
| **pyaudio / pydub 在 runtime312 已可用**（实测 2026-09-24）；ffmpeg 已是系统既有依赖（变速用） | D:\AI\EDTalk\runtime312 | **零新增依赖** |
| NiceGUI 3.16.0（webui 进程，`app` 为 FastAPI 实例，7 个自定义路由）；主系统进程另有独立内部 HTTP API（FastAPI + uvicorn，_register_routes() 已注册 /send /llm /callback） | frontend/main.py:17、816-968；web_server.py:56-126 | 字幕页与 socket.io 挂**主系统内部 HTTP API**（CaptionsManager 同进程可达）；webui 不承载推送 |
| coordination_program 两条过期外部服务条目（E://GitHub_pro//...，enable:false） | config.json:1262-1279 | 处置（见 §4.6） |
| 前端设置页已存在：audio_play.py（播放器选择+变速）、web_captions_printer.py（仅 API 地址，**缺 enable 开关**） | frontend/ui/tabs/ | 设置页原地扩展，导航无需新增 |
| config 现状：`audio_player={api_ip_port}`；`web_captions_printer={enable, api_ip_port}` | config.json:65-67、707-710 | 配置节改造见 §4.5 / §5.4 |
| 新 UI 样式必须走设计令牌（design_tokens.py），3 列网格 + width:100% 样板 | docs/designs/frontend-beautification-plan.md；记忆 frontend-design-tokens | 前端改动硬约束 |
| **双进程拓扑**：webui 与主系统是两个进程——webui 内 Audio(type=2) 提前返回（audio_core.py:90-93），完整播放循环属主系统进程（my_handle.py:142 Audio(type=1)），其自带内部 HTTP API（utils/web_server.py FastAPI + uvicorn；frontend/main.py:902-904 经 api_ip/api_port 转发 /send） | web_server.py:56、162；my_handle.py:142 | 播放器/字幕管理器随主系统进程；字幕页挂 web_server 内部 API（非 webui） |

## 3. 整合架构总览

```
                        AUTOlive webui 进程（NiceGUI 3.16 / FastAPI）
┌─────────────────────────────────────────────────────────────────────────┐
│ Audio 类（utils/audio/，Mixin 组合，随系统运行启动）                        │
│  message_queue → synthesis(TTS) → voice_tmp_path_queue                   │
│        ↓ playback_manager.only_play_audio()（dequeue）                   │
│  [字幕推送钩子 :74] → CaptionsManager.push(data_json)  ← 进程内直调（修断链）│
│        ↓ 播放分支                                                        │
│  ├─ is_edtalk_active → edtalk_client.push_full（仅推送，不本地播放）        │
│  ├─ player == builtin → AUDIO_PLAY_CENTER（内置，进程内队列播放）           │
│  ├─ player == audio_player(_v2) → AUDIO_PLAYER HTTP 客户端（兼容保留）      │
│  └─ else → pygame mixer                                                 │
│                                                                          │
│ CaptionsManager（utils/web_captions/）：队列节流 → socket.io 广播           │
│ 字幕页 /captions（主系统进程内部 HTTP API 挂载）← OBS 浏览器源              │
│ 前端设置页：audio_play.py / web_captions_printer.py（基础功能分组，已存在）   │
└─────────────────────────────────────────────────────────────────────────┘

外部项目 D:\AI\audio_player / D:\AI\captions_printer：解除依赖，目录原样保留（不删除）
```

整合形态：**进程内模块**（不是内嵌子服务、不是子进程）。播放器与字幕管理器随**主系统进程**启动并随其启停（webui 与主系统是两个进程：webui 内 Audio(type=2) 于 audio_core.py:90-93 提前返回，完整播放循环在主系统进程 my_handle.py:142）；字幕显示页挂主系统进程内部 HTTP API（§5.2），通过 WebSocket 接收推送（该页是"屏幕输出"，不是 API 调用）。

## 4. 模块一：内置音频播放器

> **方案取舍记录（native F5）**：曾考虑"基于 pygame mixer 的队列层"替代（可消除 R1 的 PyAudio 设备风险域），否决理由：①插队/暂停续播语义保真——源项目 callback 流式架构原生支持暂停点续播；②源项目实测稳定；③pygame mixer 无流级暂停位置追踪，需自建状态机，复杂度反而更高。6 个月后回看此记录即知为何背上 PyAudio 设备管理。

### 4.1 代码移植清单

| 源（D:\AI\audio_player\） | 目标（D:\AI\AUTOlive\） | 处置 |
|---|---|---|
| utils/audio_play_center.py（AUDIO_PLAY_CENTER 类） | utils/audio/builtin_play_center.py | 移植 + §4.3 缺陷修复 + §4.2 接口适配 |
| utils/common.py 的 download_audio / search_audio_file / copy_audio_file / get_all_audio_device_info / get_new_audio_path | utils/audio/builtin_play_center.py 内并入（仅保留实际用到的） | 按需精简并入，不建独立 Common |
| config.json 的 priority_mapping / device_index / audio_interval / random_audio_interval 语义 | config.json `audio_player` 节（§4.5） | 配置语义并入 |
| app.py Flask 壳、index.html/js/css 控制台、bat、logger、tests | 不移植 | 壳丢弃；logger 用 utils/my_log |

### 4.2 接口适配（调用点零改动）

- 内置播放器暴露与 `AUDIO_PLAYER` 客户端**相同的 6 方法签名**：`play(data)`（→ add_audio_json）、`pause_stream()`、`resume_stream()`、`skip_current_stream()`、`get_list()`、`clear()`（→ clear_audio_json）。
- **get_list UI 扩展（design D5/D6）**：get_list 为前端控制区返回轻量摘要 `{queue_len, playing, paused, current:{type, content截断20字}}`——内置播放器自有读取口，仅 UI 消费，不触碰 playback_manager 鸭子类型调用点；为控制区状态回显（播放中/已暂停/空闲）与「正在播放」感知提供数据源。
- audio_core.py:118 按 `play_audio.player` 分流实例化：`builtin` → 内置 `AUDIO_PLAY_CENTER`，其余 → 既有 HTTP 客户端。playback_manager.py 两处 `self.audio_player.play(data_json)`（156、266）零逻辑改动。
- **stop_current_audio 条件修复（spec C-1）**：playback_manager.py:201 现条件 `== "audio_player"`（不含 v2/builtin；且 mixer_normal 类属性仅 pygame 分支赋值，builtin 模式下弹幕打断将命中 None.music → AttributeError 或打断静默失效）——该行条件改为 `in ["audio_player", "audio_player_v2", "builtin"]`（一行改动，顺带修复 v2 既有缺陷；此为"调用点零改动"承诺的唯一声明例外）。builtin 停止语义调用目标 = skip_current_stream()（6 方法中唯一"停当前"语义；mixer_normal 在 builtin 下为 None 不可用）——实施时以单测闭合：打断后无 AttributeError、下一播放正常（native F6）。
- 分支条件扩展：`elif self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2", "builtin"]`（130、254 两处）。
- 播放器级变速**不移植**（变速已由 playback_manager 层在 TTS 产物上完成——audio_random_speed 配置 + pydub speedup 不变调；播放器级改采样率变速会变调，丢弃）；`speed`/`random_speed` 字段固定 1/False（与现 playback_manager 构造值一致）。

### 4.3 移植时修复的已知缺陷（源码实证）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 播放等待为 `while ... : pass` 自旋烧 CPU（audio_play_center.py:158-159） | 改 threading.Event 等待（pyaudio callback 完成事件） |
| 2 | `./out/tmp_*.wav` 解码临时文件只增不清（:111-115） | 播放完成后删除临时文件；启动时清理遗留 |
| 3 | AttributeError 兜底清空**整个队列**（:174-180） | 单条失败跳过 + 日志，不动队列余项 |
| 4 | `set_device_index()` 写的字段播放时不读（:209-210），运行时切设备无效 | 播放时读取当前配置 device_index |

### 4.4 播放完成回调

> **队列有界（S7）**：源播放器与字幕队列均无上限。对齐 EDTalk 计划"链路队列全有界+阻塞式背压"既定义务：内置播放器待播队列上限 50（满载 add_audio_json 阻塞等待，上游 voice_tmp_path_queue 已有界）、字幕队列上限 100（满载丢弃最旧并日志告警——字幕可丢，台词不可丢）。（对齐 info_to_callback）

外部 audio_player 模式下，播放完成由外部服务 POST /callback 回传（audio_play_center.py:40-59）；内置后改为进程内回调：`AUDIO_PLAY_CENTER` 支持注册完成回调，audio_core 初始化时注册 `send_audio_play_info_to_callback`（注意其为 async 协程——播放器在独立线程，经 `asyncio.run_coroutine_threadsafe` 提交到主 loop，或以同步 wrapper 封装；实施时按 Audio 类既有线程/协程模型选型，eng 审查把关）。`play_audio.info_to_callback` 开关语义不变。

### 4.5 配置改造（config.json `audio_player` 节）

```json
"audio_player": {
  "device_index": -1,                    // -1 = 系统默认设备；设置页下拉枚举
  "audio_interval": 0,                   // 播完后固定间隔（秒）
  "random_audio_interval": {"enable": false, "min": 0, "max": 0},
  "priority_mapping": { ... },           // type→优先级数值，默认表按 AUTOlive 实际 type 词表重写（见 R5）
  "api_ip_port": "http://127.0.0.1:5602" // 仅旧外部模式使用（audio_player/audio_player_v2）
}
```

- `play_audio.player` 默认值**保持 `pygame` 不变**（既有用户行为零变化）；`builtin` 为新增选项，用户在设置页选择。
- priority_mapping 默认值必须与 AUTOlive data_json.type 实际词表对齐（comment / reread / schedule / entrance / gift / follow / copywriting / idle_time_task / local_qa_audio 等，实施首日 grep 词表核对，见 R5）。

### 4.6 启动/停止接线与旧条目处置

- 内置播放器随 Audio 类初始化启动（daemon 线程），随**主系统进程**存活（Audio 完整实例化仅发生在主系统进程：my_handle.py:142；webui 内 Audio(type=2) 提前返回不建播放器）——与 pygame 模式线程模型一致，无新增进程管理负担。
- 系统停止链路挂接：停止运行时对内置播放器执行 clear + 停流（对齐 `stop_audio`/mixer stop 语义；实施时定位既有停止调用点接入）。
- coordination_program 两条过期外部服务条目（config.json:1262-1279，路径 E://GitHub_pro// 已不存在）：**删除**，并在设置页 coordination 相关说明无残留引用（实施时 grep 验证）。

## 5. 模块二：内置 web 字幕打印机

### 5.1 代码移植清单

| 源（D:\AI\captions_printer\） | 目标（D:\AI\AUTOlive\） | 处置 |
|---|---|---|
| app.py 的 MessageQueueManager（:37-88）+ CHARACTER_DELAY/DEFAULT_START_DELAY | utils/web_captions/captions_manager.py（类 CaptionsManager） | 原样移植，emit 通道改造为 socket.io 广播 |
| js/index.js 的 showSubtitle/show1/show2/hideSubtitle/clearSubtitle（:238-376）+ `\n` 解码（:240-244） | frontend/web_captions/index.js | 移植渲染逻辑，去配置控制台代码；show_mode 改由 config 决定 |
| index.html:63-65 字幕显示区 DOM | frontend/web_captions/index.html | 仅保留字幕区（去控制台表单） |
| css/index.css:5-19 #subtitle/#subtitle_bg | frontend/web_captions/index.css | 子集移植（字幕页是独立页面，不走 design_tokens——该页面向 OBS 浏览器源，样式由用户配置驱动） |
| js/socket.io.js（本地打包客户端） | frontend/web_captions/socket.io.js | 原样复制（避免 CDN 依赖） |
| config.json 14 样式键 | config.json `web_captions_printer` 节（§5.4） | schema 并入 |
| app.py Flask 壳、配置控制台 UI、desktop_caption_window.py、utils/common+logger、bat | 不移植 | 壳丢弃；配置 UI 迁入设置页（§6.2） |

### 5.2 服务挂载（主系统进程内部 HTTP API，utils/web_server.py）

> **渲染安全（S3）**：源项目 showSubtitle 用 innerHTML 渲染（支持 <br> 换行）——LLM 生成内容直接进 DOM = XSS 向量。移植时改为：文本节点 HTML 转义 + 仅白名单转换换行符为 <br>（保留换行语义，杜绝标签注入）。

- **挂载目标（spec C-0 修正）**：字幕页与 socket.io 挂载于**主系统进程**的内部 HTTP API（utils/web_server.py 的 FastAPI app，路由在 _register_routes() 内注册——/send、/llm、/callback 同级先例）。**不挂 webui**：webui（frontend/main.py）与主系统是两个进程，CaptionsManager 推送在主系统进程内，挂 webui 跨进程不可达。
- `@app.get('/captions')` 返回字幕页（HTMLResponse 读 frontend/web_captions/index.html）；静态资源同目录服务。
- 推送通道：**独立 socket.io ASGI 挂载**（`socketio.ASGIApp(socketio.AsyncServer(async_mode="asgi"))` mount 至 `/captions_ws/`，客户端连接 `socket.io` path `/captions_ws/socket.io`）；emit 在主系统进程 event loop 内执行（与 uvicorn 同 loop）。python-socketio 已安装（实测），零新依赖。
- **并发模型（spec CL-1）**：`CaptionsManager.push(data_json)` 为异步入口，内部做**线程安全入队**（list+Lock，沿用源 MessageQueueManager 模式）；内部节流线程逐条出队，经 `asyncio.run_coroutine_threadsafe` 将 `sio.emit` 提交主系统 event loop（web_server 启动 uvicorn 时捕获 loop 引用）。config_update 广播同通道。
- **enable 语义（spec N-3）**：socket.io 挂载与 /captions 路由随主系统启动无条件创建（结构性 mount）；enable 仅控制 push 短路；开关修改后重启主系统生效。
- 事件契约沿用原项目：服务端→客户端 `message`（{content, start_delay, keep_time}）与 `config_update`；客户端→服务端无需任何事件（手动发送功能不移植）。
- **初始状态同步（design D1，发布阻断级修复）**：sio `connect` 事件回调中向新连接 emit 一次当前完整字幕配置（与 config_update 同数据源）——OBS 浏览器源每次打开/刷新/重连即恢复用户配置样式；仅靠增量广播会使样式在重开后回退默认，热更新承诺失效。

### 5.3 推送接线（修复断链 + 时长对齐）

- playback_manager.py:74-75 修复为进程内直调：`await self.captions_manager.push(data_json)`（CaptionsManager 单例随 audio_core 初始化创建；照 EDTalkClient 的 register_client 模式注册全局 holder，供非 Audio 上下文使用）。
- **异常隔离（S4）**：CaptionsManager.push 内部全捕获+日志计数，任何字幕错误不得传播进播放循环（调用点在播放主 try 块内，异常会吞掉当轮播放）；content 为空时跳过推送（不推空字幕）。
- **keep_time 对齐音频时长**（原服务端忽略 keep_time 的升级修复）：push 时从 `data_json["voice_path"]` 读取音频时长（soundfile，已安装），字幕保持至音频播完（keep_time = max(音频时长, 保底显示时长)）；读时长失败降级为原自动计算（len*CHARACTER_DELAY + DEFAULT_START_DELAY）。配置开关 `keep_time_align_audio`（默认 true）。
- 字幕推送位于播放分支之前（现状位置），**EDTalk 推送模式 / pygame / builtin 全模式共用**——内置字幕打印机补齐 EDTalk 模式字幕能力（原模式无任何字幕）。
- enable=false 时零开销（不创建 socket 广播、不进队列——CaptionsManager.push 入口短路）。

### 5.4 配置改造（config.json `web_captions_printer` 节）

```json
"web_captions_printer": {
  "enable": false,
  "keep_time_align_audio": true,
  "show_mode": "1",                     // "1" 渐显 / "2" 打字机（原 14 键 schema 照搬）
  "single_char_show_time": 80,
  "gradient_show_time": 500,
  "hide_time": 1000,
  "show_over_hide_time": 2000,
  "bg_color": "#000000",
  "body_bg_color": "#FFFFFF",
  "font_color": "#FFFFFF",
  "subtitle_font_family": "Microsoft YaHei",
  "subtitle_font_size": 24,
  "subtitle_font_weight": "bold",
  "subtitle_webkit_text_stroke": 0,
  "subtitle_bg_width": 400,
  "subtitle_bg_height": 60
}
```

- `api_ip_port` 键**删除**（内置后无外部地址；migrate 时移除，设置页去输入框）。
- 样式保存（设置页自动保存既有机制）→ 落盘 config.json + `config_update` 广播 → 字幕页即时热更新。
- **透明语义（design D10，必修）**：body_bg_color 渲染层支持空值/"none" → `background: transparent`（OBS 浏览器源叠加场景必须可表达透明；hex 14 键 schema 无法表达 rgba——映射方案优先，不扩 schema）；字幕页地址回显旁加一行「在 OBS 中添加浏览器源粘贴此地址」。
- **默认背景色（D10③，taste 留 Phase 4）**：源默认 #FFFFFF 在 OBS 叠加场景=整块白色挡画面；默认改透明 vs 保留白底（存量习惯）留批准门裁决。
- 字幕页地址 = `http://<api_ip>:<api_port>/captions`（随主系统内部 HTTP API 配置走，无独立端口；api_ip 为 0.0.0.0 时地址回显按既有先例转 127.0.0.1——frontend/main.py:902 同款转换，spec CL-2）。

## 6. 模块三：前端设置页改造（基础功能分组，页已存在）

### 6.1 音频播放页（frontend/ui/tabs/audio_play.py）

1. 播放器 select 增加选项 `builtin`（label"内置播放器"）；tooltip 更新：pygame=系统自带默认 / 内置播放器=进程内队列播放（插队/暂停/续播） / audio_player(_v2)=外部服务模式（需自行启动外部项目；兼容保留，builtin 稳定一个版本后移除——sunset 判据登记 TODOS，native F4）。
2. `audio_player` 卡片改造（**播放器选择修改后重启主系统生效——与字幕 enable 同口径，选项旁标注（design D11）；实施首日验证可否热切，可热切再升级提示**）：
   - "内置播放器"卡：设备下拉（get_all_audio_device_info 异步枚举 + "系统默认"项，device_index；**loading 态「正在枚举设备…」、空列表态「未检测到音频设备，将使用系统默认」且下拉禁用为默认项——design D8**）、播放间隔、随机间隔（开关+上下限）、优先级映射（只读）折叠区：默认收起，展开显示 type→优先级数值表 + 一行「未识别类型排队尾」；v1 无编辑入口（design D4）。
   - "外部服务 API 地址"输入框：选 builtin/pygame 时**隐藏**（动态显隐），选 audio_player(_v2) 时显示（保留 is_url_check 校验）。
3. 互斥提示（对齐 EDTalk 计划四组合提示模式）：`is_edtalk_active` 为真时页面顶部常驻提示"数字人模式下音频由 EDTalk 播放"，**同时播放器 select 置 disabled（视觉锁死）——「可改但无效」是认知失调路径，「禁用+说明」更诚实（design D3）**。（来源：计划初稿既有条目，非 0G cherry-pick）
4. 小型控制区（卡片常驻）：选 builtin 时显示队列条数回显（get_list）与暂停/续播/跳过当前/清空队列四按钮——运营者手动打断用；未选 builtin 时卡片显示空态文案"内置播放器未启用"。tooltip 注明语义边界："插队仅重排待播顺序，不打断当前播放"（native F1 预期管理）。
   - **状态回显行（design D5/D6）**：按钮区上方常驻一行状态：「播放中：[type] 内容…（截断20字）/ 已暂停 / 空闲（队列 N 条）」；暂停时"暂停"按钮禁用、"续播"启用（数据源=get_list UI 扩展）。
   - **刷新机制（design D7）**：ui.timer 1s 轮询，仅当选 builtin 且页面可见时激活；get_list 返回轻量摘要不持锁全量拷贝（防闪烁与锁竞争）。
   - **错误条（design D9）**：控制区顶部显示最近一次播放器错误（可关闭）——R1 设备失败附设备列表与 device_index 指引、R8 ffmpeg 报错在此浮出；直播中运营者不看日志文件，播放静默失败必须 UI 可见。日志仍全量。
5. 全部新 UI 走 design_tokens 令牌 + 3 列网格 + width:100% 样板（硬约束）。

### 6.2 web字幕打印机页（frontend/ui/tabs/web_captions_printer.py）

1. 补 `enable` 开关（现状 UI 缺失，仅有 API 地址——断链三重奏之一）；**开关旁三步微指引（design D12）**：「①开启并保存 → ②重启主系统生效 → ③复制下方地址到 OBS 浏览器源」。
2. 移除 API 地址输入框；新增**字幕页地址回显**（只读 + "复制地址"按钮，供 OBS 浏览器源粘贴；地址 = http://<api_ip>:<api_port>/captions，api_ip 为 0.0.0.0 时回显 127.0.0.1）。
3. 新增样式配置卡（14 键，**卡内三组信息架构——design D2**）：①平铺区=显示模式 select（渐显/打字机）+字体/字号/字重/描边+文字色/背景色/页面背景色（色盘）；②次级区=字幕区宽高；③**高级折叠区（默认收起）**=渐显时长/单字时长/隐藏时长/播完保留时长 4 个渲染时序微调键，tooltip 注明单位 ms 与"误改破坏字幕节奏"风险。
4. `keep_time_align_audio` 开关（"字幕时长对齐音频"）。
5. 热更新语义标注：样式修改保存后字幕页即时生效（config_update 广播），无需刷新 OBS 源。

## 7. NOT in scope（本期不做）

| 项 | 理由 |
|---|---|
| 桌面字幕窗口（desktop_caption_window.py，tkinter） | 双重排队瑕疵 + 硬编码 localhost:5502 + 依赖未列 requirements；浏览器页 + OBS 浏览器源可覆盖同等场景。如需桌面悬浮窗后续单独评估 |
| "打断当前→插播→自动恢复原音频"机制 | 源项目无此实现（实证）；用户口述按既有三项能力（暂停/插队/续播）解读。如需打断恢复登记 TODOS（callback 闭包架构可扩展：暂停当前流→记读位→播插队项→恢复） |
| 音量控制 | 源项目唯一尝试无效（代码注释自证"没有效果"）；pyaudio 流级音量=改采样数据，属新开发，登记 TODOS |
| 内置播放器级变速 | 播放器级改采样率变速会变调；系统级变速已由 playback_manager 层（pydub speedup，不变调）覆盖 |
| 外部模式移除（audio_player/audio_player_v2 选项与 HTTP 客户端） | 保留向后兼容，零维护成本；tooltip 标注"外部服务模式" |
| D:\AI\audio_player / D:\AI\captions_printer 目录清理归档 | 只解除系统依赖；目录处置（删除/归档）由用户另行决定 |
| 手动发送字幕（原页面输入框） | 控制台功能，直播场景无使用路径；需手动推字幕时可用 /send 等既有接口扩展 |
| 字幕多行队列/滚动显示 | 源项目为覆盖式单条显示，语义保持 |

## 8. 风险与待验证点

| # | 风险 | 缓解 |
|---|---|---|
| R1 | PyAudio 设备打开失败（独占/采样率不匹配的 WDM 设备） | 设备枚举下拉 + 失败回退系统默认设备 + 报错附设备列表与 device_index 配置指引 |
| R2 | 播放器线程/字幕节流线程与主系统进程 asyncio 交互（完成回调、sio.emit 线程安全） | 队列已有锁保护；回调与 emit 经 run_coroutine_threadsafe 提交主 loop；实施首日以冒烟验证（播放+回调+停止） |
| R3 | 主系统进程承载播放线程——主系统重启打断播放（webui 重启不受影响） | 与 pygame 模式现状一致（同为进程内线程），无回归；计划如实声明 |
| R4 | socket.io 独立挂载与主系统 uvicorn 上其他 WS 路径冲突 | 独立 ASGI app 挂 /captions_ws/ 前缀，path 隔离；实施首日冒烟（页面连接+推送+config_update） |
| R5 | priority_mapping 默认表与 AUTOlive type 词表不齐（源表为旧 ai_vtuber 词表） | 实施首日 grep data_json["type"] 赋值点核对词表，重写默认表；未识别 type 插队尾（源行为保底） |
| R6 | 音频时长读取失败（非 wav 产物/文件损坏） | keep_time 降级为原自动计算（len*80ms+2000ms），不阻塞播放 |
| R7 | 配置键迁移（web_captions_printer.api_ip_port 删除 + 14 键新增 + audio_player 节扩展） | 沿用既有 ensure-default/migrate 机制（EDTalk 计划确立）；缺键补默认值，防 get 返回 None |
| R8 | ffmpeg 缺失环境下 pydub 解码非 wav 失败 | ffmpeg 已是系统既有依赖（变速功能同依赖）；报错文案指向环境文档 |
| R9 | 断链修复涉及播放主循环（playback_manager）回归 | 分支结构零逻辑改动原则；回归三模式（pygame/builtin/edtalk）+ 外部模式冒烟 |
| R10 | EDTalk 模式字幕音画同步——字幕在本地 dequeue 推送，EDTalk 端开口时机由其管线决定，可能系统性超前 | 首日冒烟必测项：EDTalk 模式字幕延迟实测（native F3）；偏移明显则登记"EDTalk 回调驱动字幕"TODO |

## 9. 验收标准

1. 播放器选 builtin → 运行系统：TTS 与文案音频经内置播放器播放，队列保序、priority_mapping 插队（高优先级 type 排前）、insert_index 显式插队、暂停点续播、跳过当前、清空队列全部可用。
2. 设置页控制区：队列条数实时回显；暂停/续播/跳过/清空按钮生效。
3. 设备选择：下拉枚举声卡，切换 device_index 后新播放请求走新设备（修复"写了不读"缺陷）；设备打开失败时回退默认设备并报错附设备列表。
4. web字幕打印机：enable → `http://<api_ip>:<api_port>/captions` 页面随音频逐句显示字幕，样式 14 键修改保存后**即时热更新**；keep_time_align_audio 开启时字幕保持至音频播完。
5. EDTalk 模式：音频仅推送 EDTalk（无本地播放、无双声），字幕页照常逐句显示（补齐原缺口）；首日冒烟实测字幕与 EDTalk 开口的偏移在可接受范围（native F3，偏移明显则降级登记 TODO）。
6. 回归：pygame 模式、外部 audio_player 模式（HTTP 客户端路径）、既有启动/停止链路均不受影响；字幕断链修复后原 AttributeError 场景消失。
7. 零新增 pip 依赖（pyaudio/pydub/python-socketio runtime312 已具备，实测 2026-09-24）。
8. 配置迁移：老 config.json 升级后所有新键有默认值，web_captions_printer.enable=false 行为零变化；coordination_program 过期条目清除后启动无报错。


<!-- autoplan-accepted:ceo -->
- 用户三点原始需求全保持：①audio_player 核心逻辑整合进系统 audio 播放流程，设置在"前端基础功能-音频播放"，不再请求外部 API；②captions_printer 集成为系统模块，设置在"前端基础功能-web字幕打印机"，不再请求外部 API；③功能随系统启动即可用。
- 整合形态 = 进程内模块（非内嵌子服务/子进程）；外部项目目录解除依赖、原样保留不删除。
- 内置播放器以与 AUDIO_PLAYER 客户端相同的 6 方法签名（play/pause_stream/resume_stream/skip_current_stream/get_list/clear）鸭子类型接入；playback_manager 调用点零逻辑改动；play_audio.player 新增 builtin 选项，默认值保持 pygame 不变。
- 移植时修复源播放器 4 缺陷：自旋等待烧 CPU、tmp wav 只增不清、异常清空全队列、set_device_index 写了不读。
- web字幕打印机修复断链：playback_manager.py:74-75 改为 CaptionsManager 进程内直调（原 send_to_web_captions_printer 方法不存在）；推送点保持在播放分支前，全模式（EDTalk/pygame/builtin）共用。
- 字幕页挂载目标修正为**主系统进程内部 HTTP API**（utils/web_server.py FastAPI，_register_routes() 注册 /captions + /captions_ws/ socket.io；不挂 webui——双进程拓扑实证：webui 内 Audio(type=2) 于 audio_core.py:90-93 提前返回，完整播放循环在主系统进程 my_handle.py:142）；字幕页地址 = http://<api_ip>:<api_port>/captions（0.0.0.0 回显转 127.0.0.1，先例 frontend/main.py:902）；事件契约沿用源项目 message/config_update。
- keep_time 对齐音频时长（soundfile 读时长，失败降级自动计算；keep_time_align_audio 默认 true）。
- CaptionsManager 并发模型（spec CL-1 义务）：push 异步入口内部线程安全入队（list+Lock，沿用源 MessageQueueManager 模式），节流线程经 asyncio.run_coroutine_threadsafe 将 sio.emit 提交主系统进程 event loop（与 uvicorn 同 loop）；config_update 同通道。
- stop_current_audio 条件修复（spec C-1 义务）：playback_manager.py:201 由 `== "audio_player"` 改为 `in ["audio_player", "audio_player_v2", "builtin"]`（builtin 弹幕打断必需；顺带修复 v2 既有缺陷）——此为"调用点零改动"承诺的唯一声明例外。
- 前端两设置页原地扩展：builtin 选项+设备下拉+控制区（暂停/续播/跳过/清队+条数回显）+EDTalk 互斥提示+外部地址动态显隐；web_captions_printer 页补 enable 开关+14 键样式配置+地址回显复制+热更新。
- 桌面字幕窗口（tkinter）本期丢弃；打断恢复机制、音量控制、播放器级变速、外部模式移除均入 NOT in scope。
- 零新增 pip 依赖（pyaudio/pydub/python-socketio runtime312 实测已具备）；priority_mapping 默认表实施首日按 AUTOlive type 词表核对重写。
- coordination_program 两条过期外部服务条目删除；web_captions_printer.api_ip_port 键 migrate 移除。
- 0G cherry-pick 5 项全部接受（keep_time 对齐/队列控制区/地址复制/动态显隐/过期条目清理），均已落计划条目；§6.1.3 互斥提示标注来源为初稿既有条目（非 0G cherry-pick）。
- 用户口述"暂停插队续播"按代码实证三项能力（插队/暂停/续播）解读；"打断当前→插播→恢复"前提落差与桌面窗口丢弃列为 User Challenge，留 Phase 4 最终批准门裁决。
- Spec 审查 C-2 修正（义务）：CEO 摘要 0I 风险编号 R5/R7 错位已纠正；架构图/§4.6/R2/R3/R4/验收4 进程边界措辞同步修正。
- Spec 审查第2轮 N-1/N-2/N-3 修正（义务）：§4.5 两处风险引用 R7→R5；§6.1.4 控制区改为卡片常驻+按 builtin 显隐内容；§5.2 补 enable 语义（挂载随主系统启动无条件创建，enable 仅控制 push 短路，开关修改重启生效）。
- native CEO 声部义务（2026-09-25，放行附三条件）：F1 打断恢复升级为 Phase 4 必须裁决项（两 demo 语义：纯队列插队 vs 打断恢复），控制区 tooltip 标注"插队不打断当前播放"；F3 EDTalk 字幕同步实测入首日冒烟（新增 R10）+ 验收5 补同步性；F6 builtin 停止语义闭合 skip_current_stream + 单测；F4 外部模式 tooltip sunset 措辞 + TODOS 登记移除判据；F5 §4 头补 pygame-mixer 替代方案取舍记录；F2 默认值张力（存量 pygame/新配置 builtin 或一次性引导）留 Phase 4 taste；F7/F8 记录无动作。
<!-- /autoplan-accepted:ceo -->

## Review record

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"0be1269a0ec2bc5e2f93d3c10ccddbe59f708b5c69f99717d8e48e68868f2633","replacements":[{"oldText":"整合形态：**进程内模块**（不是内嵌子服务、不是子进程）。播放器与字幕管理器作为 Audio/系统对象的组件随系统启停；前端仅剩一个字幕显示页通过 WebSocket 接收推送（该页是\"屏幕输出\"，不是 API 调用）。","newText":"整合形态：**进程内模块**（不是内嵌子服务、不是子进程）。播放器与字幕管理器随**主系统进程**启动并随其启停（webui 与主系统是两个进程：webui 内 Audio(type=2) 于 audio_core.py:90-93 提前返回，完整播放循环在主系统进程 my_handle.py:142）；字幕显示页挂主系统进程内部 HTTP API（§5.2），通过 WebSocket 接收推送（该页是\"屏幕输出\"，不是 API 调用）。"},{"oldText":"| NiceGUI 3.16.0，`app` 为 FastAPI 实例，已有 7 个自定义路由；内部 socket.io 挂载于 `/_nicegui_ws/`（nicegui/nicegui.py:52-54） | frontend/main.py:17、816-968 | 字幕页挂主服务：自有路由 + 独立 socket.io 挂载（不复用内部 sio，防版本耦合） |","newText":"| NiceGUI 3.16.0（webui 进程，`app` 为 FastAPI 实例，7 个自定义路由）；主系统进程另有独立内部 HTTP API（FastAPI + uvicorn，_register_routes() 已注册 /send /llm /callback） | frontend/main.py:17、816-968；web_server.py:56-126 | 字幕页与 socket.io 挂**主系统内部 HTTP API**（CaptionsManager 同进程可达）；webui 不承载推送 |"},{"oldText":"| 新 UI 样式必须走设计令牌（design_tokens.py），3 列网格 + width:100% 样板 | docs/designs/frontend-beautification-plan.md；记忆 frontend-design-tokens | 前端改动硬约束 |","newText":"| 新 UI 样式必须走设计令牌（design_tokens.py），3 列网格 + width:100% 样板 | docs/designs/frontend-beautification-plan.md；记忆 frontend-design-tokens | 前端改动硬约束 |\n| **双进程拓扑**：webui 与主系统是两个进程——webui 内 Audio(type=2) 提前返回（audio_core.py:90-93），完整播放循环属主系统进程（my_handle.py:142 Audio(type=1)），其自带内部 HTTP API（utils/web_server.py FastAPI + uvicorn；frontend/main.py:902-904 经 api_ip/api_port 转发 /send） | web_server.py:56、162；my_handle.py:142 | 播放器/字幕管理器随主系统进程；字幕页挂 web_server 内部 API（非 webui） |"},{"oldText":"│ 字幕页 /captions（frontend/web_captions/ 静态页）← OBS 浏览器源             │","newText":"│ 字幕页 /captions（主系统进程内部 HTTP API 挂载）← OBS 浏览器源              │"},{"oldText":"- audio_core.py:118 按 `play_audio.player` 分流实例化：`builtin` → 内置 `AUDIO_PLAY_CENTER`，其余 → 既有 HTTP 客户端。playback_manager.py 两处 `self.audio_player.play(data_json)`（156、266）与 `stop_current_audio()`（201-202）**零逻辑改动**。","newText":"- audio_core.py:118 按 `play_audio.player` 分流实例化：`builtin` → 内置 `AUDIO_PLAY_CENTER`，其余 → 既有 HTTP 客户端。playback_manager.py 两处 `self.audio_player.play(data_json)`（156、266）零逻辑改动。\n- **stop_current_audio 条件修复（spec C-1）**：playback_manager.py:201 现条件 `== \"audio_player\"`（不含 v2/builtin；且 mixer_normal 类属性仅 pygame 分支赋值，builtin 模式下弹幕打断将命中 None.music → AttributeError 或打断静默失效）——该行条件改为 `in [\"audio_player\", \"audio_player_v2\", \"builtin\"]`（一行改动，顺带修复 v2 既有缺陷；此为\"调用点零改动\"承诺的唯一声明例外）。builtin 停止语义调用目标 = skip_current_stream()（6 方法中唯一\"停当前\"语义；mixer_normal 在 builtin 下为 None 不可用）——实施时以单测闭合：打断后无 AttributeError、下一播放正常（native F6）。"},{"oldText":"- 内置播放器随 Audio 类初始化启动（daemon 线程），随 webui 进程存活——与 pygame 模式线程模型一致，无新增进程管理负担。","newText":"- 内置播放器随 Audio 类初始化启动（daemon 线程），随**主系统进程**存活（Audio 完整实例化仅发生在主系统进程：my_handle.py:142；webui 内 Audio(type=2) 提前返回不建播放器）——与 pygame 模式线程模型一致，无新增进程管理负担。"},{"oldText":"### 5.2 服务挂载（frontend/main.py）\n\n- `@app.get('/captions')` 返回字幕页（HTMLResponse 读 frontend/web_captions/index.html）；静态资源同目录服务。\n- 推送通道：**独立 socket.io ASGI 挂载**（`socketio.ASGIApp(socketio.AsyncServer(async_mode=\"asgi\"))` mount 至 `/captions_ws/`，客户端连接 `socket.io` path `/captions_ws/socket.io`）——不复用 NiceGUI 内部 sio（`/_nicegui_ws/` 为框架内部实现，避免版本升级耦合）；python-socketio 已安装（实测），零新依赖。","newText":"### 5.2 服务挂载（主系统进程内部 HTTP API，utils/web_server.py）\n\n- **挂载目标（spec C-0 修正）**：字幕页与 socket.io 挂载于**主系统进程**的内部 HTTP API（utils/web_server.py 的 FastAPI app，路由在 _register_routes() 内注册——/send、/llm、/callback 同级先例）。**不挂 webui**：webui（frontend/main.py）与主系统是两个进程，CaptionsManager 推送在主系统进程内，挂 webui 跨进程不可达。\n- `@app.get('/captions')` 返回字幕页（HTMLResponse 读 frontend/web_captions/index.html）；静态资源同目录服务。\n- 推送通道：**独立 socket.io ASGI 挂载**（`socketio.ASGIApp(socketio.AsyncServer(async_mode=\"asgi\"))` mount 至 `/captions_ws/`，客户端连接 `socket.io` path `/captions_ws/socket.io`）；emit 在主系统进程 event loop 内执行（与 uvicorn 同 loop）。python-socketio 已安装（实测），零新依赖。\n- **并发模型（spec CL-1）**：`CaptionsManager.push(data_json)` 为异步入口，内部做**线程安全入队**（list+Lock，沿用源 MessageQueueManager 模式）；内部节流线程逐条出队，经 `asyncio.run_coroutine_threadsafe` 将 `sio.emit` 提交主系统 event loop（web_server 启动 uvicorn 时捕获 loop 引用）。config_update 广播同通道。\n- **enable 语义（spec N-3）**：socket.io 挂载与 /captions 路由随主系统启动无条件创建（结构性 mount）；enable 仅控制 push 短路；开关修改后重启主系统生效。"},{"oldText":"- 字幕页地址 = `http://<webui_ip>:<webui_port>/captions`（随 webui 配置走，无独立端口）。","newText":"- 字幕页地址 = `http://<api_ip>:<api_port>/captions`（随主系统内部 HTTP API 配置走，无独立端口；api_ip 为 0.0.0.0 时地址回显按既有先例转 127.0.0.1——frontend/main.py:902 同款转换，spec CL-2）。"},{"oldText":"| R2 | 播放器线程与 webui asyncio 交互（完成回调、线程安全） | 队列已有锁保护；回调经 run_coroutine_threadsafe 或同步 wrapper；实施首日以冒烟验证（播放+回调+停止） |","newText":"| R2 | 播放器线程/字幕节流线程与主系统进程 asyncio 交互（完成回调、sio.emit 线程安全） | 队列已有锁保护；回调与 emit 经 run_coroutine_threadsafe 提交主 loop；实施首日以冒烟验证（播放+回调+停止） |"},{"oldText":"| R3 | webui 进程承载播放线程——webui 重启打断播放 | 与 pygame 模式现状一致（同为进程内线程），无回归；计划如实声明 |","newText":"| R3 | 主系统进程承载播放线程——主系统重启打断播放（webui 重启不受影响） | 与 pygame 模式现状一致（同为进程内线程），无回归；计划如实声明 |"},{"oldText":"| R4 | socket.io 独立挂载与 NiceGUI 内部 /_nicegui_ws 冲突 | 独立 ASGI app 挂 /captions_ws/ 前缀，path 隔离；实施首日冒烟（页面连接+推送+config_update） |","newText":"| R4 | socket.io 独立挂载与主系统 uvicorn 上其他 WS 路径冲突 | 独立 ASGI app 挂 /captions_ws/ 前缀，path 隔离；实施首日冒烟（页面连接+推送+config_update） |"},{"oldText":"3. 互斥提示（对齐 EDTalk 计划四组合提示模式）：`is_edtalk_active` 为真时页面顶部常驻提示\"数字人模式下音频由 EDTalk 播放，播放器选择不生效\"。","newText":"3. 互斥提示（对齐 EDTalk 计划四组合提示模式）：`is_edtalk_active` 为真时页面顶部常驻提示\"数字人模式下音频由 EDTalk 播放，播放器选择不生效\"。（来源：计划初稿既有条目，非 0G cherry-pick）"},{"oldText":"2. 移除 API 地址输入框；新增**字幕页地址回显**（只读 + \"复制地址\"按钮，供 OBS 浏览器源粘贴）。","newText":"2. 移除 API 地址输入框；新增**字幕页地址回显**（只读 + \"复制地址\"按钮，供 OBS 浏览器源粘贴；地址 = http://<api_ip>:<api_port>/captions，api_ip 为 0.0.0.0 时回显 127.0.0.1）。"},{"oldText":"4. web字幕打印机：enable → `http://<webui_ip>:<webui_port>/captions` 页面随音频逐句显示字幕，样式 14 键修改保存后**即时热更新**；keep_time_align_audio 开启时字幕保持至音频播完。","newText":"4. web字幕打印机：enable → `http://<api_ip>:<api_port>/captions` 页面随音频逐句显示字幕，样式 14 键修改保存后**即时热更新**；keep_time_align_audio 开启时字幕保持至音频播完。"},{"oldText":"  \"priority_mapping\": { ... },           // type→优先级数值，默认表按 AUTOlive 实际 type 词表重写（见 R7）","newText":"  \"priority_mapping\": { ... },           // type→优先级数值，默认表按 AUTOlive 实际 type 词表重写（见 R5）"},{"oldText":"- priority_mapping 默认值必须与 AUTOlive data_json.type 实际词表对齐（comment / reread / schedule / entrance / gift / follow / copywriting / idle_time_task / local_qa_audio 等，实施首日 grep 词表核对，见 R7）。","newText":"- priority_mapping 默认值必须与 AUTOlive data_json.type 实际词表对齐（comment / reread / schedule / entrance / gift / follow / copywriting / idle_time_task / local_qa_audio 等，实施首日 grep 词表核对，见 R5）。"},{"oldText":"4. 小型控制区（卡片，选 builtin 时显示）：队列条数回显（get_list）、暂停/续播/跳过当前/清空队列四按钮——运营者手动打断用；服务未选 builtin 时显示空态\"内置播放器未启用\"。","newText":"4. 小型控制区（卡片常驻）：选 builtin 时显示队列条数回显（get_list）与暂停/续播/跳过当前/清空队列四按钮——运营者手动打断用；未选 builtin 时卡片显示空态文案\"内置播放器未启用\"。tooltip 注明语义边界：\"插队仅重排待播顺序，不打断当前播放\"（native F1 预期管理）。"},{"oldText":"## 4. 模块一：内置音频播放器\n\n### 4.1 代码移植清单","newText":"## 4. 模块一：内置音频播放器\n\n> **方案取舍记录（native F5）**：曾考虑\"基于 pygame mixer 的队列层\"替代（可消除 R1 的 PyAudio 设备风险域），否决理由：①插队/暂停续播语义保真——源项目 callback 流式架构原生支持暂停点续播；②源项目实测稳定；③pygame mixer 无流级暂停位置追踪，需自建状态机，复杂度反而更高。6 个月后回看此记录即知为何背上 PyAudio 设备管理。\n\n### 4.1 代码移植清单"},{"oldText":"| R9 | 断链修复涉及播放主循环（playback_manager）回归 | 分支结构零逻辑改动原则；回归三模式（pygame/builtin/edtalk）+ 外部模式冒烟 |","newText":"| R9 | 断链修复涉及播放主循环（playback_manager）回归 | 分支结构零逻辑改动原则；回归三模式（pygame/builtin/edtalk）+ 外部模式冒烟 |\n| R10 | EDTalk 模式字幕音画同步——字幕在本地 dequeue 推送，EDTalk 端开口时机由其管线决定，可能系统性超前 | 首日冒烟必测项：EDTalk 模式字幕延迟实测（native F3）；偏移明显则登记\"EDTalk 回调驱动字幕\"TODO |"},{"oldText":"5. EDTalk 模式：音频仅推送 EDTalk（无本地播放、无双声），字幕页照常逐句显示（补齐原缺口）。","newText":"5. EDTalk 模式：音频仅推送 EDTalk（无本地播放、无双声），字幕页照常逐句显示（补齐原缺口）；首日冒烟实测字幕与 EDTalk 开口的偏移在可接受范围（native F3，偏移明显则降级登记 TODO）。"},{"oldText":"audio_player(_v2)=外部服务模式（需自行启动外部项目，仅兼容保留）","newText":"audio_player(_v2)=外部服务模式（需自行启动外部项目；兼容保留，builtin 稳定一个版本后移除——sunset 判据登记 TODOS，native F4）"},{"oldText":"### 5.2 服务挂载（主系统进程内部 HTTP API，utils/web_server.py）","newText":"### 5.2 服务挂载（主系统进程内部 HTTP API，utils/web_server.py）\n\n> **渲染安全（S3）**：源项目 showSubtitle 用 innerHTML 渲染（支持 <br> 换行）——LLM 生成内容直接进 DOM = XSS 向量。移植时改为：文本节点 HTML 转义 + 仅白名单转换换行符为 <br>（保留换行语义，杜绝标签注入）。"},{"oldText":"- **keep_time 对齐音频时长**（原服务端忽略 keep_time 的升级修复）：","newText":"- **异常隔离（S4）**：CaptionsManager.push 内部全捕获+日志计数，任何字幕错误不得传播进播放循环（调用点在播放主 try 块内，异常会吞掉当轮播放）；content 为空时跳过推送（不推空字幕）。\n- **keep_time 对齐音频时长**（原服务端忽略 keep_time 的升级修复）："},{"oldText":"### 4.4 播放完成回调","newText":"### 4.4 播放完成回调\n\n> **队列有界（S7）**：源播放器与字幕队列均无上限。对齐 EDTalk 计划\"链路队列全有界+阻塞式背压\"既定义务：内置播放器待播队列上限 50（满载 add_audio_json 阻塞等待，上游 voice_tmp_path_queue 已有界）、字幕队列上限 100（满载丢弃最旧并日志告警——字幕可丢，台词不可丢）。"}]} -->

<!-- autoplan-accepted:ceo -->
- 用户三点原始需求全保持：①audio_player 核心逻辑整合进系统 audio 播放流程，设置在"前端基础功能-音频播放"，不再请求外部 API；②captions_printer 集成为系统模块，设置在"前端基础功能-web字幕打印机"，不再请求外部 API；③功能随系统启动即可用。
- 整合形态 = 进程内模块（非内嵌子服务/子进程）；外部项目目录解除依赖、原样保留不删除。
- 内置播放器以与 AUDIO_PLAYER 客户端相同的 6 方法签名（play/pause_stream/resume_stream/skip_current_stream/get_list/clear）鸭子类型接入；playback_manager 调用点零逻辑改动；play_audio.player 新增 builtin 选项，默认值保持 pygame 不变。
- 移植时修复源播放器 4 缺陷：自旋等待烧 CPU、tmp wav 只增不清、异常清空全队列、set_device_index 写了不读。
- web字幕打印机修复断链：playback_manager.py:74-75 改为 CaptionsManager 进程内直调（原 send_to_web_captions_printer 方法不存在）；推送点保持在播放分支前，全模式（EDTalk/pygame/builtin）共用。
- 字幕页挂载目标修正为**主系统进程内部 HTTP API**（utils/web_server.py FastAPI，_register_routes() 注册 /captions + /captions_ws/ socket.io；不挂 webui——双进程拓扑实证：webui 内 Audio(type=2) 于 audio_core.py:90-93 提前返回，完整播放循环在主系统进程 my_handle.py:142）；字幕页地址 = http://<api_ip>:<api_port>/captions（0.0.0.0 回显转 127.0.0.1，先例 frontend/main.py:902）；事件契约沿用源项目 message/config_update。
- keep_time 对齐音频时长（soundfile 读时长，失败降级自动计算；keep_time_align_audio 默认 true）。
- CaptionsManager 并发模型（spec CL-1 义务）：push 异步入口内部线程安全入队（list+Lock，沿用源 MessageQueueManager 模式），节流线程经 asyncio.run_coroutine_threadsafe 将 sio.emit 提交主系统进程 event loop（与 uvicorn 同 loop）；config_update 同通道。
- stop_current_audio 条件修复（spec C-1 义务）：playback_manager.py:201 由 `== "audio_player"` 改为 `in ["audio_player", "audio_player_v2", "builtin"]`（builtin 弹幕打断必需；顺带修复 v2 既有缺陷）——此为"调用点零改动"承诺的唯一声明例外。
- 前端两设置页原地扩展：builtin 选项+设备下拉+控制区（暂停/续播/跳过/清队+条数回显）+EDTalk 互斥提示+外部地址动态显隐；web_captions_printer 页补 enable 开关+14 键样式配置+地址回显复制+热更新。
- 桌面字幕窗口（tkinter）本期丢弃；打断恢复机制、音量控制、播放器级变速、外部模式移除均入 NOT in scope。
- 零新增 pip 依赖（pyaudio/pydub/python-socketio runtime312 实测已具备）；priority_mapping 默认表实施首日按 AUTOlive type 词表核对重写。
- coordination_program 两条过期外部服务条目删除；web_captions_printer.api_ip_port 键 migrate 移除。
- 0G cherry-pick 5 项全部接受（keep_time 对齐/队列控制区/地址复制/动态显隐/过期条目清理），均已落计划条目；§6.1.3 互斥提示标注来源为初稿既有条目（非 0G cherry-pick）。
- 用户口述"暂停插队续播"按代码实证三项能力（插队/暂停/续播）解读；"打断当前→插播→恢复"前提落差与桌面窗口丢弃列为 User Challenge，留 Phase 4 最终批准门裁决。
- Spec 审查 C-2 修正（义务）：CEO 摘要 0I 风险编号 R5/R7 错位已纠正；架构图/§4.6/R2/R3/R4/验收4 进程边界措辞同步修正。
- 设计声部义务（2026-09-25，native 12 项全采纳，[subagent-only]）：D1 初始配置通道（sio connect emit 全量配置——发布阻断级修复）；D5/D6 get_list UI 扩展（queue_len/playing/paused/current 摘要）+控制区状态回显行；D7 ui.timer 1s 轮询（builtin+页面可见才激活）；D9 控制区错误条（R1/R8 浮出）；D2 14 键卡内三组信息架构（时序 4 键收高级折叠区）；D3 EDTalk 激活时 select disabled；D4 优先级映射只读折叠区单一结构；D8 设备下拉 loading/空列表态；D10 body_bg_color 透明语义（空值/none→transparent，OBS 指引行；默认色值 taste 留 Phase 4）；D11 播放器选择重启主系统生效（首日验证热切）；D12 enable 旁三步微指引；mockup 未生成（designer API key 缺失）→实施后 /design-review 视觉 QA 义务（沿 EDTalk 先例）。
- Spec 审查第2轮 N-1/N-2/N-3 修正（义务）：§4.5 两处风险引用 R7→R5；§6.1.4 控制区改为卡片常驻+按 builtin 显隐内容；§5.2 补 enable 语义（挂载随主系统启动无条件创建，enable 仅控制 push 短路，开关修改重启生效）。
- native CEO 声部义务（2026-09-25，放行附三条件）：F1 打断恢复升级为 Phase 4 必须裁决项（两 demo 语义：纯队列插队 vs 打断恢复），控制区 tooltip 标注"插队不打断当前播放"；F3 EDTalk 字幕同步实测入首日冒烟（新增 R10）+ 验收5 补同步性；F6 builtin 停止语义闭合 skip_current_stream + 单测；F4 外部模式 tooltip sunset 措辞 + TODOS 登记移除判据；F5 §4 头补 pygame-mixer 替代方案取舍记录；F2 默认值张力（存量 pygame/新配置 builtin 或一次性引导）留 Phase 4 taste；F7/F8 记录无动作。
<!-- /autoplan-accepted:ceo -->

（审查记录：各阶段义务块与审计Trail由审查流程写入。）

### CEO DUAL VOICES — CONSENSUS TABLE [subagent-only]（Codex provider 400 不可用）

| 维度 | Claude | Codex | 共识 |
|---|---|---|---|
| 1. 前提有效？ | 强前提实证、3 薄弱前提已列（F1/F3/F6） | N/A | N/A（单声部） |
| 2. 解正确的问题？ | 是（断链修复+EDTalk字幕补齐为真价值） | N/A | N/A |
| 3. 范围校准正确？ | SELECTIVE EXPANSION 5 cherry-pick 均合理 | N/A | N/A |
| 4. 替代方案充分探索？ | F5 补 pygame-mixer 取舍记录后充分 | N/A | N/A |
| 5. 竞争/市场风险 | F7 内部整合无市场风险；上游内化风险已有缓解位 | N/A | N/A |
| 6. 6 个月轨迹 | F2/F4 已处置（taste/TODO） | N/A | N/A |

Codex 外部声部不可用（trivial probe 亦 400 InvalidParameter；gpt-6-astra 元数据缺失）——修复：GSTACK_CODEX_MODEL 覆盖或 provider 修复。原生声部完整；六格 N/A，永不 CONFIRMED。结论放行附三条件（F1 必须裁决/F3 同步实测/F6 停止语义闭合），全部已落义务。

### Sections 1-11 审查（CEO 深审，2026-09-25）

**S1 架构**——C-0 双进程拓扑修正后自洽：内置播放器/CaptionsManager 随主系统进程（my_handle.py:142 完整 Audio），字幕页+socket.io 挂内部 API（web_server.py）。耦合面：Audio→builtin_play_center（同包）、web_server→CaptionsManager（同进程），均落既有 Mixin/单例模式内。单点：播放器线程死=播放停（单条失败跳过+日志）；socket 挂载失败=字幕离线（音频不受影响）。回滚=player 改回 pygame（一行配置，即时）。No new issues（C-0 已由 spec 轮修复并验证）。

**S2 错误与救援**——映射：play_audio 循环（设备打开失败→回退默认设备+日志（R1）；解码失败→单条跳过【缺陷3修复后】）；push（enable=false 短路；读时长失败→降级自动计算（R6）；emit 提交失败→日志+计数）；/captions（模板文件缺失→500+日志）；sio.emit（无客户端=no-op）。catch-all 检查：无新增；源 :174-180 异常清全队列缺陷已修复为单条跳过。Error & Rescue Registry 见 Failure Modes Registry（本计划规模下两表合并，行粒度=codepath 级；方法级契约在 eng 阶段深化）。0 GAP 遗留。

**S3 安全**——真发现：**XSS 向量**（源 innerHTML 渲染 LLM 内容）→ 渲染层转义+白名单换行转换（已落义务）。新增攻击面评估：/captions 无输入端点（手动发送不移植）、无新依赖（供应链零）、无 secrets；/captions 随 api_ip 暴露面与既有 /send 一致（0.0.0.0 时局域网可达——既有接受面，无新增凭据）；WS 广播无鉴权=与内部 API 同语义。威胁矩阵：内容注入（High likelihood/Med impact/已缓解）、局域网暴露（Low/Low/既有接受）。

**S4 数据流与交互边界**——字幕数据流：dequeue→push(队列+Lock)→节流线程→run_coroutine_threadsafe(emit)→OBS。nil/empty：content 空→跳过推送（义务已落）；voice_path 缺失→keep_time 降级。error：push 全吞隔离（义务已落——调用点在播放主 try 内，否则字幕错误吞掉当轮播放）。交互：控制区按钮幂等（pause/resume 语义天然幂等）；OBS 刷新→socket.io 自动重连（无状态同步，下一条字幕恢复显示，可接受）；多 OBS 源=广播天然支持。异步序：单节流线程串行消费（源设计保真），无竞争对。

**S5 代码质量**——移植物贴合既有模式（Mixin 组合/register_client 单例/ensure-default/FormField 三列网格）；DRY：data_json speed 字段固定 1/False 与 playback_manager 构造重复但语义显式（P5 保留）；命名清晰（builtin_play_center/CaptionsManager）。No issues（spec 两轮已深滤）。

**S6 测试**——单测：priority_insert 三语义（优先级序/insert_index/未识别 type 队尾）、stop_current_audio 三模式分发（builtin→skip_current_stream，F6 义务）、push 短路、keep_time 计算+降级路径、设备回退。集成冒烟：三模式播放回归（R9）、EDTalk 字幕同步实测（R10）、socket.io 连接+config_update 热更。Flaky：设备枚举测试 skip-if-no-device 标注。2am Friday 测试=builtin 全链路（播放+打断+字幕）冒烟脚本。金字塔：单测 6+集成 3+E2E 0（单机直播工具，OBS 人工验收）——合理。

**S7 性能**——修复自旋后播放等待 0 CPU（Event 等待）；真发现：**源队列无上限**→队列有界义务已落（播放 50 阻塞背压/字幕 100 丢最旧——对齐 EDTalk 背压语义：字幕可丢、台词不可丢）；无 N+1（无 DB）；emit 广播 O(n_clients)，n≤3 无压力；tmp wav 清理（缺陷2）即磁盘性能项。

**S8 可观测**——日志：播放开始/完成（type+耗时）、插队生效、设备回退、字幕推送失败计数、socket 连接/断开；指标：队列长度=控制区回显（get_list 既有）；告警：无（单机，日志+控制区足够）；runbook：R1→设置页选默认设备、字幕黑屏→查 enable+地址回显。调试性：单条失败日志含 type+路径（修复缺陷3时带上下文）。no gaps。

**S9 部署**——无 DB 迁移；配置迁移=R7 ensure-default（老配置补键+api_ip_port 移除）；flag=builtin 本身 opt-in、字幕 enable 默认 false=存量零影响；回滚=player 改回 pygame+重启（一分钟内）；部署序=代码→重启主系统→验收 1-5 冒烟。存量/新配置默认值张力（F2）留 Phase 4 taste。

**S10 长期轨迹**——债：外部模式 sunset TODO（F4，判据=builtin 稳定一版本）；可逆性 4/5（配置行回滚+目录未删除）；路径依赖：内置化后未来 EDTalk 原生字幕回调可直接替换推送源（F7 缓解位既有）；知识：§3 图+F5 取舍记录+STRUCTURE.md 同步义务。1 年问题：新工程师从架构图+取舍记录可完整复原决策。

**S11 设计（UI scope 有）**——信息架构：两页均在基础功能分组（既有二级列表），无新增导航；状态覆盖：控制区=空态（未启用文案，N-2 修正后自洽）/错误（设备失败+设备列表指引）；字幕页=loading 黑屏（OBS 场景正确）/empty 静默/error 自动重连；设计令牌硬约束+3 列网格已写入义务；无障碍：字幕 14 键可配（字体/描边/对比度）天然服务观众侧可读性；AI slop：无（复用既有 tab 样板）。用户流：设置页选择→运行→OBS 贴 /captions 地址（一键复制）——三步完成。建议：Phase 2 设计审查（autoplan 流水线下一阶段即 /plan-design-review）。

### Failure Modes Registry（CEO，规范化）

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| builtin play_audio | 设备打开失败 | Y（回退默认+报错附设备列表） | Y（单测 skip-if-no-device） | 报错+指引 | Y |
| builtin play_audio | 解码失败/文件损坏 | Y（单条跳过） | Y | 该条无声，播下一条 | Y |
| builtin play_audio | 队列满载 | Y（阻塞背压） | Y（单测） | 播放延迟 | Y |
| stop_current_audio(builtin) | 条件遗漏→AttributeError | Y（C-1 修复+单测） | Y | 打断生效 | Y |
| CaptionsManager.push | 读时长失败 | Y（降级自动计算） | Y | 字幕按默认时长 | Y |
| CaptionsManager.push | emit 提交失败 | Y（日志+计数） | Y | 字幕缺条，音频正常 | Y |
| /captions socket | 页面断线 | Y（socket.io 自动重连） | 冒烟 | 短暂黑屏后恢复 | Y |
| EDTalk 模式字幕 | 音画偏移 | 冒烟实测把关（R10） | Y（首日必测） | 偏移明显则 TODO 降级 | Y |
| 渲染层 | XSS 注入 | Y（转义+白名单） | Y（单测：恶意输入转义） | 正常显示转义文本 | Y |

CRITICAL GAP = 0（全部 Y/Y/可见/日志）。Error & Rescue Registry 与本表合并呈现（S2 说明）。

### 决策审计（Decision Audit Trail）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否 |
|---|---|---|---|---|---|---|
| 1 | 0D | 整合形态=进程内模块（主系统进程） | Mechanical | P1/P3 | 消除网络层/端口，错误同进程 | 内嵌子服务、修复外部拉起 |
| 2 | 0D | 字幕通道=独立 socket.io 挂载 | Mechanical | P1 | 复用源前端零改造+框架解耦 | 复用内部 sio、原生 WS 重写 |
| 3 | 0D | 播放器接入=鸭子类型 6 方法 | Mechanical | P3/P5 | 调用点零改动 | playback_manager 重构 |
| 4 | 0E | 模式=SELECTIVE EXPANSION | Mechanical | autoplan 覆盖 | 固定 | — |
| 5 | 0G | 5 cherry-pick 全接受 | Mechanical | P2 | blast radius 内+<1d | — |
| 6 | spec C-0 | 挂载目标=主系统内部 API | Mechanical | P1 | 跨进程不可达=验收不可达 | 挂 webui |
| 7 | spec C-1 | stop_current_audio 条件扩展 | Mechanical | P1 | builtin 打断必需 | 保持原条件 |
| 8 | native F1 | 打断恢复=Phase 4 必须裁决 | User Challenge | 用户裁决 | 前提落差+用户价值未验证 | 自动裁决（禁止） |
| 9 | native F2 | 默认值策略 | Taste | 留门 | 存量安全 vs 可发现性张力 | — |
| 10 | native F3-F6 | 同步实测/停止闭合/sunset/取舍记录 | Mechanical | P1/P2 | 放行条件全采纳 | — |
| 11 | S3 | 渲染转义+白名单 | Mechanical | P2 | XSS 真向量，blast radius 内 | 保留源 innerHTML |
| 12 | S4 | push 全吞隔离+空跳过 | Mechanical | P1 | 字幕错误不得炸播放 | — |
| 13 | S7 | 队列有界（50 阻塞/100 丢旧） | Mechanical | P2 | 对齐 EDTalk 背压义务 | 无界队列 |
| 14 | Codex | 外部声部=unavailable | Mechanical | 失败政策 | trivial probe 400 | — |
| 15 | 0H | 文档批准=A | Mechanical | autoplan | 两输入反映精确决策 | — |

### CEO Completion Summary

```
  +====================================================================+
  |            MEGA PLAN REVIEW — COMPLETION SUMMARY (CEO)             |
  +====================================================================+
  | Mode selected        | SELECTIVE_EXPANSION (autoplan 覆盖)          |
  | System Audit         | 双进程拓扑/断链活缺陷/EDTalk 字幕缺口         |
  | Step 0               | SELECTIVE EXPANSION；5 cherry-pick 全接受     |
  | Section 1  (Arch)    | 0 新发现（C-0 已修）                          |
  | Section 2  (Errors)  | 9 错误路径映射，0 GAP                         |
  | Section 3  (Security)| 1 发现（XSS→义务），0 High 未缓解             |
  | Section 4  (Data/UX) | 6 边界映射，0 未处理（义务已落）              |
  | Section 5  (Quality) | 0 发现                                        |
  | Section 6  (Tests)   | 映射完成，0 缺口                              |
  | Section 7  (Perf)    | 1 发现（队列上限→义务）                       |
  | Section 8  (Observ)  | 0 缺口                                        |
  | Section 9  (Deploy)  | 0 风险（回滚=一行配置）                       |
  | Section 10 (Future)  | 可逆性 4/5，债 1（外部模式 sunset）           |
  | Section 11 (Design)  | 0 发现 / UI scope 已评                        |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (8 项)                                |
  | What already exists  | written                                       |
  | Dream state delta    | written                                       |
  | Error/rescue registry| 9 行，0 CRITICAL GAPS                         |
  | Failure modes        | 9 total，0 CRITICAL GAPS                      |
  | TODOS.md updates     | 2 项（外部模式 sunset；EDTalk 回调字幕——条件触发）|
  | Scope proposals      | 5 proposed, 5 accepted                        |
  | CEO plan             | written（ceo-plans/2026-09-25-*.md）           |
  | Outside voice        | codex unavailable（provider 400）              |
  | Lake Score           | N/A（coverage 项均为单选项义务）               |
  | Diagrams produced    | 2（架构图/字幕数据流）                         |
  | Stale diagrams found | 0                                             |
  | Unresolved decisions | 2 User Challenges + 1 Taste（留 Phase 4 门）   |
  +====================================================================+
```

### NOT in scope（CEO 确认：计划 §7 八项无新增）

### What already exists（CEO）：见计划 §2 现状核实表与 0B——播放三分支/门面 6 方法/register 模式/ensure-default/两设置 tab/内部 API 路由先例全部复用，零重建。

### Dream state delta：本计划使 AUTOlive 距"单仓全内置直播系统"理想缩短两步（消除 2 外部进程+修复活缺陷+补齐 EDTalk 字幕）；剩余差距=EDTalk 回调驱动字幕（R10 条件 TODO）与音量控制（TODOS）。

**UNRESOLVED DECISIONS（留 Phase 4）**：User Challenge #1（打断恢复必须裁决——native 升级为强制）；User Challenge #2（桌面窗口丢弃确认）；Taste：F2 默认值策略。

### DESIGN OUTSIDE VOICES — LITMUS SCORECARD [subagent-only]（Codex 400 不可用；mockup 未生成——designer API key 缺失）

```
DESIGN OUTSIDE VOICES — LITMUS SCORECARD:
═══════════════════════════════════════════════════════════════
  Check                                    Claude  Codex  Consensus
  ─────────────────────────────────────── ─────── ─────── ─────────
  1. Brand unmistakable in first screen?   N/A*    —      N/A
  2. One strong visual anchor?             N/A*    —      N/A
  3. Scannable by headlines only?          YES     —      N/A
  4. Each section has one job?             YES     —      N/A
  5. Cards actually necessary?             YES     —      N/A
  6. Motion improves hierarchy?            N/A     —      N/A
  7. Premium without decorative shadows?   YES     —      N/A
  ─────────────────────────────────────── ─────── ─────── ─────────
  Hard rejections triggered:               0       —      N/A
═══════════════════════════════════════════════════════════════
```
*字幕页为纯显示面（OBS 叠加），品牌/锚点/motion 类 litmus 不适用；设置页复用既有 design_tokens 样板（OPERATE 模式：calm hierarchy/dense-but-readable/utility language 全部满足）。分类器：设置页=OPERATE（App UI Rules），字幕页=EXPERIENCE（artifact 占满、chrome 退位）。Hard rejection 0：无卡片网格首页/无 hero/无 stacked-cards（设置页卡片=Quasar 既有卡片语义+FormField 样板，卡片即交互容器）。

### Design Passes 1-7（autoplan auto-decide，2026-09-25）

**Pass 1 信息架构：6/10 → 9/10**——发现 D2（14 键无内部层级：时序微调键与高频显示模式混排）D4（优先级映射嵌套表述混乱）。修复：卡内三组（平铺/次级/高级折叠）+映射只读折叠区单一结构（已落）。9 因页面级导航零变更（两页均在既有基础功能分组二级列表）。
**Pass 2 交互状态覆盖：4/10 → 9/10**——系统性盲区：初始状态通道（D1 critical：connect 时 emit 全量配置——不修则 OBS 重开即样式回退，热更新承诺失效）、按钮状态机（D5：暂停/续播无反馈=盲按钮）、播放感知（D6）、刷新机制（D7）、设备下拉 loading/空态（D8）、错误呈现通道（D9：直播中不看日志文件）。全部已落（get_list UI 扩展+状态回显行+错误条+轮询规范）。9 因字幕页的 loading/error 天然简单（黑屏/自动重连）。
**Pass 3 用户旅程：5/10 → 9/10**——发现 D10（OBS 透明缺失+白底默认=开箱即失败）、D11（播放器切换生效时机未说明）、D12（首次启用三重门槛无引导）。修复：透明语义+OBS 指引行+生效时机标注+三步微指引（已落）。遗留 D10③（默认白底 vs 透明）=taste 留 Phase 4。
**Pass 4 AI Slop：8/10 → 9/10**——设置页=OPERATE 模式合规（utility language/calm hierarchy/卡片即交互）；无黑名单命中（无 3 列图标网格/无渐变/无 emoji 装饰；3 列网格=NiceGUI 表单栅格非 feature grid）。字幕页=EXPERIENCE（纯显示）。mockup 未生成，按计划文本评估；9 因视觉证据缺失。
**Pass 5 设计系统对齐：8/10 → 10/10**——design_tokens 硬约束+FormField/三列网格样板已在计划义务；色盘控件沿用既有；字幕页豁免 tokens（OBS 用户配置驱动，已在计划声明）。
**Pass 6 响应式/无障碍：7/10 → 9/10**——字幕页 14 键（字号/描边/对比度可配）天然服务观众可读性；OBS 固定分辨率场景明确（无移动断点需求，如实声明）；设置页 3 列网格+width:100% 既有规范覆盖；键盘导航=Quasar 兜底（ARIA 增强沿 EDTalk TODOS 先例保持挂起）。
**Pass 7 未决设计决策：2 项登记**——①D10③ 字幕页默认背景色（白底 vs 透明默认）→ Phase 4 taste；②视觉 QA 义务（mockup 缺失→实施后 /design-review）→ 义务非决策。

### Design 阶段 NOT in scope（新增 2 项）

| 项 | 理由 |
|---|---|
| 字幕页移动端/响应式断点 | 唯一场景=OBS 固定分辨率浏览器源（§5.1 明示）；如实声明无此需求 |
| 设置页视觉重设计 | 复用既有 design_tokens 样板即合规；页面级重设计超出本期 blast radius |

### What already exists（Design）：design_tokens.py 令牌体系、FormField 组件、3 列网格+width:100% 样板（common_config.py/filter_config.py 模板）、Quasar 色盘控件、二级列表导航——全部复用，零新建模式。

### Design Implementation Tasks（markdown 见任务 JSONL；关键项已并入计划 §4/§5/§6 条目）

### Design Completion Summary

```
  +====================================================================+
  |         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
  +====================================================================+
  | System Audit         | design_tokens 即 DESIGN.md 等价物；UI scope= |
  |                      | 2 设置页+1 字幕显示页                        |
  | Step 0               | 初始 4/10（状态通道系统性缺失为最大缺口）     |
  | Pass 1  (Info Arch)  | 6/10 → 9/10（D2/D4 修复）                    |
  | Pass 2  (States)     | 4/10 → 9/10（D1/D5/D6/D7/D8/D9 修复）        |
  | Pass 3  (Journey)    | 5/10 → 9/10（D10/D11/D12 修复）              |
  | Pass 4  (AI Slop)    | 8/10 → 9/10（0 hard rejection）              |
  | Pass 5  (Design Sys) | 8/10 → 10/10（tokens 全对齐）                |
  | Pass 6  (Responsive) | 7/10 → 9/10（OBS 场景明确+可读性可配）        |
  | Pass 7  (Decisions)  | 2 resolved in-plan, 1 deferred (D10③ taste)  |
  +--------------------------------------------------------------------+
  | NOT in scope         | written (2 新增 + §7 既有 8 项)              |
  | What already exists  | written                                     |
  | TODOS.md updates     | 1 项（视觉 QA——随实施义务，不入 TODOS）       |
  | Approved Mockups     | 0 generated（API key 缺失）/ 0 approved      |
  | Decisions made       | 12（D1-D12 全部落计划条目）                  |
  | Decisions deferred   | 1（D10③ 默认色值→Phase 4 taste）             |
  | Overall design score | 4/10 → 9/10                                 |
  +====================================================================+
```

**UNRESOLVED DECISIONS（留 Phase 4）**：D10③ 字幕页默认背景色（白底 vs 透明默认）——taste。
