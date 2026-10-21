# TODOS.md

## Deferred Items from 门1验证 (2026-10-21) — 清理批次 pytest 既有失败

1. **test_t5_key_mapping 5 项既有失败**（P2, S）— 【2026-10-21 已修复，提交 9d6eedf4：TestMatching 4 项 key_trigger_type 置"无"隔离 key 分支环境依赖；serial 测试 monkeypatch get_serial_manager 模拟缺依赖；纯测试侧修复产品零改动，全套 735/73/0】TestMatching×4（test_danmu_similarity_trigger_copywriting/test_reply_contains_trigger/test_gift_exact_match/test_start_cmd_stripped）+ TestSerialSafety::test_serial_send_requires_pyserial。现象：key 模式走真实 pyautogui 按键分支（pyautogui 已装），audio_synthesis_handle mock 未被调用；serial 测试期望 pyserial 缺失降级但 pyserial 已装。根因方向：测试设计依赖"pyautogui/pyserial 缺失"环境或 mock 边界未覆盖真按键分支，与清理无关（git stash HEAD 对比同败，留痕 .gstack/tmp/autoplan/batch1-verify.md）。依赖：无。

## Deferred Items from /autoplan (2026-10-21) — 死代码清理批次（批准实施，rec）

1. **批次 1b：utils/*.backup 退役**（P2, S）— 【挂起，uc2b 裁决】backup diff 验证已通过（2026-10-21，留痕 .gstack/tmp/autoplan/diff-*-backup.txt + batch1-verify.md：T5 按键映射已迁 feature_handler.py、T6 答谢三通道已迁 timer_handler.py，46 个独有函数逐一核对无迁移缺失）；删除须待 T5-T9 恢复产物经用户实测确认后执行（终门 uc2b：尚未实测）。对象：utils/audio.py.backup、utils/db.py.backup、utils/my_handle.py.backup。依赖：用户实测确认。
2. **仓库卫生门禁**（P2, M）— 将本批 grep 门推广为批次验证模板（pyautogui 式两态白名单+git grep 全跟踪扫描+活模块 import 探测）；backup/*.bak 类后缀进 lint 拦截（CI/本地 pre-commit 均可）。来源：CEO 审查 F1"一次性清理无防再脏机制"。依赖：无。
3. **.github/ 上游 issue 模板退役评估**（P3, S）— 硬 fork 后上游 issue 模板无意义但无害；评估是否随仓库品牌化删除/改写。依赖：无。
4. **log/ vs logs/ 双日志目录合并评估**（P3, M）— 两目录并存（log/=运行时日志含 README 跟踪、logs/=服务日志未跟踪），合并需迁移路径引用，超清理批次 30min 界。依赖：无。
5. **Scripts 诊断工具归档注释统一**（P3, S）— bench_fastrag_latency/diagnose_danmaku_chain/smoke_builtin_captions 三工具加统一头部注释（用途/前置/调用方式）；注意改动会破坏文档路径引用需同步。依赖：无。
6. **specs/ 历史计划档案归档标注**（P3, S）— specs/integration/ 11 份历史计划档案加"已归档"头部标注，与活文档区分。依赖：无。
7. **DX-R3 注记**（P3, S）— Scripts/半自动/ 已于 2026-10-21 清理批次删除，conda 环境名分裂问题（specs/技术栈.md autolive vs 脚本 ai_vtb）仅剩文档侧；统一方案收敛为"文档示例改 ai_vtb"或在下次动 conda 环境时一并处理。

## Deferred Items from /autoplan (2026-10-20) — 主线流程图审查（图-码对齐+断链登记）

1. **T10 语音对话链接通**（P2, L）— conversation_manager.py:221-240 do_listen_and_comment 为空框架（循环体仅 sleep），audio_listen 无调用者，ASR（funasr/whisper）未接入对话链。触发条件锚定：T5-T9 断链批次落地后下一批（不无限期）。入口：conversation_manager 框架填充+ASR 接入+全链手动实测。
2. **写库/过滤顺序语义决策**（P3, S）— 现状弹幕原文先写 danmu 表（comment_handler.py:255-260）后过违禁词过滤（:272-288），数据分析读到含违禁词原文。变更影响数据分析口径，需用户裁决：保持现状（入库原文）vs 写库前过滤 vs 入库+标记列。**与下方 A4 交叉**：badwords 文件缺失期间违禁词过滤全链失效，写库内容=未过滤原文。依赖：无。
3. **前提表→运行时冒烟断言**（P2, M）— 26 条 F 前提（流程图-代码对照表，docs/flowchartTD.md Review record 0A 节）转化为运行期可验证检查（开关存在/路径可达/出边有消费端），把流程审计投入复利化；含 ENG E-1 队列深度/丢弃计数可观测。**A1-A4 实测错误即"冒烟断言"价值的直接例证**（代码在位≠实际生效：违禁词文件缺失过滤静默失效、变声链配置残留每弹幕刷错）。依赖：T1/T2 修图完成后。
4. **my_handle.py.backup 退役清理**（P3, S）— 【2026-10-21 已由清理批次 1b 接管，diff 验证通过，见上方 2026-10-21 节第 1 条；删除仍待用户实测确认】T5-T9 断链迁移完成后，backup 文件（含 key_mapping_handle:2225/答谢:3077-3137 等唯一实现副本）使命结束；确认迁移完整后删除或归档。依赖：T5-T9 批次验收。
5. **"改链必改图"纪律全仓库推广**（P3, S）— 已作为 T5-T9 批次门禁（每任务 Verify 含图更新）；推广为全仓库轻量流程：改消息链/播放链代码的提交须同步检查 docs/flowchartTD.md 对应节点/边。依赖：T1/T2 修图完成后生效。
6. **流程图与实测发现的交叉注记**（P3, S）— 下方 A 组实测错误对流程图四态标注的影响：F8 过滤链在 badwords 缺失期间应标"实际失效"（代码在位但文件缺失）；变声通道 so_vits_svc（synthesis_manager.py:278-301）每弹幕触发 ConnectionRefused 说明播放链存在图中未画的变声分支且其配置处于残留开启态。T2 修图时一并消化。

## Deferred Items from 环境实测 (2026-10-20) — 系统审查批次实测修复轮（用户指示：先记录，架构调整后统一修复）

### A 组：弹幕链刷屏错误（每条弹幕触发，真实直播间实测发现）

1. **so_vits_svc 变声链每弹幕 ConnectionRefused**（P2, S）— 实测：每条弹幕音频合成后触发 `synthesis_manager.voice_change → so_vits_svc_api`，连接失败报错刷屏（`so_vits_svc合成失败，请检查配置`）；疑似 config 残留变声开启但服务不存在，或触发条件缺陷（不应无条件走变声链）。修复=查明触发条件（config 开关/调用点判定）+关闭残留开关或修条件。注意与 RVC 翻唱链的关系辨析（变声 vs 翻唱两链）。
2. **多音字.txt 相对路径 Errno 2**（P2, S）— TTS 前处理多音字词典以相对路径打开（`[Errno 2] No such file or directory: '多音字.txt'`），工作目录依赖：非仓库根启动即失效。修复=路径改为相对仓库根锚定（utils.autolive_paths 语义）或确认词典文件入库位置。
3. **luna_ai 写弹幕日志 'NoneType' .get 失败**（P2, S）— 数据库写入路径某 dict 为 None（`写入弹幕日志失败：'NoneType' object has no attribute 'get'` 每弹幕报），弹幕日志功能实际失效。修复=定位 None 来源（大概率 config 节缺失未兜底）。
4. **data/badwords.txt 违禁词文件缺失**（P2, S）— config `badwords.path=data/badwords.txt` 指向文件不存在，每次弹幕 Errno 2，违禁词过滤实际未生效。修复=补文件（含违禁拼音表 data/违禁拼音.txt 同查）或 config 判空跳过。

### B 组：批次收尾验证项

5. **P25 多参考音频音色人耳判定**（P2, S）— 合成已出音频（`out/gpt_sovits_inproc_*.wav`），用户试听确认多参考音色质量即裁决（spike 收尾）。
6. **P26 双语速时长对比**（P3, S）— synth_speed 透传链路已通；跑 synth(1.0) vs synth(2.0) 时长对比验证生效性（spike 收尾，pydub 回退已保底）。
7. **gptsovits S1/S2 两断言存量失败**（P3, S）— test_gptsovits_deep_integration.py 两项失败；config 键恢复实验已否定键假设，待查（引擎 mock 态/单测环境 GPU 依赖方向）。

### C 组：批次遗留小项

8. **utils/*.backup 文件处置**（P3, S）— 【2026-10-21 已由清理批次 1b 接管（diff 验证通过，删除待用户实测确认，见 2026-10-21 节第 1 条）】删除批后遗留的备份文件清点与删除。
9. **D-15 首启"更新移除项"告知 UI**（P3, S）— 首次启动告知用户本批移除的模块/键面，避免旧配置静默失效无感知。
10. **v2 backend=api 兜底数据链退役评估**（P3, S）— inproc 为主后，api 兜底路径的数据链是否随键迁移一并退役评估。

## Deferred Items from /autoplan (2026-10-15) — EDTalk 全面整合（正式接入批次）

1. **片段编排能力**（P2, L）— 用户指示保留待办：realtime_serve 段播放调度增强（含 reaction_segment 弹幕→切段联动——配置键/UI/switch_segment 方法在但无回复分发点调用方，前端调查发现缺口）。依赖：本期推理模块托管+热切落地后评估。
2. **训练断点续训增强**（P2, M）— 步骤级重跑（进度文件+start_stage）已兜底，本项为编排层状态记录增强（迭代级 checkpoint resume）。train_fine_tune/train_audio2mouth 原生支持 resume_ckpt，主要工作=编排层状态机。依赖：本期训练链路稳定。
3. **多角色管理/训练队列**（P3, L）— 单角色全链路先落地；多角色需角色目录约定（名称派生表扩展）与训练排队 UI。依赖：第 2 条。
4. **音频感知段边界切换**（P3, M）— 上游 EDTalk TODOS 既有项：播完当前音频再切段（替代按片段周期切）。依赖：上游音频播放优化。
5. **EMBEDDING v0 缺口登记项**（P3, M）— 上游已登记：多角色 load_character/事件推送/参数持久化/gaze·repair off→on 懒初始化。依赖：上游演进。
6. **解释器副本内 venv 方案评估**（P2, M）— F3 结构债带日期承诺（2026-Q1 前）：评估在 AUTOlive/edtalk 内建 venv 或首启引导创建，消除对 D:\AI\EDTalk\runtime312 的残余耦合。依赖：本期"唯一副本"纪律落地稳定后。
7. **GPU 时间片方案**（P3, M）— F7 长期缓解：训练与直播的时间片调度（错峰自动化/资源预留），当前靠双向互斥+错峰引导。依赖：多角色队列。
8. **训练四阶段接口化**（P3, M）— F2/F12 架构注记：preprocess→底模→评估→口型的可插拔接口，未来换口型引擎（MuseTalk/LatentSync 等快变量）时前端与编排复用。推理侧已有 EMBEDDING v0 契约，训练侧补齐等价边界。依赖：本期训练管线稳定运行一个版本。
9. **虚拟摄像头选型分析**（P3, S）— F10：UnityCapture vs OBS Virtual Camera/pyvirtualcam/共享内存对比登记，以 F6 脆弱性（DirectShow 老滤镜+单读者）为重评触发条件。本期胜出理由=集成成本最低。
10. **词表分离上游化 PR**（P2, S）— 既有项（2026-10-14 登记）：AUTOlive 的 send_blocked_keywords 分离提回上游 DanmakuListener。

## Deferred Items from /autoplan (2026-10-14) — DanmakuListener v0.8.0 模块更新

1. **词表分离上游化 PR**（P2, S）— AUTOlive 的 send_blocked_keywords 词表分离（8926b9fe）是通用需求而非 AUTOlive 特有，提回上游 D:\AI\DanmakuListener 收编后永久消除该补丁的每轮重放债（v0.7.0→v0.8.0 已重放两次）。依赖：v0.8.0 同步落地后执行；落点=上游 wiring.py SendGuard 构造处支持 settings.send_blocked_keywords + 上游测试；同时维护 docs/danmaku_module.md 本地补丁台账作为未上游化期间的核对清单。

## Deferred Items from /autoplan (2026-10-12) — 改动盘点与文档刷新

1. **全面测试执行**（P1, M）— 整合浪潮（76 提交）后的系统级验证。**退出标准**：全链路冒烟通过（冷启动+弹幕监听+对话链+数字人+字幕）+ tests/features 29 文件入库裁决完成。测试入口清单见 [docs/开发记录/2026-10-12_整合阶段改动盘点.md](docs/开发记录/2026-10-12_整合阶段改动盘点.md)「全面测试准备清单」节。依赖：无（本批文档已就绪）。
2. **CHANGELOG.md 创建**（P2, S）— 盘点报告是一次性快照，持续记录需机制。**触发时机：下个整合批次落地时**，以该批盘点报告为底稿建 Keep-a-Changelog 格式。
3. **PNG 架构图**（P3, S）— README/拓扑文档的 ASCII 图已覆盖信息；渲染图需图形工具链。依赖：无。
4. **各整合项目模块级独立文档**（P3, M）— 音频三项目/EDTalk/字幕播放器各建 docs/ 模块文档（弹幕已有 danmaku_module.md 范本）；盘点报告已承担浓缩角色。依赖：全面测试完成后按实测反馈写。
5. **上游季度分诊巡检**（P3, S）— hard fork 独立演进后仍需季度 diff 分诊（Ikaros-521/AI-Vtuber 上游），决定取舍并记录漂移成本。依赖：无；随季度窗口执行。

## Deferred Items from /autoplan (2026-09-24) — EDTalk 功能集成

1. **训练产物→推理服务自动接线**（P2, M）— CEO 阶段延期：训练主播产出 audio2lip ckpt 后自动替换实时推理角色权重。依赖：本期训练链路（train_streamer pipeline）落地并稳定。落地时注意 ckpt 格式（audio2lip 单独键 vs 完整格式，见 EDTalk 记忆「部署注意」）。
2. **多角色管理/训练队列**（P3, L）— CEO 阶段延期：单角色全链路先落地；多角色需角色目录约定与训练排队 UI。依赖：第 1 条。
3. **训练断点续训**（P2, M）— CEO 阶段延期：长训练（底模微调+口型微调累计 ~15h）中断后从最近 checkpoint 恢复；当前为步骤级失败即停。train_fine_tune/train_audio2mouth 原生支持 resume_ckpt，主要工作是编排层的状态记录与 UI。

## Deferred Items from /autoplan (2026-09-24) — 项目更名为 AUTOlive

1. **conda 环境名文档-脚本分裂统一**（P3, S）— DX 阶段发现（DX-R3）：specs/技术栈.md 的 conda 示例用 `autolive`（改名后为 `autolive`），而 Scripts/半自动/1.创建虚拟环境.bat 实际创建 `ai_vtb` 环境——上游既有的文档/脚本不一致，改名后依然存在。统一方案二选一：文档示例改 `ai_vtb` 对齐脚本（零风险），或脚本+文档统一改名并重建环境（破坏性，需用户重装环境）。依赖：无；建议在下次动 conda 环境时顺手处理。

## Deferred Items from /autoplan (2026-09-23) — 前端导航栏增强 + 菜单重排

1. **启停操作日志回显到前端日志区**（P3, S-M）— CEO 阶段 cherry-pick C3：ui.notify + logger 已覆盖反馈闭环，日志区回显属增强非缺口。依赖：前端日志区数据通道形态。
2. **main.py 子进程退出监控（崩溃自动复位 running_flag）**（P2, S-M）— CEO spec 审查 R3 问题2 (b) 路线：后端无子进程退出监控，子进程崩溃后 running_flag 恒 True（启动按钮持续禁用）。当前恢复路径 = WebUI「重启」按钮（停止阶段捕获死 PID 异常并清理后重新拉起）。落地时注意与主动停止的竞态（监控复位需排除 stop 进行中）。依赖：本批启停按钮任务落地后评估。**CEO 备注（native voice 发现1，2026-09-23）**：无人值守直播场景下升级为下一阶段 P1 候选——深夜长播中 LLM/TTS 子进程静默崩溃 = 直播间黑场数小时；最小实现 = ui.timer 顺带查 PID 存活 + auto_run 式自动重拉；C6 状态灯（本批任务1）是崩溃告警的天然挂载点。
3. **/sys_cmd 重启语义统一**（P3, S）— native voice 发现3：D-02 后并存两套「重启」语义（WebUI 按钮 = 系统级 stop+run；/sys_cmd restart = webui 自重启 os.execl）。本批在 /sys_cmd 处理函数补注释标注差异；语义统一/废弃旧自重启待后续裁决，防止语义分叉固化为永久债务。
4. **重启确认记忆**（P3, S）— 设计审查发现9：auto_run 部署下重启是日用高频操作，每次过确认框有摩擦。v1 维持 C2 确认（防误触裁决不翻案）；后续可加「本次会话不再询问」记忆选项。
5. **前端 ARIA/屏幕阅读器增强**（P3, S）— 设计审查 Pass 6：Quasar 自带基础 ARIA 兜底；确认对话框的屏幕阅读器语义、导航区 landmark 标注待增强。依赖：本批落地后随 /design-review 实测评估。
6. **webui 网络暴露面收敛**（P2, S）— eng 审查安全附注：webui 默认绑定 0.0.0.0（main.py L874）且 /sys_cmd 的 run/stop/restart/factory 无鉴权——局域网任意主机可启停系统/覆写配置；本批新按钮使该能力显性化。建议：默认绑定改 127.0.0.1 或为 /sys_cmd 加 token。依赖：与使用场景确认（是否需要局域网访问）。
7. **running_flag check-then-act 竞态窗口**（P3, S）— eng F10：main.py L278/L284 之间存在 TOCTOU 窗口（UI executor 线程与 /sys_cmd async endpoint 理论可并发）；单机自用风险极低。建议后续用 threading.Lock 或单一调度路径收口。
8. **config.json.bak 缺失致 factory 必炸**（P3, S）— eng F11：/sys_cmd factory 依赖根目录 config.json.bak，仓库当前不存在该文件，恢复出厂必失败。与「重启按钮是恢复路径」叙事相邻，修复 = 生成/维护该备份或移除 factory 入口。
9. **测试资产入库**（P2, S）— 实施发现：.gitignore:286 的 `test_*.py` 规则使整个测试套件（42+ 文件）不进版本库——改写后的 test_task_16_new.py 也无法提交，测试改动在协作间丢失。建议：收窄该规则（如仅忽略报告文件）并把 tests/features 入库。

