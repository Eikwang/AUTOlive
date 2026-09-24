# AUTOlive × audio_player / captions_printer 功能整合计划

> 日期：2026-09-24 ｜ 发起：用户 /autoplan 指令
> 前置事实：三代码库实地摸底（AUTOlive 直读 + audio_player/captions_printer 全量通读，2026-09-24）
> 参照：`specs/integration/EDTalk功能集成计划.md`（同类整合范本）

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
| NiceGUI 3.16.0，`app` 为 FastAPI 实例，已有 7 个自定义路由；内部 socket.io 挂载于 `/_nicegui_ws/`（nicegui/nicegui.py:52-54） | frontend/main.py:17、816-968 | 字幕页挂主服务：自有路由 + 独立 socket.io 挂载（不复用内部 sio，防版本耦合） |
| coordination_program 两条过期外部服务条目（E://GitHub_pro//...，enable:false） | config.json:1262-1279 | 处置（见 §4.6） |
| 前端设置页已存在：audio_play.py（播放器选择+变速）、web_captions_printer.py（仅 API 地址，**缺 enable 开关**） | frontend/ui/tabs/ | 设置页原地扩展，导航无需新增 |
| config 现状：`audio_player={api_ip_port}`；`web_captions_printer={enable, api_ip_port}` | config.json:65-67、707-710 | 配置节改造见 §4.5 / §5.4 |
| 新 UI 样式必须走设计令牌（design_tokens.py），3 列网格 + width:100% 样板 | docs/designs/frontend-beautification-plan.md；记忆 frontend-design-tokens | 前端改动硬约束 |

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
│ 字幕页 /captions（frontend/web_captions/ 静态页）← OBS 浏览器源             │
│ 前端设置页：audio_play.py / web_captions_printer.py（基础功能分组，已存在）   │
└─────────────────────────────────────────────────────────────────────────┘

外部项目 D:\AI\audio_player / D:\AI\captions_printer：解除依赖，目录原样保留（不删除）
```

整合形态：**进程内模块**（不是内嵌子服务、不是子进程）。播放器与字幕管理器作为 Audio/系统对象的组件随系统启停；前端仅剩一个字幕显示页通过 WebSocket 接收推送（该页是"屏幕输出"，不是 API 调用）。

## 4. 模块一：内置音频播放器

### 4.1 代码移植清单

| 源（D:\AI\audio_player\） | 目标（D:\AI\AUTOlive\） | 处置 |
|---|---|---|
| utils/audio_play_center.py（AUDIO_PLAY_CENTER 类） | utils/audio/builtin_play_center.py | 移植 + §4.3 缺陷修复 + §4.2 接口适配 |
| utils/common.py 的 download_audio / search_audio_file / copy_audio_file / get_all_audio_device_info / get_new_audio_path | utils/audio/builtin_play_center.py 内并入（仅保留实际用到的） | 按需精简并入，不建独立 Common |
| config.json 的 priority_mapping / device_index / audio_interval / random_audio_interval 语义 | config.json `audio_player` 节（§4.5） | 配置语义并入 |
| app.py Flask 壳、index.html/js/css 控制台、bat、logger、tests | 不移植 | 壳丢弃；logger 用 utils/my_log |

### 4.2 接口适配（调用点零改动）

- 内置播放器暴露与 `AUDIO_PLAYER` 客户端**相同的 6 方法签名**：`play(data)`（→ add_audio_json）、`pause_stream()`、`resume_stream()`、`skip_current_stream()`、`get_list()`、`clear()`（→ clear_audio_json）。
- audio_core.py:118 按 `play_audio.player` 分流实例化：`builtin` → 内置 `AUDIO_PLAY_CENTER`，其余 → 既有 HTTP 客户端。playback_manager.py 两处 `self.audio_player.play(data_json)`（156、266）与 `stop_current_audio()`（201-202）**零逻辑改动**。
- 分支条件扩展：`elif self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2", "builtin"]`（130、254 两处）。
- 播放器级变速**不移植**（变速已由 playback_manager 层在 TTS 产物上完成——audio_random_speed 配置 + pydub speedup 不变调；播放器级改采样率变速会变调，丢弃）；`speed`/`random_speed` 字段固定 1/False（与现 playback_manager 构造值一致）。

