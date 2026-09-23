# TODOS.md

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