10. **functions[].enabled 僵尸数据退役**（P3, S）— batch2 CEO native 发现6：二级开关迁移后该字段退出消费，43 个 enabled 全 True 与实际业务状态不符，遗留误导。后续以 migrate 脚本一次性删除或注释 deprecated（确认 validate_integration 等无依赖）。

## Deferred Items from /autoplan (2026-09-22) — runtime312 启动脚本切换

1. **api_old.py 与 flask_socketio 处置**（P3, S）— 旧 API 入口是否保留/归档；flask_socketio 仅其使用（runtime312 未装）。全仓库依赖审计发现，非两 webui 入口所需。
2. **users.pth 注入升级为整合正式议题**（P2, S）— runtime312 的 `Lib\site-packages\users.pth` 硬编码注入 6 个 GPT-SoVITS 路径进所有进程，存在模块遮蔽风险；启动脚本 sys.path 断言只是临时保险，应从源头移除或收编管理。
3. **EDTalk 目录硬耦合的结构性解耦**（P3, M）— AUTOlive 启动命脉指向 `D:\AI\EDTalk\runtime312`，EDTalk 挪动/重装即断；环境复制/软链/搬迁方案待评估。启动预检已缓解。
4. **UC-1 裁决联动**（P2, —）— 【已裁决 2026-09-22：B 带警告切换】1.bat 已照新模板改写并带警告头；webui-bak.py 的 nicegui 3.x 适配债保持挂起（与下方第 1 条 2026-09-02 项合并推进）。2 周退役线时复核是否升级为归档。UC-2 已裁决采纳导入守卫（zhipu.py/gemini.py 已落地），google-genai 迁移（选项②）未排期。