### 4.3 移植时修复的已知缺陷（源码实证）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 播放等待为 `while ... : pass` 自旋烧 CPU（audio_play_center.py:158-159） | 改 threading.Event 等待（pyaudio callback 完成事件） |
| 2 | `./out/tmp_*.wav` 解码临时文件只增不清（:111-115） | 播放完成后删除临时文件；启动时清理遗留 |
| 3 | AttributeError 兜底清空**整个队列**（:174-180） | 单条失败跳过 + 日志，不动队列余项 |
| 4 | `set_device_index()` 写的字段播放时不读（:209-210），运行时切设备无效 | 播放时读取当前配置 device_index |

### 4.4 播放完成回调（对齐 info_to_callback）

外部 audio_player 模式下，播放完成由外部服务 POST /callback 回传（audio_play_center.py:40-59）；内置后改为进程内回调：`AUDIO_PLAY_CENTER` 支持注册完成回调，audio_core 初始化时注册 `send_audio_play_info_to_callback`（注意其为 async 协程——播放器在独立线程，经 `asyncio.run_coroutine_threadsafe` 提交到主 loop，或以同步 wrapper 封装；实施时按 Audio 类既有线程/协程模型选型，eng 审查把关）。`play_audio.info_to_callback` 开关语义不变。

### 4.5 配置改造（config.json `audio_player` 节）

```json
"audio_player": {
  "device_index": -1,                    // -1 = 系统默认设备；设置页下拉枚举
  "audio_interval": 0,                   // 播完后固定间隔（秒）
  "random_audio_interval": {"enable": false, "min": 0, "max": 0},
  "priority_mapping": { ... },           // type→优先级数值，默认表按 AUTOlive 实际 type 词表重写（见 R7）
  "api_ip_port": "http://127.0.0.1:5602" // 仅旧外部模式使用（audio_player/audio_player_v2）
}
```

- `play_audio.player` 默认值**保持 `pygame` 不变**（既有用户行为零变化）；`builtin` 为新增选项，用户在设置页选择。
- priority_mapping 默认值必须与 AUTOlive data_json.type 实际词表对齐（comment / reread / schedule / entrance / gift / follow / copywriting / idle_time_task / local_qa_audio 等，实施首日 grep 词表核对，见 R7）。

### 4.6 启动/停止接线与旧条目处置

- 内置播放器随 Audio 类初始化启动（daemon 线程），随 webui 进程存活——与 pygame 模式线程模型一致，无新增进程管理负担。
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

### 5.2 服务挂载（frontend/main.py）

- `@app.get('/captions')` 返回字幕页（HTMLResponse 读 frontend/web_captions/index.html）；静态资源同目录服务。
- 推送通道：**独立 socket.io ASGI 挂载**（`socketio.ASGIApp(socketio.AsyncServer(async_mode="asgi"))` mount 至 `/captions_ws/`，客户端连接 `socket.io` path `/captions_ws/socket.io`）——不复用 NiceGUI 内部 sio（`/_nicegui_ws/` 为框架内部实现，避免版本升级耦合）；python-socketio 已安装（实测），零新依赖。
- 事件契约沿用原项目：服务端→客户端 `message`（{content, start_delay, keep_time}）与 `config_update`；客户端→服务端无需任何事件（手动发送功能不移植）。

### 5.3 推送接线（修复断链 + 时长对齐）

- playback_manager.py:74-75 修复为进程内直调：`await self.captions_manager.push(data_json)`（CaptionsManager 单例随 audio_core 初始化创建；照 EDTalkClient 的 register_client 模式注册全局 holder，供非 Audio 上下文使用）。
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
- 字幕页地址 = `http://<webui_ip>:<webui_port>/captions`（随 webui 配置走，无独立端口）。

## 6. 模块三：前端设置页改造（基础功能分组，页已存在）

### 6.1 音频播放页（frontend/ui/tabs/audio_play.py）

1. 播放器 select 增加选项 `builtin`（label"内置播放器"）；tooltip 更新：pygame=系统自带默认 / 内置播放器=进程内队列播放（插队/暂停/续播） / audio_player(_v2)=外部服务模式（需自行启动外部项目，仅兼容保留）。
2. `audio_player` 卡片改造：
   - "内置播放器"卡：设备下拉（get_all_audio_device_info 枚举 + "系统默认"项，device_index）、播放间隔、随机间隔（开关+上下限）、优先级映射说明文案（展开显示 type→优先级表 + 编辑入口为高级折叠区，v1 仅展示）。
   - "外部服务 API 地址"输入框：选 builtin/pygame 时**隐藏**（动态显隐），选 audio_player(_v2) 时显示（保留 is_url_check 校验）。
