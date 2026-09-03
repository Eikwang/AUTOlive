# EDTalk 实时推理服务对接纪要（T1 spike 产出）

日期：2026-09-03 | 产出自：EDTalk 侧 /autoplan 计划任务 T1（契约冻结前置走查）
契约：`D:\AI\EDTalk\realtime_serve\EMBEDDING.md`（v0，冻结）

## 1. 现状核实

- AI-Vtuber **当前无任何 EDTalk 实时服务对接代码**（全仓无 `audio/push` /
  `segment/switch` / `:8000` 调用命中）。"虚拟身体"目前仅为类型选择
  （`config.visual_body = 'metahuman_stream'`），tab 内容只有
  `metahuman_stream` 一节（`frontend/ui/tabs/visual_body.py`，NiceGUI，
  类型下拉 + API 地址输入）。
- 对接为绿地，无存量兼容负担。

## 2. 可复用的既有资产（全部实测定位）

| 资产 | 位置 | 用途 |
|---|---|---|
| 服务启动机制 | `frontend/main.py:195-236` `start_programs()` | 按 `config.coordination_program[]` 逐项 `subprocess.Popen` 拉起；停止用 `taskkill /F /T`。**EDTalk 接入启动流程 = 加一条配置** |
| 服务客户端模式 | `utils/audio_handle/audio_player.py` `AUDIO_PLAYER` | EDTalk 客户端照此模式写：requests + `api_ip_port` 配置 + try/except + logger |
| 音频播放挂钩点 | `utils/audio/playback_manager.py:147、245`（两处 `audio_player.play(data_json)`） | TTS 音频播放入口——`/audio/push_full` 推送接在这两处旁（播放与推送并行，不替代本地播放） |
| 统一环境决定 | `specs/integration/统一环境依赖整合计划.md` | 所有服务统一 `EDTalk\runtime312` 解释器；拓扑=各服务独立端口、中枢 HTTP 调用；已盘点"EDTalk 无 .bat"缺口（9.4） |
| 启动脚本缺口 | 同上 9.4 | EDTalk 侧需补 `realtime_serve` 启动 .bat（本对接顺带闭合） |

## 3. AI-Vtuber 侧改造清单（后续任务，按序）

**A1 前端「虚拟身体」tab 扩展**（`frontend/ui/tabs/visual_body.py`）
- 新增 `edtalk_realtime` 节，分两区：
  - **配置区**（存 `config.json`，`config.edtalk_realtime`）：`api_ip_port`
    （默认 `http://127.0.0.1:8000`）、启动参数（character_dir、初始 segments
    调度、face_repair / gaze 启动模式）、`webui.show_card.visual_body.edtalk_realtime`
    显隐开关（照 metahuman_stream 模式，含 `migrate_config.py`/`split_config.py` 迁移）。
  - **控制区**（直调 HTTP，实时生效，**不落 config**；控件初始值可存 config 作
    "上次控制值"，连接成功后经 API 推送还原）：
    - 视线纠正：开关（on↔adaptive↔off，409 时禁用置灰提示"需启动时启用"）
      + 收敛角滑条 [-1, 1]（POST /gaze/params）
    - 面部修复：开关（off↔on↔adaptive，409 同上）（POST /repair/params）
    - 素材片段：模式选择（顺序循环=auto / 指定列表）（POST /segment/schedule，
      默认 align=false 不打断当前播放）+ 即时切换按钮（POST /segment/switch，
      可选"当前段播完后切"= at=segment_end）
    - 状态回显：GET /status（current_segment / resolution / prefetch /
      production_fps / underruns）
- 与 metahuman_stream **互斥**（照 webui-bak.py:1496 先例给警告文案）。

**A2 启动流程接入**
- `config.coordination_program` 增加项：
  `{name: "EDTalk数字人", enable: true, executable: "D:/AI/EDTalk/runtime312/python.exe",
    parameters: ["D:/AI/EDTalk/realtime_serve/main.py", "-m", "realtime_serve.main", ...]}`。
- ⚠️ 现状 `start_programs()` 的 `cmd = [executable, app_path]` **不透传 parameters[1:]**
  ——需小改 `frontend/main.py:213` 为 `cmd = [executable] + parameters`
  （向后兼容：既有条目 parameters 只有 app_path，行为不变）。
- EDTalk 侧补 `realtime_serve` 启动 .bat（整合计划 9.4 缺口），.bat 内指向
  runtime312 解释器；或直接用上面的 coordination_program 方式，二选一。

**A3 音频链路**（核心数据面）
- 新建 `utils/edtalk_realtime/edtalk_client.py`（照 AUDIO_PLAYER 模式）：
  `push_full(pcm_path)`（读 wav → 16kHz int16 → base64 → POST /audio/push_full）、
  `switch_segment`、`set_gaze_params`、`set_repair_params`、`get_status`。
- `playback_manager.py:147、245` 两处 play 旁：若 `visual_body == 'edtalk_realtime'`
  则推送 PCM（16kHz int16；若 TTS 输出采样率不同需在此转码或确认 TTS 输出规格）。
- EDTalk 侧虚拟摄像头画面由 OBS/直播推流端消费（与 metahuman_stream 的
  "TTS 托管"模式相反：音频仍由 AI-Vtuber 管控并双路分发）。

**A4 反应段联动**（弹幕 → 表情段）
- 弹幕/回复触发点调 `POST /segment/switch`（建议 `at=segment_end` 或
  immediate 由运营配置）；挂钩点在 my_handle / 回复分发侧，**实现期定位**。
- 客户端需容忍 EDTalk 未启动（连接失败降级为跳过推送，不影响直播主流程）。

## 4. 对齐保障

- 前端控件命名与语义以 `EMBEDDING.md` v0 契约为准（单一来源），两侧并行开发
  不会漂移；服务器未完成的部分前端可用契约 mock。
- 409 语义（gaze/repair 启动 off）→ 前端先 GET /status 读 `{mode}_capable`
  再渲染开关可用态。
- 默认回环绑定对 AI-Vtuber 无感（同机 127.0.0.1 直连）。

## 5. 待办决议记录

- 面部修复开关 → 运行时热切（用户裁决 2026-09-03，EDTalk 侧 M23）。
- **TTS 输出采样率 = 16kHz（用户确认 2026-09-03）** → A3 无需转码，
  直接按 16kHz int16 推 `/audio/push_full`。