5. **utils/__init__.py 惰性化（PEP 562 `__getattr__`）**（P2, M）— 当前巨石顶层导入链（web_server→my_handle→gpt→zhipu/gemini）使可选 LLM 通道包成为所有入口的硬依赖，本次两类启动阻断皆源于此。守卫只是止血，惰性化才是根因修复；否则下一个可选通道包还会复现同类阻断。（Eng A1，2026-09-22）

## Deferred Items from /autoplan (2026-09-02) — 六项目统一环境整合

1. **gradio/pydantic 版本 API 差异适配**（P1, M）— 统一环境里 gradio 只能装一个版本、AUTOlive 需从 pydantic 1.x 适配 2.x。待统一环境落地后作为"后期适配"第一优先。阻塞：统一环境补装完成。
2. **uv 化重建单一可复现环境**（P2, L）— 用 pyproject+lock 替代 conda+裸 pip 的 379+ 包混合环境（conda 导出 requirements 不可靠）。待统一环境稳定运行 1-2 个月后评估。
3. **opencv 三变体收敛为 opencv-contrib-python**（P3, S）— 当前 opencv-python/-contrib/-headless 并存互覆盖；收敛需先冒烟 EDTalk 推理链。
4. **开发/测试依赖与运行依赖分文件**（P3, M）— 统一环境稳定后整理。
5. **start_all.bat 顶层编排一键启动**（P2, M）— 弹幕→中枢→TTS→数字人全链路一键拉起+健康检查；与 UC-1（编排优先还是环境优先）裁决联动。