3. 互斥提示（对齐 EDTalk 计划四组合提示模式）：`is_edtalk_active` 为真时页面顶部常驻提示"数字人模式下音频由 EDTalk 播放，播放器选择不生效"。
4. 小型控制区（卡片，选 builtin 时显示）：队列条数回显（get_list）、暂停/续播/跳过当前/清空队列四按钮——运营者手动打断用；服务未选 builtin 时显示空态"内置播放器未启用"。
5. 全部新 UI 走 design_tokens 令牌 + 3 列网格 + width:100% 样板（硬约束）。

### 6.2 web字幕打印机页（frontend/ui/tabs/web_captions_printer.py）

1. 补 `enable` 开关（现状 UI 缺失，仅有 API 地址——断链三重奏之一）。
2. 移除 API 地址输入框；新增**字幕页地址回显**（只读 + "复制地址"按钮，供 OBS 浏览器源粘贴）。
3. 新增样式配置卡（14 键：显示模式 select 渐显/打字机、字体/字号/字重/描边、文字色/背景色/页面背景色（色盘）、字幕区宽高、渐显时长/单字时长/隐藏时长/播完保留时长）。
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
| R2 | 播放器线程与 webui asyncio 交互（完成回调、线程安全） | 队列已有锁保护；回调经 run_coroutine_threadsafe 或同步 wrapper；实施首日以冒烟验证（播放+回调+停止） |
| R3 | webui 进程承载播放线程——webui 重启打断播放 | 与 pygame 模式现状一致（同为进程内线程），无回归；计划如实声明 |
| R4 | socket.io 独立挂载与 NiceGUI 内部 /_nicegui_ws 冲突 | 独立 ASGI app 挂 /captions_ws/ 前缀，path 隔离；实施首日冒烟（页面连接+推送+config_update） |
| R5 | priority_mapping 默认表与 AUTOlive type 词表不齐（源表为旧 ai_vtuber 词表） | 实施首日 grep data_json["type"] 赋值点核对词表，重写默认表；未识别 type 插队尾（源行为保底） |
| R6 | 音频时长读取失败（非 wav 产物/文件损坏） | keep_time 降级为原自动计算（len*80ms+2000ms），不阻塞播放 |
| R7 | 配置键迁移（web_captions_printer.api_ip_port 删除 + 14 键新增 + audio_player 节扩展） | 沿用既有 ensure-default/migrate 机制（EDTalk 计划确立）；缺键补默认值，防 get 返回 None |
| R8 | ffmpeg 缺失环境下 pydub 解码非 wav 失败 | ffmpeg 已是系统既有依赖（变速功能同依赖）；报错文案指向环境文档 |
| R9 | 断链修复涉及播放主循环（playback_manager）回归 | 分支结构零逻辑改动原则；回归三模式（pygame/builtin/edtalk）+ 外部模式冒烟 |

## 9. 验收标准

1. 播放器选 builtin → 运行系统：TTS 与文案音频经内置播放器播放，队列保序、priority_mapping 插队（高优先级 type 排前）、insert_index 显式插队、暂停点续播、跳过当前、清空队列全部可用。
2. 设置页控制区：队列条数实时回显；暂停/续播/跳过/清空按钮生效。
3. 设备选择：下拉枚举声卡，切换 device_index 后新播放请求走新设备（修复"写了不读"缺陷）；设备打开失败时回退默认设备并报错附设备列表。
4. web字幕打印机：enable → `http://<webui_ip>:<webui_port>/captions` 页面随音频逐句显示字幕，样式 14 键修改保存后**即时热更新**；keep_time_align_audio 开启时字幕保持至音频播完。
5. EDTalk 模式：音频仅推送 EDTalk（无本地播放、无双声），字幕页照常逐句显示（补齐原缺口）。
6. 回归：pygame 模式、外部 audio_player 模式（HTTP 客户端路径）、既有启动/停止链路均不受影响；字幕断链修复后原 AttributeError 场景消失。
7. 零新增 pip 依赖（pyaudio/pydub/python-socketio runtime312 已具备，实测 2026-09-24）。
8. 配置迁移：老 config.json 升级后所有新键有默认值，web_captions_printer.enable=false 行为零变化；coordination_program 过期条目清除后启动无报错。

## Implementation plan

（本文件即审查主体；§1-9 为实现计划。）