## Deferred Items from /autoplan (2026-08-31)

### From CEO Phase
1. **自定义布局支持** — 需要新的配置系统和 UI 组件，超出当前优化范围
2. **配置导入导出功能** — 独立功能，与布局优化无关
3. **拖拽重排功能** — 复杂交互，需要仔细设计

### From Design Phase
4. **高级动画效果** — 复杂交互， defer to future iteration
5. **实时预览功能** — 复杂功能，需要新的架构

### From Eng Phase
6. **虚拟滚动优化** — 性能优化，当列表很大时需要
7. **单元测试覆盖** — 测试基础设施，需要补充配置页面和集成测试

## Priority Notes
- P1 tasks (简化导航栏、优化卡片设计、改进配置页面) are IN SCOPE and ready for implementation
- P2 tasks (搜索功能、主题切换、过渡动画) are IN SCOPE and ready for implementation
- P3 tasks (颜色字体统一、响应式布局、加载状态) are IN SCOPE and ready for implementation
- All items above are DEFERRED and should be addressed in future iterations

## Deferred Items from /autoplan (2026-09-25) — audio_player/captions_printer 整合

1. **外部播放器模式 sunset**（P3, S）— CEO F4：audio_player/audio_player_v2 外部模式在 builtin 稳定运行一个版本后从 UI 选项与 HTTP 客户端移除。移除判据：builtin 连续一个版本无 P1/P2 缺陷 + 用户确认不再使用外部服务。依赖：本期 builtin 落地并稳定。
2. **EDTalk 回调驱动字幕**（P3, M）— CEO F3/R10 条件触发：仅当首日冒烟实测 EDTalk 模式字幕偏移明显（系统性超前 >500ms）时启动——让 EDTalk 端在开口时回调触发字幕显示，替代本地 dequeue 推送。依赖：EDTalk 契约是否提供音频开始播放回调端点。

## Deferred Items from /autoplan (2026-09-25) — 音频三项目整合

1. **训练完成后自动切换生产声音档案**（P2, S）— CEO 阶段延期：训练声音页产出档案后自动切换 TTS 生产声音。依赖：P2-4 voice_profiles 选择器落地。运行中换模机制定案后再评估自动化。
2. **翻唱预生成热门歌单**（P3, M）— CEO 阶段延期：提前生成常用曲目避免直播等待。依赖：P3-2 翻唱链路稳定运行。

## P3 退役批次执行记录（2026-10-07，提交 efdee3b7）

用户实测通过（配置期登录/房间管理/关键词全链路）后执行：utils/platforms/ 12 模块、根目录旧资产（api_old.py/webui-bak.py/main.py.backup/proto+pb2/protobuf//直播ws脚本/启动程序1.bat/dy2.py）、config.json 旧平台节（bilibili/twitch/ordinaryroad_barrage_fly/platform/room_display_id）全部删除；test_config platform 断言（独立 fixture）不受影响；main.py TaskScheduler fallback 改 'talk'。177 项测试全绿。

## Deferred Items from /autoplan (2026-09-27) — DanmakuListener 弹幕监听整合

1. **差异化假设书面化与付费理由验证**（P2, M）— CEO 原生声部发现 5.1：壁垒=整合体验+稳定性运营+合规自托管，需书面化并经阶段 0 客户验证是真实付费理由。依赖：阶段 0 客户校验（本整合 P1 并行项，用 listen --replay demo.jsonl 链路演示）。
2. **draft 平台固定校准节拍**（P3, S）— CEO 原生声部发现 5.2：为虎牙/快手/视频号 draft 状态设每平台两周抓包校准槽位，客户参与追赶优先级排序。依赖：组件侧（DanmakuListener）协作节奏约定。

## Deferred Items from /autoplan (2026-10-06) — DanmakuListener 进程内整合

1. **S7 多房间对话链扩展（2026-10-07 用户裁定语义变更：多房间全监听全互动已实现——活跃概念退役，本条收敛为多房间消息归账/答谢计费的精细化）**（P2, L）— Bridge 原生支持多房间监听（已落地：非活跃房间仅计数+行内明示"仅监听不互动"），但 my_handle 对话链为单直播间架构——多房间全量互动需产品级设计（消息路由/答谢计费/开播状态机多房间化）。rooms[].active 显式指定为正交扩展点。
2. **S9 商用合规与隔离形态重评**（P1, M）— 商用签约前置：① 重新评估进程内隔离形态（引擎硬崩溃会带走数字人——回退方案 B：修复 serve 接线的托管子进程，见整合计划第 3 节 A/B/C 决策记录）；② 专用监听账号体系（严禁客户主账号）；③ web_console 无鉴权面复核。个人使用阶段不受阻。
3. **~~edtalk_realtime 配置加载失败~~（已修复 2026-10-12）**——根因=通用配置页 visual_body select options 漏列合法运行态值 edtalk_realtime（EDTalk 整合设置）；common_config.py visual_body_options 已补该选项。
4. **~~NiceGUI 3.16 "消息超过 WebSocket 大小限制"断联~~（已修复 2026-10-12）**——根因=nicegui/nicegui.py:52 硬编码 AsyncServer 无 buffer 配置通道（默认 1MB）；frontend/main.py 启动前猴补 socketio.AsyncServer 传 max_http_buffer_size=10MB（python-engineio snake_case 参数，实证 eio.max_http_buffer_size=10485760 生效）；NiceGUI 升级开放官方透传后移除补丁。

## Deferred Items from /autoplan (2026-10-11) — DanmakuListener v0.7.0 发送整合

1. **~~O1 AUTOlive UI 呈现 ENGINE_STATUS 登录事件~~（已完成 2026-10-12）**——engine 事件通知文案落地（danmaku_login.py _NOTIFY_TEXT 补 "engine" 条目，detail 全文透出 login.first_login 等登录引导）；事件流透出与 notify 映射此前已在位。
2. **O2 原生发送按钮+批量队列节奏**（P2, M）— **第一阶段已落地（2026-10-12，用户指令提前开工）**：AUTOlive webui 平台配置页"弹幕发送"卡（在听房间下拉+token 本机缓存+发送/对账按钮，回执四态呈现）；控制面 POST /danmaku/send + /danmaku/send-results + /danmaku/send-status（进程内直调管线，鉴权同组件 Bearer 语义）；同批修复两个进程内 wiring 缺口（组件 settings 从 config.json 灌 [send] 键+ws_token_file；web.app 全局 _bridge 对齐 AutoliveBridge——缺一则发送恒 503/ROOM_NOT_LISTENED）。端到端验证：401→dry_run→对账全通。**剩余（触发条件=浸泡两周无异常）**：批量发送队列节奏+原生 UI 批量形态（开工前重校 send_min_interval_seconds；批量同公告发多房间需契约层 room 维度或错峰发送——R39 全局去重会误杀同秒同内容第二条）。
3. **~~F6 指南 vs 实测修正 checklist 沉淀+季度同步窗口~~（已完成 2026-10-12）**——docs/danmaku_upgrade_checklist.md：前置判据+六类失真实录（键表漏列/依赖声明失真/文件清单漏列/表述过时/示例误导/大小写失真）+数据迁移纪律+升级验证顺序+季度窗口约定。
4. **DX 埋点（TTHW 自动度量）**（P3, S）— POLISH 裁决不强加；随 O2 原生发送层考虑。
5. **契约层 room 维度扩展**（P3, M）— R39 去重的根治方案（上游契约变更）；O2 批量场景开工前评估。依赖：上游协作。

## Deferred Items from /design-review (2026-10-15) — EDTalk 整合三页面视觉审查

1. **uploader 空列表黑色死区**（polish, S）— NiceGUI ui.upload 空态文件列表渲染为黑色矩形（训练页/llm 页两处）；改全局 .q-uploader 样式影响面超本批，单独任务处理（如空态高度收敛+虚线框提示）。
2. **长值输入框截断无省略号**（polish, S）— 知识库目录等长路径值在固定宽度输入框被硬裁剪（如 `edtalk/fastrag/knowledgeba…`），无 ellipsis 视觉提示；统一输入框 text-overflow 行为。
3. **侧边栏主题切换按钮语义歧义**（polish, S，存量非本批）— label 显示当前模式（深色模式下显示"深色模式"）而点击执行切换，状态/动作语义混用；建议改为"切换到浅色模式"式动作文案。
4. **三段式错误前缀 UI 格式化**（note, M）— `problem=/cause=/fix=` 机器格式直出用户界面（训练校验/摄像头重测等本批多处一致，属 /autoplan 义务口径）；如需人话排版另立任务统一改渲染层。
5. **间距/字号令牌 Tab 层零引用**（仓库级既有债, M）— /design-review 子代理审计：三 Tab 文件对 --spacing-*/--font-size-* 零引用、全内联 px，与 tts/train_voice/svc 老基线一脉相承；系统性收敛属全局任务（本批数值多贴近档位、视觉不发散，不构成回归）。
6. **三段式消息渲染面与覆盖面裁决**（M）— train/llm 走 toast、visual_body 落行内标签双通道并存，且同类错误约半数为自由文本（"获取失败/服务未连接"）；与延期项 4（三段式 UI 格式化）合并：统一渲染通道+格式化+覆盖清单。

## Deferred Items from E2E 出画排障 (2026-10-16) — EDTalk 推理整合收官

1. **~~全量测试套件 149 项存量失败（features 历史验收测试漂移）~~（已完成 2026-10-20）**— 门禁 A 批次（74b932d1）150 失败→0：asyncio.run 根因修复×4+独立路径防竞争+按现行功能现实重写/退役过期断言，最终套件 619 passed + 72 retired skip。
2. **虚拟摄像头"已安装"态无重装/改名入口**（P3, S）— visual_body 摄像头卡仅在未安装态渲染安装按钮；改名/重装需手动 regsvr32。旧安装 dll 指向 D:\AI\EDTalk\UNITYC~1（仍有效），清理该目录前须先重装（安装件在 edtalk/UnityCapture-master/Install/）。
3. **test_cover_queue GPU 环境依赖**（P3, S）— test_gpu_policy_returns_tuple 断言拒绝理由含"显存"，realtime_serve 运行时显存被占导致失败；测试应 mock GPU 查询或标注 needs-idle-gpu。
