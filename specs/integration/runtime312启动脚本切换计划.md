<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Ikaros-521-AUTOlive\\main-autoplan-restore-20260922-165941.md" -->
## Implementation plan
# AUTOlive 启动脚本切换 runtime312（Python 3.12）+ 依赖缺口清单

> 生成日期：2026-09-22 ｜ 任务来源：用户指令（/autoplan）
> 目标：将 `启动程序.bat` / `启动程序1.bat` 从 Miniconda3（activate.bat 激活）切换为
> `D:\AI\EDTalk\runtime312`（Python 3.12.12，**无 activate.bat**，直接解释器启动），
> 分别运行 `webui.py` / `webui-bak.py`；缺失依赖**不安装**，调查使用方模块并列清单由用户手动补装。

## 1. 背景与现状

- 2026-09-02 已完成六项目统一环境整合（见 `specs/integration/统一环境依赖整合计划.md`）：
  runtime312 补装 ~130 包、冒烟 42/43、统一启动脚本建于 `D:\AI\start\`（并行期模式，旧 .bat 未动）。
- 本次为用户明确指令：**直接修改 AUTOlive 自有的两个启动脚本**（不再走并行期新建模式），
  并产出依赖缺口清单。
- 旧环境：`D:\AI\AUTOlive\Miniconda3`（Python 3.10.11）；新环境：`D:\AI\EDTalk\runtime312`
  （Python 3.12.12，conda 环境但 `Scripts\activate.bat` 已不存在——实测验证，grep 无结果）。

## 2. 调查结果（关键事实，均已实测）

### 2.1 启动方式（B6 修订）

runtime312 是 conda 环境（conda-meta 存在）但 **activate.bat 缺失**（本次实测确认）。
可用启动模式 = 直接调用解释器 + PATH 前置，先例为 2026-09-02 的
`D:\AI\start\start_autolive.bat`（42/43 冒烟使用同一模式）。

**完整可照抄模板**（DX 审查 F8/F9/F10 采纳：UTF-8 无 BOM 保存 + chcp 65001 防中文乱码、
RT 可环境变量覆盖、报错现场带修复指引）：

```bat
@echo off
rem AUTOlive 中枢 UI（nicegui，端口 8086）—— runtime312 统一环境版
rem 自动化/无人值守场景可删除末尾 cmd /k（窗口保活仅为双击场景设计）
chcp 65001 >nul
if not defined RT set "RT=D:\AI\EDTalk\runtime312"
if not exist "%RT%\python.exe" (
  echo [预检失败] 未找到解释器: %RT%\python.exe
  echo 请检查 D:\AI\EDTalk\runtime312 是否存在（EDTalk 挪动/重装会导致此路径失效）
  pause
  exit /b 1
)
set FFMPEG_PATH=%RT%\ffmpeg\bin
set PATH=%RT%;%RT%\Scripts;%RT%\Library\bin;%FFMPEG_PATH%;%PATH%
SET KMP_DUPLICATE_LIB_OK=TRUE
SET HF_ENDPOINT=https://hf-mirror.com
cd /d %~dp0
echo 如启动报 ModuleNotFoundError，请按 specs/integration/runtime312启动脚本切换计划.md 第 2.3 节清单补装依赖
"%RT%\python.exe" webui.py
cmd /k
```

要点：原 bat 的 `FFMPEG_PATH`（已指向 runtime312\ffmpeg\bin）与 `KMP_DUPLICATE_LIB_OK=TRUE`
保留；`HF_ENDPOINT` 为**新增**（承 start\ 模板）；`CONDA_PATH`/`CALL activate.bat` 移除；
`%~dp0` 定位消除对工作目录的依赖；`chcp 65001` 保证中文 echo 不乱码（**文件须以 UTF-8 无 BOM 保存**）；
`if not defined RT` 允许临时覆盖解释器路径（并行测试其他 runtime）。

### 2.2 启动期实测（迭代桩探测，权威结论）

探测方法：干净子进程逐轮只桩"已证实阻断"的模块，直到引导通过
（`specs/integration/install/boot_probe_iter.py`，桩集合 = 启动阻断清单）。

| 入口 | 结果 | 阻断点 |
|---|---|---|
| `webui.py` | 桩 zhipuai + google.generativeai 后 **BOOT_OK** | 仅缺下方 2 个启动阻断依赖 |
| `webui-bak.py` | 依赖补齐后仍失败 | **nicegui 3.16.0 API 不兼容**（非依赖问题，见 2.3-C） |

导入链：`utils/__init__.py:6` → `web_server.py:13` → `my_handle.py:28` →
`gpt.py:15/16` → `zhipu.py:1`（import zhipuai）/ `gemini.py:1`（import google.generativeai），
两处均为**无 try 保护的模块顶层导入**，因此一个包缺失即挂掉两个入口。

### 2.3 缺失依赖清单（核心交付；不安装，由用户手动补装）

全仓库 174 个 .py 审计 + 二级点分名核查（防 `google.*` 命名空间假阳性），权威结论：

**A. 启动阻断（webui.py 与 webui-bak.py 都起不来）**

| 包 | 旧环境版本 | runtime312 现状 | 使用方 | 安装说明 |
|---|---|---|---|---|
| zhipuai | 2.1.4.20230814 | 未安装 | utils/gpt_model/zhipu.py:1,31,35（智谱AI 通道） | **已 dry-run 实证**（2026-09-22，带 constraints）：干净解析，装 zhipuai 2.1.5.20250825（2025-08 新版）+ PyJWT 2.8.0，不触碰基线包。命令见第 3 节 |
| google-generativeai | 0.3.1 | 未安装（2026-09-02 被 constraints 拦截） | utils/gpt_model/gemini.py:1（Gemini 通道） | ⚠️ **冲突包（弃用旧 SDK，官方已由 google-genai 取代）**：旧 SDK 硬性要求 protobuf 5.x，与 pb2 保护约束 protobuf==7.35.1 冲突（api_diff_backlog 已登记）。需用户三选一：① 不装、代码加保护把 Gemini 通道降级为可选；② 迁移到新 google-genai SDK（装包+代码适配）；③ **禁止**——无视约束强装会静默破坏弹幕 protobuf 解析（运行期才爆，属最高危失败形态） |

**B. 功能级缺失（不阻断启动，对应功能运行时报错）**

| 包 | 旧环境版本 | runtime312 现状 | 使用方 | 影响 |
|---|---|---|---|---|
| blivedm | 0.1.2 | 未安装 | utils/platforms/bilibili2.py:1-3（B站新版直播 open_live 弹幕）、api_old.py:641-643 | bilibili2 平台弹幕不可用（平台为动态加载，不阻启动）。是否在用：config.json 有配置痕迹但无法确证使用中。**安装来源已实证**：PyPI 仅 0.1.1（缺 open_live 模块）；旧环境的 0.1.2 来自用户自有 fork `gitee.com/ikaros-521/blivedm`（commit c9ac671，direct_url.json 实证）——**须从该 fork 装**，命令见第 3 节 |
| flask_socketio | — | 未安装 | api_old.py:16 | 仅旧版 api 入口使用，webui 两入口不用 |
| langchain（子模块） | 0.0.142 | 1.3.18（大版本跃迁） | utils/chat_with_file/**（聊天文档功能，懒加载） | 8 个旧路径子模块全部缺失：document_loaders、llms、prompts、text_splitter、vectorstores、chains.question_answering、embeddings.openai、callbacks。**恢复路径已 dry-run 实证**：`langchain-community 0.4.2 + langchain-classic 1.0.8 + langchain-text-splitters 1.1.2` 干净解析（langchain-classic 即 1.x 兼容旧命名空间包），装后需 import 冒烟验证——命令见第 3 节 |
| coverage | — | 未安装 | run_tests.py:31（try 保护） | 仅测试工具，可忽略 |

**C. 非依赖阻断（装包解决不了）**

- `webui-bak.py` 是按 nicegui 1.4.30 写的单体旧版；runtime312 是 nicegui 3.16.0。
  实测 `webui-bak.py:3402` `ui.select(options={'custom_llm':...}, value='chatgpt')` 抛
  `ValueError: Invalid value: chatgpt`（1.x 静默容忍非法 value，3.x 严格校验）。
  即使补齐全部依赖，webui-bak.py 也无法出界面，需逐处代码适配（属"后期适配"范围）。
- `webui.py`（模块化新版）桩测 **BOOT_OK**，是可正常启动的入口。

### 2.4 环境附带发现（记录在案）

- runtime312 的 `Lib\site-packages\users.pth` 硬编码注入 6 个 GPT-SoVITS 路径进所有进程
  （潜在模块遮蔽风险；当前两入口未受影响，AUTOlive 自有目录在 sys.path 前列）。
- 基础栈健康（干净进程逐项验证）：torch 2.11.0+cu128、numpy 2.4.6、requests 2.34.2、
  urllib3 1.26.20、socketio、nicegui 3.16.0 全部 import OK。

## 3. 实施任务清单

1. **改写 `启动程序.bat`**（→ webui.py）：**照抄 2.1 完整模板**（入口行为 `webui.py`）。
   文件以 **UTF-8 无 BOM** 保存（配 `chcp 65001` 保证中文 echo 不乱码）。
   ⚠️ **本任务仅覆盖主 bat**（`启动程序.bat`）——`启动程序1.bat` 整体归任务 2 处置，
   裁决前**不得改动**（它当前在旧环境下仍可用，抢先改写=人为制造必坏中间态，Eng E1）。
   两份 bat 若在裁决后同批修改，须同步套用同一模板（Eng A2 防漂移）。
2. **`启动程序1.bat`（→ webui-bak.py）处置**：⚠️ **受 UC-1(本次) 门控**——webui-bak.py 在 runtime312 上
   实测必崩（nicegui 3.x，见 2.3-C），先经最终批准门三选一（留旧环境指向 / 归档 / 带警告切换），裁决后执行。
3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行，全部使用**绝对路径**：

   **A 组（启动阻断）**
   - zhipuai（已 dry-run 实证）：
     `D:\AI\EDTalk\runtime312\python.exe -m pip install -c D:\AI\AUTOlive\specs\integration\install\constraints.txt zhipuai`
   - google-generativeai：**禁止直接安装**。三选一裁决前不动：① 不装+代码守卫（若 UC-2 采纳，见任务 6）
     ② 迁移 google-genai（装 `google-genai` 新 SDK + gemini.py 代码适配）③ 永久放弃 Gemini 通道（接受通道不可用）。

   **B 组（功能级，按需启用）**
   - blivedm（bilibili2 弹幕）：**先归档为本地 wheel 再装**（项目既有惯例，见 install/wheels_sha256.txt；
     gitee 个人仓库存在改名/私有化风险，远程命令不可作长期交付物，Eng H1）：
     `D:\AI\EDTalk\runtime312\python.exe -m pip download -d D:\AI\AUTOlive\specs\integration\install\wheels git+https://gitee.com/ikaros-521/blivedm@c9ac671f783c4c3eaa4ee4e7738226ebbf402259`
     然后 `pip install -c <constraints> <本地wheel路径>`；哈希记入 wheels_sha256.txt。
   - langchain 旧命名空间恢复（聊天文档功能，已 dry-run）：
     `D:\AI\EDTalk\runtime312\python.exe -m pip install -c D:\AI\AUTOlive\specs\integration\install\constraints.txt langchain-community langchain-classic langchain-text-splitters`
     （装后跑 import 冒烟验证 8 个旧路径；**是否在用：与 blivedm 同级不确定**——chat_with_file 无静态外部
     引用者，可能经动态派发调用或为死代码，装包前先确认引用点存在，Eng H2）
   - flask_socketio：仅 api_old.py 用——**建议先裁决 api_old 去留**（TODOS 已登记）再决定是否装。
   - coverage：测试工具，try 保护，可忽略。

4. **验证**（前提链：zhipuai 已装 **且** Gemini 通道按裁决落地（任务 6）——缺一则 webui.py 仍在
   `gemini.py:1` 处崩溃，8086 探活不可达）：
   - 双击 `启动程序.bat` → 预检通过（中文不乱码）→ nicegui 起服日志行 → 浏览器打开
     `http://127.0.0.1:8086`（或 `curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8086`）；
   - zhipuai 装**后冒烟**（dry-run 只证解析，不证运行时 API，Eng T1）：
     `D:\AI\EDTalk\runtime312\python.exe -c "from zhipuai import ZhipuAI; ZhipuAI(api_key='probe')"`（能实例化即过）；
   - `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言固化为该脚本永久断言项**（users.pth 是共享
     环境文件，一次性检查无回归价值，Eng T2）；
   - bat 编码机械验证（BOM 会使 `@echo off` 首行报 `'∩╗┐@echo' 不是内部或外部命令`，Eng T3）：
     `powershell -c "$b=[IO.File]::ReadAllBytes('D:\AI\AUTOlive\启动程序.bat')[0..2]; if(($b)-ceq(0xEF,0xBB,0xBF)){'有BOM-不合格'}else{'无BOM-合格'}"`;
   - 最低验收判据（不依赖用户装包）：`启动程序.bat` 以 runtime312 解释器启动、PATH 前置生效、
     在首个缺失包处给出**可读错误且窗口保持可见**（`cmd /k` 生效）、**错误现场含修复指引**（echo 提示行）；
     `启动程序1.bat` 的验收判据按 UC-1(本次) 裁决结果另行定义。
   - 回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。
5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；
   UC-1(本次) 裁决同期给出。
6. **Gemini 通道落地**（条件任务，最终门裁决后执行其一；Eng E2 论证：**守卫①是三选一的支配策略**——
   选②时守卫 except 分支自然不触发、选③时守卫正是"通道不可用但不崩"的落地形式、选①守卫即本体，
   唯一反方论点"守卫掩盖配置错误"已由 logger 警告缓解。最终门对 UC-2 的推荐即"采纳守卫①"）：
   ① zhipu.py/gemini.py 顶层导入加 try 守卫（缺失时 logger 警告"通道不可用，功能降级"，不阻断启动）
   ② 装 google-genai 并适配 gemini.py（适配工作）
   ③ 放弃通道（无代码动作，仅清单销案）。

## 4. NOT in scope（本计划明确不做）

| 项 | 理由 |
|---|---|
| 安装任何缺失依赖 | 用户明确指令：只调查、列清单，手动补装 |
| webui-bak.py 的 nicegui 3.x 代码适配 | 属"后期适配"范围（2026-09-02 已裁决推迟），非装包可解决 |
| 移除 users.pth / 清理 runtime312 | 触碰共享基线环境，超出本任务爆炸半径；记录待查 |
| 依赖版本 API 差异适配（pydantic/nicegui/langchain 等） | 既定后期工作 |
| D:\AI\start\ 并行脚本的处置 | 与本任务无关，保持现状 |

## 5. What already exists（复用映射）

| 子问题 | 已有方案 | 复用 |
|---|---|---|
| runtime312 无 activate.bat 的启动模式 | D:\AI\start\start_autolive.bat（PATH 前置） | 是（模板照抄） |
| 安装保护机制 | specs/integration/install/constraints.txt | 是（清单附命令引用） |
| 冒烟/探测方法 | install/smoke_test.py、boot_probe_iter.py（本次新增） | 是 |
| 旧环境回滚 | git 跟踪的两个 .bat + Miniconda3 未动 | 是 |

## 6. 风险与回滚

- 脚本改动两文件、均 git 跟踪，`git checkout` 即回滚，分钟级。
- 旧 Miniconda3 环境不删除、不修改——热回滚资产完好。**退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3（无退出标准的"统一环境"会永久停在双环境并行）。
- zhipuai 安装是唯一可能触碰基线的动作，且由用户手动执行并带 constraints 保护（dry-run 已验证不触碰）。
- **供应链信任标注（Eng S1）**：`HF_ENDPOINT=https://hf-mirror.com` 为本次新增环境变量，模型/分词器
  下载经第三方镜像——国内网络下的社区惯例，属**明示接受的信任决策**，留此存照供后续审计。

> **编号说明**：本文件 Review record 中的 UC-1/UC-2 为 **2026-09-22 本次运行**的用户挑战编号
> （UC-1=webui-bak.py 弃用/适配裁决，UC-2=zhipu/gemini 导入守卫），与 2026-09-02 批次的
> UC-1（编排优先还是环境优先）无关。

<!-- autoplan-accepted:ceo -->
- 硬性约束①：禁止在 runtime312 裸装 google-generativeai（破坏 protobuf==7.35.1 pb2 保护，运行期静默断弹幕）。Gemini 通道三选一：①不装+代码守卫（若 UC-2 采纳）②迁移 google-genai（装包+适配）③禁止。
- 硬性约束②：所有依赖补装命令一律使用绝对路径解释器（`D:\AI\EDTalk\runtime312\python.exe -m pip install -c specs/integration/install/constraints.txt <pkg>`），防 pip 打错环境。
- 启动脚本模板：解释器直启 + PATH 前置（%RT%;%RT%\Scripts;%RT%\Library\bin;%FFMPEG_PATH%）+ FFMPEG_PATH/KMP_DUPLICATE_LIB_OK/HF_ENDPOINT 保留 + `cd /d %~dp0` + 2 行解释器预检（失败 echo 中文原因 + pause）。
- 验证步骤：双击 bat → 预检通过 → nicegui 起服日志行 → 8086 探活；boot_probe_iter.py 桩集合回归；sys.path 首位断言（users.pth 保险）。
- zhipuai 安装已验证（dry-run）：装 zhipuai 2.1.5.20250825 + PyJWT 2.8.0，不触碰基线包，命令带 constraints。
- Miniconda3 退役线：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；UC-1 裁决同期给出。
- google-generativeai 0.3.1 与 zhipuai 旧版为弃用/过时 SDK 的事实必须在清单中标注；zhipuai dry-run 显示将装 2025-08 新版（风险缓和）。
- UC-1、UC-2 保持原样排队至最终批准门，不得自动裁决。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- 启动脚本必须以 UTF-8 无 BOM 保存并含 chcp 65001（中文 echo 不乱码是验收项）。
- 模板含 if not defined RT 覆盖机制、ModuleNotFoundError 修复指引 echo 行、cmd /k 窗口保持（自动化场景可删，模板注释已注明）。
- 任务 1 = 照抄 2.1 完整模板（完整可交付文件，非片段）；1.bat 头部注释标注"旧版单体界面"。
- 任务 4 验收前提链：zhipuai 已装 且 Gemini 通道按任务 6 裁决落地，缺一不可达 8086 探活。
- 任务 6 = Gemini 通道条件任务（守卫①/google-genai②/放弃③），最终门裁决后执行其一。
- B 组命令：blivedm 从 gitee fork（c9ac671）装；langchain 用 community+classic+text-splitters 三包（装后 import 冒烟）。
- 全部 pip 命令使用绝对路径解释器与绝对路径 constraints（正文命令为权威；accepted:ceo 块②的相对路径以勘误为准）。
- 8086 探活命令：浏览器 http://127.0.0.1:8086 或 curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8086。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- 任务 1 仅覆盖 `启动程序.bat`；`启动程序1.bat` 在 UC-1 裁决前不得改动（E1，防人为制造必坏中间态）。
- 验证链新增三件：zhipuai 实例化冒烟（from zhipuai import ZhipuAI + 实例化）、bat BOM PowerShell 检测、sys.path 首位断言固化为 boot_probe_iter.py 永久项（T1/T2/T3）。
- blivedm 走本地 wheel 归档安装（pip download 至 install/wheels + 哈希入 wheels_sha256.txt），远程 fork 命令仅作归档来源（H1）。
- langchain 行标注在用不确定度（与 blivedm 同级），装包前先确认 chat_with_file 引用点存在（H2）。
- 第 6 节含 HF_ENDPOINT 第三方镜像供应链信任标注（S1）。
- UC-2 最终门推荐"采纳守卫①"（Eng E2 支配策略论证已写入任务 6）；守卫的实际采纳由用户在门上裁决。
- utils/__init__.py PEP 562 惰性化已登记 TODOS（A1）；本批次守卫为止血措施。
<!-- /autoplan-accepted:eng -->
## Review record

# CEO REVIEW（SELECTIVE_EXPANSION，/autoplan 2026-09-22）

> 双声部：Codex 不可用（未认证，`codex login` 可恢复），Claude 独立子代理声部 `[subagent-only]`。
> 子代理产出 14 项发现 + "有条件放行"裁决（3 条修正前置），已逐项裁决并入下文。

## 0A 前提挑战

| # | 前提 | 评估 | 结论 |
|---|---|---|---|
| P1 | runtime312 无 activate.bat，需解释器直启 | 已实证：conda-meta 存在但 Scripts\activate.bat 缺失（grep 无结果） | 接受 |
| P2 | start_autolive.bat 的 PATH 前置模式适用于自有 bat | 同一环境同一模式，42/43 冒烟先例 | 接受 |
| P3 | 两 bat 均 git 跟踪、可回滚 | 已实证：`git ls-files` 命中两文件 | 接受（子代理发现 5 已验证） |
| P4 | zhipuai 可装且兼容 | **已实证（子代理发现 4 采纳）**：`pip install --dry-run -c constraints.txt zhipuai` 干净解析，装 zhipuai 2.1.5.20250825（2025-08 新版，比旧环境 2023 版新）+ PyJWT 2.8.0，不触碰基线包 | 接受（假设已变证据） |
| P5 | webui.py BOOT_OK = 可用 | 模块级验证；nicegui 服务真实起服、页面渲染未端到端验证——列入验证步骤 | 接受（带缓解） |
| P6 | "调整两个脚本"=字面执行 | **子代理发现 7/11 证伪其意图**：1.bat 指向实测必坏入口（nicegui 3.x），忠实执行产出坏脚本。意图是"两个入口在新环境可用" | 修正 → **UC-1 留门** |

无其他明显错误前提。UC-1/UC-2 排队至最终批准门（见 0D）。

## 0B 已有资产复用

- `D:\AI\start\start_autolive.bat`（解释器直启模板）——照抄模式。
- `specs/integration/install/constraints.txt`（基线保护）——清单命令引用。
- `install/boot_probe_iter.py`（本次新增的迭代桩探测）——可复用为切换后回归探测。
- git 跟踪 + 旧环境未动——分钟级回滚资产。

## 0C 梦想状态图

```
CURRENT                        THIS PLAN                      12-MONTH IDEAL
自有 bat 用 activate.bat 激活  --> 解释器直启+PATH 前置+自检  --> 一键起停全链路+健康检查
依赖缺口=启动即崩(不可见)      --> 缺口清单+严重度分级+使用方  --> 缺口=选择性可选通道
双环境并行无退出标准           --> Miniconda3 退役线落纸      --> 单环境+lock 可复现
```

## 0C-bis 实现方案对比

```
APPROACH A（最小可行）：只改两个 bat + 交清单（用户指令字面执行）
  Effort: S  Risk: 低
  Pros: 完全遵指令；diff 最小
  Cons: 1.bat 指向实测必坏入口；zhipuai/gemini 缺失期两入口全崩
APPROACH B（当前计划）：A + 预检/断言/禁止项/退役线（子代理修正全采纳）
  Effort: S(+1 文档批次)  Risk: 低
  Pros: 失败可见化；证据齐备；缺口分级清楚
  Cons: bat 略复杂（+2 行预检）
APPROACH C（守卫优先）：zhipu.py/gemini.py 加 try 守卫 → 零安装完成切换
  Effort: S  Risk: 低-中
  Pros: A 类启动阻断清零；切换不依赖手动装包
  Cons: 超出用户指令交付面（改了业务代码）→ UC-2 留门
RECOMMENDATION: B（P5 显式化 + P3 务实）；C 若获批则为最优终态（B 是其子集）
```

## 0F 模式确认

SELECTIVE_EXPANSION（autoplan 固定覆盖）。

## 0D 范围决策（SELECTIVE_EXPANSION，自动裁决）

| # | 扩展提案（含子代理发现） | Effort | 裁决 | 理由 |
|---|---|---|---|---|
| 1 | bat 解释器预检（发现 12） | S | **接受** | 爆炸半径内、2 行、失败可见化 |
| 2 | zhipuai dry-run 验证（发现 4） | S | **接受（已执行）** | 假设→证据 |
| 3 | 验证加 sys.path 断言（发现 6） | S | **接受** | users.pth 运行期保险 |
| 4 | google-generativeai 选项③ 不推荐→**禁止**（发现 8） | S | **接受** | P1：运行期静默破坏弹幕 pb2 是最高危失败形态 |
| 5 | 两旧 SDK 生命周期标注（发现 14） | S | **接受** | zhipuai 已缓和（dry-run 装新版）；google-generativeai 0.3.1 确为弃用 SDK，②迁移为推荐路径 |
| 6 | Miniconda3 退役线（发现 9） | S | **接受** | 2 周稳定+功能冒烟 → 归档，写进计划第 3 节 |
| 7 | 裁决落盘机制（发现 10） | S | **接受** | 裁决附注写入 api_diff_backlog.md |
| 8 | 清单加"是否在用"列（发现 3） | S | **接受（如实标注）** | 已实测：config.json 有配置痕迹但无法确证使用中，落盘"无法从配置确证" |
| 9 | zhipu.py/gemini.py 导入守卫（发现 1） | S | **UC-2 留门** | 改业务代码，超出用户指令交付面 |
| 10 | webui-bak 弃用/适配裁决 + 1.bat 处置（发现 2/7/11） | M | **UC-1 留门** | 产品级决策，用户拍板 |
| 11 | api_old/flask_socketio 清理 | S | 延期 TODOS | 爆炸半径外 |

**新增硬性约束**：① 禁止在 runtime312 裸装 google-generativeai（必须三选一走①②）；② 一律用绝对路径解释器装包（`D:\AI\EDTalk\runtime312\python.exe -m pip ...`），防 pip 打错环境。

## 0E 时间轴拷问（人工时标 → CC 压缩后）

- HOUR 1：bat 改写模板照抄——惊吓点：%~dp0 带尾反斜杠，`cd /d %~dp0` 直接可用，勿手拼路径。
- HOUR 2-3：依赖补装——歧义点：google-generativeai 三选一必须先裁决再动手（UC 关联）。
- HOUR 4-5：验证——惊吓点：webui.py 起服后端口/config 绑定问题属适配债，勿归因环境。
- HOUR 6+：会希望早知道的——守卫（UC-2）若采纳，A 类清单项即时降级为"功能可选"。

## 11 节审查发现（主声部）

### S1 架构
```
双击 .bat ──▶ 预检(explain/python.exe 存在) ──▶ PATH 前置(runtime312+Library\bin+ffmpeg)
         ──▶ "%RT%\python.exe" webui.py ──▶ nicegui 8086
                     │
                     └─ utils/__init__ → web_server → my_handle → gpt → zhipu/gemini  ← 启动阻断链（UC-2 裁决后消失/缓解）
```
- 发现 1（中，采纳）：中枢启动命脉硬耦合 `D:\AI\EDTalk` 绝对路径（EDTalk 挪动/重装即断）——预检 2 行缓解；结构性解耦属后续议题。
- 发现 2（高，UC-1）：webui-bak/webui 双入口双维护面——产品裁决。
- 回滚姿态：git checkout 分钟级；旧环境热回滚完好。

### S2 错误与救援映射
```
METHOD/CODEPATH              | WHAT CAN GO WRONG           | EXCEPTION CLASS
双击 .bat                    | 解释器路径不存在             | FileNotFoundError/命令失败
                             | 缺 zhipuai/google.generativeai | ModuleNotFoundError
用户手动 pip install          | constraints 冲突             | pip ResolutionConflict
                             | pip 打错环境（PATH 命中别的 python） | 静默装错 ← 绝对路径命令拦截
webui-bak 启动               | nicegui 3.x ValueError       | ValueError(Invalid value)
```
```
EXCEPTION CLASS        | RESCUED? | RESCUE ACTION                    | USER SEES
FileNotFoundError      | Y(计划)  | bat 预检 echo 提示               | 明确中文提示
ModuleNotFoundError    | Y(清单)  | 按清单补装                       | 模块名+清单指引
ResolutionConflict     | Y(机制)  | constraints 已拦截               | pip 报错+清单备注
静默装错环境           | Y(计划)  | 绝对路径解释器命令               | N/A（预防）
ValueError(Invalid value) | N ←GAP | 属 webui-bak 适配债（UC-1）     | traceback
```
1 个 GAP = webui-bak 适配债本身，UC-1 裁决后定性。

### S3 安全与威胁模型
- 无新增对外端点/输入面。供应链：zhipuai 走 PyPI 官方源 + constraints（Low）；google-generativeai 选项③ = 供应链+完整性双风险 → 已升格禁止。
- users.pth 环境注入为存量事实，已升级为整合正式议题（TODOS）。
- 无 PII/支付面。无新发现。

### S4 数据流与交互边界
本次唯一"数据流"= 依赖清单交付流：调查(AST+桩探测) → 清单(分级+使用方) → 用户手动安装 → 验证(预检断言)。阴影路径：装错环境（绝对路径命令拦截）、部分安装（清单逐项可勾选）、装后仍崩（boot_probe_iter.py 回归探测）。无 UI 交互面。无新发现。

### S5 代码质量
本计划不改业务代码（UC-2 未决前）；bat 模板与 start_autolive.bat 一致（DRY：同一形态两处镜像，差异仅入口文件名——可接受的最小重复）。无发现。

### S6 测试
```
NEW CODEPATHS                | 测试类型   | 计划内? | 失败路径测试?
bat 双击→nicegui 起服        | Smoke      | Y(任务4) | 预检缺解释器路径
webui.py 模块引导            | Smoke      | Y(boot_probe) | 缺依赖→ModuleNotFoundError 可见
webui-bak 启动               | Smoke      | Y | 已知必崩(nicegui 3.x)——预期失败对照
zhipuai 安装                 | Integration| Y(dry-run 已做) | ResolutionConflict→constraints
```
凌晨 2 点敢发布 = 双击 bat 看到日志行 + 8086 探活 + 无 traceback。无 CRITICAL GAP（webui-bak 已知失败有预期对照）。

### S7 性能
解释器直启 vs conda 激活：启动路径等价（Library\bin 前置覆盖激活语义）；运行期服务拓扑不变。无发现。

### S8 可观测性
缺口：bat 启动失败只有 cmd 窗口一闪（双击场景）。修复（采纳发现 12）：预检失败 `echo` 中文原因 + `pause`，窗口保留。验证步骤含日志行检查。无其他发现。

### S9 部署与发布
顺序：bat 改写 → 用户补装（清单）→ 验证 → 2 周并行观察 → Miniconda3 归档（退役线）。风险窗口：补装前双击 bat = 显式 ModuleNotFoundError（可见、有指引）——可接受。回滚：git checkout 两文件。无数据库/迁移面。无新发现。

### S10 长期轨迹
- 可逆性 5/5（git + 旧环境双保险）。
- 显式债：webui-bak nicegui 适配（在账）、langchain 代差（在账）、users.pth（升级登记）、api_old 处置（新登记 TODOS）。
- 1 年问题：本计划 + 清单 + 决策审计足以复原全部决策链。
- Phase 2 方向：UC-2 守卫化 → 缺口清单全面"功能可选化"；统一启动脚本与 start\ 收敛。无新发现。

### S11 设计与 UX
SKIPPED——Phase 0 范围检测无 UI 范围（14 处命中均为 API/SDK/import/pip 等术语）。

## CLAUDE SUBAGENT（CEO — 战略独立声部）发现逐项裁决

| # | 发现 | 严重度 | 主声部裁决 |
|---|---|---|---|
| 1 | 导入守卫提为任务 0（两文件 2-4 行，A 类阻断清零） | 高 | **UC-2 留门**（方向变更不可自动裁决；主声部认同其价值） |
| 2 | webui-bak 应框定为"弃用候选" | 高 | **UC-1 留门**（并入 7/11） |
| 3 | 清单按"值不值得补"重组 | 中 | 采纳 → 0D#8（config.json 无法确证使用中，如实标注） |
| 4 | zhipuai "预期无冲突"是假设 | 中 | 采纳 → 已跑 dry-run，假设变证据（P4） |
| 5 | 回滚前提未验证 | 低 | 采纳 → git ls-files 已验证（P3） |
| 6 | users.pth 运行期遮蔽未排除 | 低 | 采纳 → 验证加 sys.path 断言 |
| 7 | 明知必坏仍切换 1.bat 反噬直播现场 | 高 | **UC-1 留门**（三选一：留旧环境/归档/带警告切换） |
| 8 | 选项③是未来地雷 | 中高 | 采纳 → 升格**禁止** + 硬性约束① |
| 9 | 无 Miniconda3 退役标准 | 中高 | 采纳 → 计划第 3 节补退役线 |
| 10 | 裁决无留痕 | 低 | 采纳 → api_diff_backlog 附注 |
| 11 | 1.bat 暂留旧环境被"压过"而非被分析否决 | 高 | **UC-1 留门**（与 7 同项） |
| 12 | 中枢启动硬耦合 EDTalk 目录无护栏 | 中 | 采纳 → 预检缓解（结构性解耦留 TODOS） |
| 13 | （=发现 1） | 中 | 同 1 |
| 14 | 两旧 LLM 通道是生态弃儿 | 中高 | 采纳 → 生命周期标注；zhipuai 经 dry-run 部分缓和（装到 2025-08 新版）；gemini ②迁移为推荐路径 |

## CEO 双声部 — 共识表 `[subagent-only]`（Codex 未认证，降级单声部）

```
CEO DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                            Claude(主)  Claude(子代理)  Consensus
  ──────────────────────────────────── ─────────── ─────────────── ─────────
  1. 前提有效性                         修正后成立   证伪 P6(1.bat)   CONFIRMED(修正)
  2. 是否在解决正确的问题               是          质疑(切换器≠可用性) CONFIRMED(修正)
  3. 范围标定是否正确                   遵指令最小   守卫+弃用裁决    DISAGREE→UC-1/UC-2 门
  4. 替代方案是否充分探索               3 方案      守卫优先方案      CONFIRMED(已并入 0C-bis C)
  5. 竞争/生态风险                      已记录       死 SDK 论        CONFIRMED(已采纳标注+禁止③)
  6. 6 个月轨迹是否健全                 退役线补齐后 停留双环境论      CONFIRMED(已采纳退役线)
═══════════════════════════════════════════════════════════════
CONFIRMED = 双方一致（含采纳修正后）。DISAGREE = 单声部异议，按规则升级 User Challenge 留最终门。
```

## NOT in scope（CEO 审查后更新）

在原计划第 4 节基础上追加：

| 项 | 理由 |
|---|---|
| api_old.py / flask_socketio 处置 | 爆炸半径外（非本任务入口），延期 TODOS |
| EDTalk 目录结构性解耦（软链/复制环境） | 预检缓解已足够，结构改造超范围 |
| google-generativeai 裸装（选项③） | **禁止**（非延期）：运行期静默破坏弹幕 pb2 |

## What already exists（CEO 审查后追加）

- 基线保护机制 constraints.txt —— zhipuai 推荐命令直接引用。
- boot_probe_iter.py —— 切换后回归探测直接复用。
- api_diff_backlog.md —— google-generativeai/zhipuai 裁决附注落盘载体。

## Dream state delta

本计划完成后：AUTOlive 两个自有启动脚本脱离 conda 激活语义（12 个月理想态的"一键+健康检查"达成前半）；依赖缺口从"启动即崩不可见"变为"分级清单+使用方定位"；Miniconda3 首次有了可验证的退出标准。距理想态还差：UC-1（webui-bak 命运）、UC-2（守卫化）、start\ 脚本收敛三步，均已显式在账。

## 错误/救援登记册与失效模式登记册

见 S2 表格（5 个 codepath，1 个 GAP = webui-bak 适配债，UC-1 裁决后定性；无 CRITICAL GAP——所有失败模式均有"用户可见"输出路径）。

```
CODEPATH              | FAILURE MODE         | RESCUED? | TEST? | USER SEES?        | LOGGED?
bat 双击启动          | 解释器缺失           | Y(预检)  | Y     | 中文提示+pause    | Y
bat 双击启动          | 依赖缺失             | Y(清单)  | Y     | 模块名+指引       | Y
手动 pip 安装         | constraints 冲突     | Y        | Y(dry-run) | pip 报错     | Y
手动 pip 安装         | 装错环境             | Y(绝对路径) | N/A(预防) | N/A         | Y
webui-bak 启动        | nicegui ValueError   | N(UC-1)  | Y(预期对照) | traceback   | Y
```

## TODOS.md 延期项（自动裁决：A 加入）

1. **api_old.py 与 flask_socketio 处置**（P3, S）——旧 API 入口是否保留/归档；flask_socketio 仅其使用。
2. **users.pth 注入升级为整合正式议题**（P2, S）——runtime312 被 GPT-SoVITS 硬编码路径注入所有进程，遮蔽风险，验证断言只是临时保险。
3. **EDTalk 目录硬耦合的结构性解耦**（P3, M）——预检缓解已落地，环境复制/软链方案待评估。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~30min / CC: ~5min)** — 启动脚本 — 改写 `启动程序.bat`（webui.py）：解释器直启模板 + 2 行预检
  - Surfaced by: 用户指令本体 + S8（失败可见化）
  - Files: 启动程序.bat
  - Verify: 双击 → 中文预检输出 → nicegui 起服日志行
- [ ] **T2 (P1, human: ~30min / CC: ~5min)** — 启动脚本 — `启动程序1.bat`（webui-bak.py）按 UC-1 裁决结果处置
  - Surfaced by: 子代理发现 7/11（UC-1：留旧环境指向 / 归档 / 带警告切换，三选一）
  - Files: 启动程序1.bat
  - Verify: 按裁决结果各自定义（留旧=旧行为不变；带警告=echo 警告+pause）
- [ ] **T3 (P1, human: ~10min / CC: ~2min)** — 依赖清单 — 交付依赖补装清单（本计划 2.3 节 + 生命周期标注 + 禁止项 + 绝对路径命令）
  - Surfaced by: 用户指令本体 + 子代理发现 3/8/14
  - Files: 本计划文件（清单即交付物）
  - Verify: 用户按清单手动补装 zhipuai 后，`python -c "import zhipuai"` 成功
- [ ] **T4 (P2, human: ~10min / CC: ~2min)** — 验证 — 补装后验证步骤：boot_probe_iter.py 回归 + sys.path 断言 + 8086 探活
  - Surfaced by: P5 缓解 + 子代理发现 6
  - Files: 无新文件（命令行验证）
  - Verify: `python specs/integration/install/boot_probe_iter.py` 桩集合为空且 BOOT_OK
- [ ] **T5 (P2, CC: ~5min)** — 裁决落盘 — google-generativeai/zhipuai 裁决附注写入 api_diff_backlog.md
  - Surfaced by: 子代理发现 10
  - Files: specs/integration/install/api_diff_backlog.md
  - Verify: 附注含日期与裁决内容
- [ ] **UC-1/UC-2（最终批准门裁决后展开）** — 导入守卫（若 UC-2 采纳：zhipu.py/gemini.py 各 2-4 行）与 webui-bak 命运（若 UC-1 采纳弃用：归档脚本+TODOS 登记）

### Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY (CEO)             |
+====================================================================+
| Mode selected        | SELECTIVE_EXPANSION (autoplan 固定)          |
| System Audit         | 无 stash；TODO 均与本任务无关；design doc     |
|                      | 为 8/31 前端特性（不适用）；两 bat git 跟踪    |
| Step 0               | 方案 B；接受 7 项扩展 / 延期 1 项 /           |
|                      | UC-1、UC-2 留门 / 新增 2 硬性约束             |
| S1  (Arch)           | 2 issues（EDTalk 硬耦合、双入口双维护）       |
| S2  (Errors)         | 5 错误路径，1 GAP（webui-bak 适配债）         |
| S3  (Security)       | 0 issues（选项③升格禁止）                     |
| S4  (Data/UX)        | 清单交付流 3 阴影路径全部覆盖                 |
| S5  (Quality)        | 0 issues                                     |
| S6  (Tests)          | 图已出，0 CRITICAL GAP                       |
| S7  (Perf)           | 0 issues                                     |
| S8  (Observ)         | 1 缺口（bat 预检，已采纳）                    |
| S9  (Deploy)         | 0 risks（显式失败+分钟级回滚）                |
| S10 (Future)         | 可逆性 5/5，显式债务 4 项                     |
| S11 (Design)         | SKIPPED（无 UI 范围）                         |
+--------------------------------------------------------------------+
| NOT in scope         | written（8 项）                              |
| What already exists  | written（3 项追加映射）                       |
| Dream state delta    | written                                      |
| Error/rescue registry| 5 codepaths, 0 CRITICAL GAPS                 |
| Failure modes        | 5 total, 0 CRITICAL GAPS                     |
| TODOS.md updates     | 3 items proposed→accepted                    |
| Scope proposals      | 11 proposed, 7 accepted, 1 deferred          |
| CEO plan             | written（ceo-plans/2026-09-22-runtime312-…） |
| Outside voice        | Claude 子代理 [subagent-only]，Codex 未认证   |
| Lake Score           | N/A（无完整项 vs 捷径型决策）                 |
| Diagrams produced    | 启动链路图、梦想状态图（文字化）              |
| Stale diagrams found | 0                                            |
| Unresolved decisions | 2（UC-1、UC-2）→ 最终批准门                  |
+====================================================================+
```

## 决策审计（Decision Audit Trail — CEO 阶段）

> **勘误与补充（规格评审第 2 轮采纳，2026-09-22）**：
> 1. accepted 块中"FFMPEG_PATH/KMP_DUPLICATE_LIB_OK/HF_ENDPOINT 保留"措辞不准——前两项为原 bat
>    保留，**HF_ENDPOINT 为新增**（承自 start\start_autolive.bat 模板）。块字节保持不变，以此勘误为准。
> 2. accepted 块硬性约束②中的 constraints 路径应为绝对路径
>    `D:\AI\AUTOlive\specs\integration\install\constraints.txt`（相对路径在错误 cwd 下静默失效）。
> 3. 窗口保持：模板必须含 `cmd /k`（S2"用户可见"与 S9"显式失败"以此成立）。
> 4. 最低验收判据 scope：仅绑定 `启动程序.bat`；`启动程序1.bat` 按 UC-1(本次) 裁决另行定义。

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否选项 |
|---|---|---|---|---|---|---|
| 1 | CEO | 前提 P1-P6：5 接受、P6 修正后留门 | 机械 | P6/P5 | 均经实证（dry-run、ls-files、grep） | — |
| 2 | CEO | 实现方案选 B（全采纳修正） | 机械 | P5/P3 | 显式失败可见化，成本 S | A（字面执行）、C（留门） |
| 3 | CEO | 接受 7 项扩展（预检/dry-run/断言/禁止③/退役线/落盘/清单增强） | 机械 | P2/P1 | 爆炸半径内、均 S 级 | 不加 |
| 4 | CEO | 延期 api_old 处置 | 机械 | P2 | 爆炸半径外 | 纳入本期 |
| 5 | CEO | 选项③ 升格禁止 | 机械 | P1 | 运行期静默破坏是最高危形态 | 维持"不推荐" |
| 6 | CEO | UC-1（webui-bak/1.bat 命运）留门 | **User Challenge** | — | 双声部一致要求用户裁决 | 自动裁决 |
| 7 | CEO | UC-2（zhipu/gemini 导入守卫）留门 | **User Challenge** | — | 双声部一致主张改用户明示范围 | 自动裁决 |

<!-- autoplan-accepted:ceo -->
- 硬性约束①：禁止在 runtime312 裸装 google-generativeai（破坏 protobuf==7.35.1 pb2 保护，运行期静默断弹幕）。Gemini 通道三选一：①不装+代码守卫（若 UC-2 采纳）②迁移 google-genai（装包+适配）③禁止。
- 硬性约束②：所有依赖补装命令一律使用绝对路径解释器（`D:\AI\EDTalk\runtime312\python.exe -m pip install -c specs/integration/install/constraints.txt <pkg>`），防 pip 打错环境。
- 启动脚本模板：解释器直启 + PATH 前置（%RT%;%RT%\Scripts;%RT%\Library\bin;%FFMPEG_PATH%）+ FFMPEG_PATH/KMP_DUPLICATE_LIB_OK/HF_ENDPOINT 保留 + `cd /d %~dp0` + 2 行解释器预检（失败 echo 中文原因 + pause）。
- 验证步骤：双击 bat → 预检通过 → nicegui 起服日志行 → 8086 探活；boot_probe_iter.py 桩集合回归；sys.path 首位断言（users.pth 保险）。
- zhipuai 安装已验证（dry-run）：装 zhipuai 2.1.5.20250825 + PyJWT 2.8.0，不触碰基线包，命令带 constraints。
- Miniconda3 退役线：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；UC-1 裁决同期给出。
- google-generativeai 0.3.1 与 zhipuai 旧版为弃用/过时 SDK 的事实必须在清单中标注；zhipuai dry-run 显示将装 2025-08 新版（风险缓和）。
- UC-1、UC-2 保持原样排队至最终批准门，不得自动裁决。
<!-- /autoplan-accepted:ceo -->


# 执行记录（2026-09-22，最终批准门裁决后开工）

## 第二轮：双击失败修复 + 弃用链清理（用户实测反馈驱动）

**用户报告**：两个脚本双击启动均失败；并澄清 zhipu/gemini 通道在新版系统已弃用，要求删除残留。

**失败根因（实测复现）**：bat 文件被编辑器以 GBK 重存（原计划规定 UTF-8 无 BOM），而 bat 内
`chcp 65001` 使 cmd 将后续行按 UTF-8 解码——GBK 字节错位导致 cmd 行缓冲偏移碎裂，
全部命令"不是内部或外部命令"，python 从未执行。**教训**：模板把编码纪律押在"文件永不被重存"上不成立；
中文 Windows 的 bat 应直接使用原生 GBK 编码，与编辑器行为天然兼容。

**修复与清理**：

| 步骤 | 结果 |
|---|---|
| 两个 bat 重写为 **GBK 编码 + 去掉 chcp 65001**（CRLF，加"勿改存 UTF-8"注释） | cmd 实测 0 碎裂；主 bat 真实拉起到 `NiceGUI ready`（修复前 10+ 碎裂命令） |
| **删除弃用通道**（用户澄清 zhipu/gemini 已弃用） | 删 `utils/gpt_model/zhipu.py`、`gemini.py`；gpt.py 删导入+注册表项+加弃用回退保护（旧配置命中已弃用通道时告警并回退 blip） |
| 前端 image_recognition.py | 删 Gemini/智谱 配置卡与 options；模型选择只留 blip |
| webui-bak.py | 四处成套删除：gemini/zhipu 派发分支、UI 卡块、config 保存映射、options（防 NameError） |
| config.json | `image_recognition.model: 'gemini' → 'blip'`（关键：不改此项前端 select 会在 nicegui 3.x 下同 webui-bak 一样当场 ValueError；gemini/zhipu 子键保留为用户数据） |
| 验证 | 残留导入扫描=零（仅 .claude/worktrees 旧副本）；boot_probe BOOT_OK+SHADOW_OK；直跑起服 `GET / → 200` 零错误；cmd 实跑双 bat 0 碎裂（主 bat NiceGUI ready；旧版死于预期内 nicegui ValueError） |

**第二轮门上裁决的处置更新**：UC-2 守卫方案被弃用链整体删除取代（通道不复存在，守卫对象消失）；
zhipuai 补装清单作废（不再需要任何依赖补装）。UC-1 维持 B（带警告切换）。

## 第一轮完成（2026-09-22 门上裁决后）

## 门上裁决（用户 2026-09-22）

| 项 | 裁决 |
|---|---|
| UC-1 | **B：带警告切换**——1.bat 照新模板改写，头部加醒目警告（nicegui 3.x 待适配，勿在直播中依赖） |
| UC-2 | **A：采纳守卫**——zhipu.py/gemini.py 顶层导入加 try 守卫，缺包降级为警告+使用时 RuntimeError（含指引） |
| 总体 | 批准开工 |

## 已完成

| 步骤 | 结果 |
|---|---|
| 启动程序.bat 改写 | 完整模板落地（chcp 65001/预检/RT 覆盖/指引行/cmd /k/UTF-8 无 BOM） |
| 启动程序1.bat 改写 | 同模板 + 6 行警告头（UC-1=B） |
| UC-2 守卫 | zhipu.py:1-15/类守卫 + gemini.py:1-19/类守卫，缺包 logger 警告不阻断启动 |
| boot_probe_child.py | sys.path 遮蔽断言固化为永久项（Eng T2） |
| T5 裁决落盘 | api_diff_backlog.md 补 google-generativeai/zhipuai 裁决附注（带日期） |
| 验证 | BOM 双 bat 合格；boot_probe **桩集合=空**（守卫生效）；SHADOW_OK；webui.py **BOOT_OK（零补装）**；webui-bak 死于预期内 nicegui ValueError（已警告标注） |

## 剩余（用户手动）

1. 补装 zhipuai（命令见第 3 节，dry-run 已实证）→ 实例化冒烟 → 双击 `启动程序.bat` → 8086 探活。
2. 按需启用 B 组（blivedm 本地 wheel / langchain 三包，命令见第 3 节）。
3. 2 周并行观察期后：Miniconda3 归档 + api_old 处置裁决（TODOS）。


# DX REVIEW（DX POLISH，/autoplan 2026-09-22）

> 双声部：Codex 不可用（未认证），Claude 独立子代理声部 `[subagent-only]`。
> 子代理产出 11 项发现（2 high / 6 medium / 3 low），已逐项裁决，F1/F7/F8/F9/F10 直接修订正文。
> 搜索不可用（Aside 未就绪），竞争基准使用参照系对比（P1 授权）。
>
> **权威声明**：本 record 为审查记录；accepted 块为义务摘要。两者与正文 §2/§3 冲突时，
> 以正文 + 决策审计勘误块为准（消除双源漂移，DX F5）。

## Step 0：DX 范围评估

**Persona（P6 推断，与 2026-09-02 DX 评审一致）**
```
TARGET DEVELOPER PERSONA（= 单人运营者，兼职维护者）
Who:       单人运营直播系统；安装/启动/排障/升级全部自己来
Context:   开播前/直播中双击脚本起服务；出问题时没有第二个人可问
Tolerance: 双击 30 秒内要么起来、要么看到中文原因；超过 1 屏 traceback 无指引即放弃排查
Expects:   双击即起；缺包时知道装什么怎么装；改坏了能一键回滚
```

**共情叙述（第一人称）**：周五晚上开播前，我双击 `启动程序.bat`。旧版是 Miniconda 的 activate——出问题时黑窗一闪，我连报错都看不到。新脚本承诺：窗口保留、预检中文提示。如果跳出一行 `ModuleNotFoundError: zhipuai`，我需要的不是 traceback 本身，而是紧挨着的那行"装什么、怎么装"——DX 子代理 F7 命中的正是这个：错误现场与修复动作之间的距离，就是这个计划 DX 成败的距离。

**竞争基准（参照系对比，搜索不可用）**
```
Tool / 基线                        | TTHW            | Notable DX Choice
旧体验（Miniconda activate.bat）   | 双击 1 步       | 但失败不可见（窗口闪退）
D:\AI\start\start_autolive.bat 先例| 双击 1 步       | 42/43 冒烟背书
本计划                             | 双击 1 步 + 首次手动 pip（约 5 分钟，一次性） | 失败可见 + 现场修复指引 + cmd /k 保窗
目标档位：内部工具第一梯队（日常双击 <=1 分钟到 UI；首次装包 <=5 分钟）
```

**Magical moment（P5 最低成本载具）**：双击 → 中文预检绿灯 → 8086 浏览器打开即见 UI。载具 = 完整预检 + 报错指引 + `cmd /k` 保窗，零新代码，全部落在 bat 模板里。

**模式确认**：DX POLISH（autoplan 固定）——不扩范围，把每个触点做扎实。

## 开发者旅程 9 阶段（摩擦点已裁决）

| 阶段 | 运营者做什么 | 摩擦点 | 状态 |
|---|---|---|---|
| 1 决策 | 读本计划 2.3 节 | — | ok |
| 2 补装依赖 | 复制第 3 节绝对路径命令 | F2：B 组无命令 → 已修（blivedm fork 源 + langchain-classic 路径均实证） | fixed |
| 3 Gemini 裁决 | 最终门三选一 | F1：主路径缺口无主 → 已修（新增条件任务 6 + 任务 4 前提链） | fixed |
| 4 首次冒烟 | 双击 → 浏览器 8086 | F3：无探活命令 → 已修（curl/浏览器两式） | fixed |
| 5 日常一键启动 | 双击 | F8：中文乱码风险 → 已修（chcp 65001 + UTF-8 无 BOM 规定） | fixed |
| 6 排障 | 看窗口报错 | F7：裸 traceback 无指引 → 已修（echo 修复指引行） | fixed |
| 7 脚本升级/复用 | 并行测其他 runtime | F10：RT 写死 → 已修（if not defined RT 覆盖） | fixed |
| 8 回滚 | git checkout 两文件 | — | ok |
| 9 退役 | 2 周后归档 Miniconda3 | F11：自动化场景 cmd /k → 已修（模板注释说明可删） | fixed |

## 首次使用困惑报告（T+时间轴，已按裁决标注）

```
T+0:00  双击新 bat → 窗口保留（cmd /k），中文预检输出          → 通过
T+0:05  若缺 zhipuai → ModuleNotFoundError + echo 指引行        → F7 修复后：知道下一步
T+0:30  复制第 3 节命令装 zhipuai → 对照 dry-run 预期结果自验     → 通过
T+1:00  再双击 → 崩在 gemini.py:1（google.generativeai 缺失）    → F1 修复前：此处死路！
          F1 修复后：任务 6 条件任务承接，验收前提链写明
T+2:00  8086 探活：curl 或浏览器（F3 修复后有现成命令）           → 通过
T+3:00  最终状态：UI 可用 / 或按指引继续                          → 达标
```

## 8 Pass 评分（初评 → 修复后）

| # | Pass | 初评 | 修复后 | 依据与修复 |
|---|---|---|---|---|
| 1 | Getting Started | 6 | 8 | F1 主路径缺口（最高危）→ 条件任务 6 + 前提链；F3 探活命令；F9 完整模板 |
| 2 | API/CLI（脚本接口） | 5 | 8 | F4 约束模板矛盾（勘误块声明以正文为准）；F10 RT 覆盖；F6 头部注释 |
| 3 | Error Messages | 5 | 8.5 | F7 修复指引行；F8 chcp；S2 登记册已有映射。中文报错=问题+原因+修复三件套齐 |
| 4 | Documentation | 7 | 8 | F2 B 组命令实证化（fork 源/langchain-classic）；交付物指针明确 |
| 5 | Upgrade & Migration | 6 | 8 | 退役线 + cmd /k 自动化说明（F11）+ UC-1 三分支裁决后即改 |
| 6 | Dev Environment | 7 | 8.5 | F10 覆盖机制；users.pth sys.path 断言；dry-run 证据可自验 |
| 7 | Community | N/A | — | 内部单机部署无外部社区面。已检查，不适用 |
| 8 | DX Measurement | 5 | 7 | boot_probe_iter.py 可重复回归 + 验收判据可勾选；无更多度量面 |

## DX 子代理发现逐项裁决

| # | 发现 | 级别 | 裁决 |
|---|---|---|---|
| F1 | 主路径被 Gemini 裁决卡死，缺口无归属任务 | high | 采纳 → 新增任务 6（条件任务）+ 任务 4 前提链（P1 完整性） |
| F2 | B 组依赖零可复制命令 | medium | 采纳+探针实证 → blivedm=gitee fork 命令；langchain=community+classic 路径（均 dry-run） |
| F3 | 8086 探活无命令 | low | 采纳 → curl + 浏览器两式 |
| F4 | 硬性约束②模板相对路径与正文矛盾 | high | 采纳（勘误机制）→ accepted 块不可变，勘误块声明"以正文绝对路径为准" |
| F5 | 摘要块与正文双源漂移 | medium | 采纳 → Review record 顶部权威声明 + 勘误块 |
| F6 | 1.bat 命名无语义 | low | 采纳 → 头部注释标注入口与年代 |
| F7 | ModuleNotFoundError 无修复指引 | medium | 采纳 → 模板加 echo 指引行 + 验收判据升级（P1：问题+原因+修复） |
| F8 | bat 中文编码/代码页风险 | medium | 采纳 → chcp 65001 + UTF-8 无 BOM 规定 + 验收含"中文不乱码" |
| F9 | 模板是片段非完整文件 | medium | 采纳 → 2.1 改为完整可照抄模板，任务 1 照抄 |
| F10 | 解释器路径硬编码无覆盖 | medium | 采纳 → if not defined RT 一行覆盖机制 |
| F11 | cmd /k 无自动化出口 + UC-1 分支未预写 | low | 采纳（部分）→ 模板注释说明可删 cmd /k；UC-1 三分支 bat 内容待裁决后写（预写 3 份是投机，拒绝） |

## DX 双声部 — 共识表 `[subagent-only]`

```
DX DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                          Claude(主)  Claude(子代理)  Consensus
  ────────────────────────────────── ─────────── ─────────────── ─────────
  1. Getting started < 目标?          修复后可达   F1 主路径缺口    CONFIRMED(需修,已修)
  2. 脚本接口可猜测/一致?              中          F4/F10 矛盾+硬编码 CONFIRMED(需修,已修)
  3. 错误信息可行动?                   中          F7/F8 缺指引+乱码 CONFIRMED(需修,已修)
  4. 文档可查找可复制?                 中          F2/F9 片段化     CONFIRMED(需修,已修)
  5. 升级/回滚路径安全?                高          F11 逃生舱小缺口  CONFIRMED(需修,已修)
  6. 环境摩擦低?                      中          F10 覆盖缺失     CONFIRMED(需修,已修)
═══════════════════════════════════════════════════════════════
CONFIRMED = 双声部一致（含采纳修正后）；外部声部 N/A（Codex 未认证）。
```

## DX Scorecard

```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD (DX POLISH)                |
+====================================================================+
| Getting Started        |  6 -> 8    | F1 主路径归属 + F3/F9        |
| API/CLI (脚本接口)     |  5 -> 8    | F4 权威源 + F6/F10           |
| Error Messages         |  5 -> 8.5  | F7/F8 三件套齐               |
| Documentation          |  7 -> 8    | F2 实证命令                  |
| Upgrade & Migration    |  6 -> 8    | 退役线 + F11 出口            |
| Dev Environment        |  7 -> 8.5  | F10 + 断言 + dry-run 自验    |
| Community              |  N/A       | 内部部署                     |
| DX Measurement         |  5 -> 7    | 回归探测 + 可勾选验收        |
+--------------------------------------------------------------------+
| OVERALL                |  5.9 -> 8.0                              |
| TTHW                   |  首次: 双击必死 -> 约5min(含装包)        |
|                        |  日常: 双击 约30s 到 UI                  |
| Competitive tier       |  内部工具第一梯队                        |
| Magical moment         |  designed（双击→预检绿灯→8086 UI）       |
| Product Type           |  内部运维工具（双击脚本+手动命令面）      |
| Mode                   |  DX POLISH                               |
+====================================================================+
| Zero Friction         | covered（双击 1 步；失败可见）            |
| Learn by Doing        | covered（模板可照抄+预期结果对照）        |
| Fight Uncertainty     | covered（预检/指引/回滚三件套）           |
| Opinionated+Escape    | covered（RT 覆盖 + cmd /k 可删 + 手动装包）|
| Code in Context       | covered（完整模板即最终文件）             |
| Magical Moments       | covered（最低成本载具在 bat 内）          |
+====================================================================+
```

## DX Implementation Checklist

- [x] 日常 TTHW < 1 分钟（双击路径，补装完成后）
- [x] 安装是"一条命令"（每包一条绝对路径命令，B 组含实证来源）
- [x] 首次运行有有意义输出（中文预检 + 指引行）
- [x] Magical moment 载具落地（bat 模板内）
- [x] 错误信息=问题+原因+修复（预检 echo + ModuleNotFoundError 指引行）
- [ ] 验收时人工确认"中文不乱码"（执行期检查项）
- [x] 升级路径：退役线 + UC-1 分支裁决机制
- [x] 回滚命令可复制（git checkout）
- [x] 度量：boot_probe_iter.py 回归可重复

## NOT in scope（DX 阶段追加）

| 项 | 理由 |
|---|---|
| UC-1 三分支 bat 预写 3 份完整文件 | 投机产物，裁决后写一份真实的即可 |
| 启动脚本重命名（start_autolive 风格） | 历史命名，本计划不改文件名（F6 仅加注释） |
| 启动日志落盘（重定向到 log 文件） | cmd /k 场景窗口即日志；落盘属 start_all 编排议题（TODOS 已有） |

## DX 完成摘要

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (DX)                 |
+====================================================================+
| Mode                | DX POLISH（autoplan 固定）                    |
| Persona             | 单人运营者（P6 推断，与 2026-09-02 一致）      |
| 初始 DX 完整度       | 5.9/10 → 修复后 8.0/10                       |
| TTHW                | 首次 约5min（含装包）→ 日常双击 约30s         |
| 子代理发现          | 11 项（2 high / 6 medium / 3 low）全裁决      |
| 正文修订            | F1/F2/F3/F7/F8/F9/F10/F11 已落 2.1/3 节      |
| F4/F5 处置          | 勘误机制 + Review record 顶部权威声明         |
| Consensus           | 6/6 维度 CONFIRMED（含"需修且已修"）；外部 N/A |
| 未决项              | 0 新增（UC-1/UC-2 维持既有排队）              |
+====================================================================+
```

## 决策审计（Decision Audit Trail — DX 阶段）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否选项 |
|---|---|---|---|---|---|---|
| 8 | DX | F1-F11 全采纳（9 项直接修订正文） | 机械 | P1/P5 | 均爆炸半径内 S 级；F4 经勘误机制 | 维持现状 |
| 9 | DX | UC-1 三分支不预写 bat | 机械 | P3 | 投机产物；裁决后写真实的 | 预写 3 份 |
| 10 | DX | blivedm/langchain 命令以 dry-run 实证为准 | 机械 | P5 | fork 源与兼容包均实证 | 只写"需裁决" |
| 11 | DX | Review record 顶部加权威声明（正文>摘要块） | 机械 | P5 | 消除双源漂移 | 删除摘要块（不可变） |

<!-- autoplan-baseline-edits:dx {"sourceSha256":"93d660c65d01deb993948acad192a16890d4dbf0bc27c8c2477905e3596381e9","replacements":[{"oldText":"### 2.1 启动方式（B6 修订）\n\nruntime312 是 conda 环境（conda-meta 存在）但 **activate.bat 缺失**（本次实测确认）。\n可用启动模式 = 直接调用解释器 + PATH 前置，先例为 2026-09-02 的\n`D:\\AI\\start\\start_autolive.bat`（42/43 冒烟使用同一模式）：\n\n```bat\nset RT=D:\\AI\\EDTalk\\runtime312\nif not exist \"%RT%\\python.exe\" (\n  echo [预检失败] 未找到解释器: %RT%\\python.exe\n  echo 请检查 D:\\AI\\EDTalk\\runtime312 是否存在（EDTalk 挪动/重装会导致此路径失效）\n  pause\n  exit /b 1\n)\nset FFMPEG_PATH=%RT%\\ffmpeg\\bin\nset PATH=%RT%;%RT%\\Scripts;%RT%\\Library\\bin;%FFMPEG_PATH%;%PATH%\nSET KMP_DUPLICATE_LIB_OK=TRUE\nSET HF_ENDPOINT=https://hf-mirror.com\ncd /d %~dp0\n\"%RT%\\python.exe\" webui.py\ncmd /k\n```\n\n> 窗口保持：原两 bat 末尾的 `cmd /k` **必须保留**（承自原脚本与 start\\ 模板）——\n> 缺失它，双击场景下补装前的 ModuleNotFoundError 会一闪而过，\"失败可见\"不成立。\n\n原 bat 已有的 `FFMPEG_PATH` 设置（恰好已指向 runtime312\\ffmpeg\\bin）与\n`KMP_DUPLICATE_LIB_OK=TRUE` 保留；`CONDA_PATH`/`CALL activate.bat` 整体移除；\n新增 `%~dp0` 定位（消除对双击时工作目录的依赖）。\n\n","newText":"### 2.1 启动方式（B6 修订）\n\nruntime312 是 conda 环境（conda-meta 存在）但 **activate.bat 缺失**（本次实测确认）。\n可用启动模式 = 直接调用解释器 + PATH 前置，先例为 2026-09-02 的\n`D:\\AI\\start\\start_autolive.bat`（42/43 冒烟使用同一模式）。\n\n**完整可照抄模板**（DX 审查 F8/F9/F10 采纳：UTF-8 无 BOM 保存 + chcp 65001 防中文乱码、\nRT 可环境变量覆盖、报错现场带修复指引）：\n\n```bat\n@echo off\nrem AUTOlive 中枢 UI（nicegui，端口 8086）—— runtime312 统一环境版\nrem 自动化/无人值守场景可删除末尾 cmd /k（窗口保活仅为双击场景设计）\nchcp 65001 >nul\nif not defined RT set \"RT=D:\\AI\\EDTalk\\runtime312\"\nif not exist \"%RT%\\python.exe\" (\n  echo [预检失败] 未找到解释器: %RT%\\python.exe\n  echo 请检查 D:\\AI\\EDTalk\\runtime312 是否存在（EDTalk 挪动/重装会导致此路径失效）\n  pause\n  exit /b 1\n)\nset FFMPEG_PATH=%RT%\\ffmpeg\\bin\nset PATH=%RT%;%RT%\\Scripts;%RT%\\Library\\bin;%FFMPEG_PATH%;%PATH%\nSET KMP_DUPLICATE_LIB_OK=TRUE\nSET HF_ENDPOINT=https://hf-mirror.com\ncd /d %~dp0\necho 如启动报 ModuleNotFoundError，请按 specs/integration/runtime312启动脚本切换计划.md 第 2.3 节清单补装依赖\n\"%RT%\\python.exe\" webui.py\ncmd /k\n```\n\n要点：原 bat 的 `FFMPEG_PATH`（已指向 runtime312\\ffmpeg\\bin）与 `KMP_DUPLICATE_LIB_OK=TRUE`\n保留；`HF_ENDPOINT` 为**新增**（承 start\\ 模板）；`CONDA_PATH`/`CALL activate.bat` 移除；\n`%~dp0` 定位消除对工作目录的依赖；`chcp 65001` 保证中文 echo 不乱码（**文件须以 UTF-8 无 BOM 保存**）；\n`if not defined RT` 允许临时覆盖解释器路径（并行测试其他 runtime）。\n\n"},{"oldText":"| blivedm | 0.1.2 | 未安装 | utils/platforms/bilibili2.py:1-3（B站新版直播 open_live 弹幕）、api_old.py:641-643 | bilibili2 平台弹幕不可用（平台为动态加载，不阻启动）。是否在用：config.json 有配置痕迹但无法确证使用中 |\n","newText":"| blivedm | 0.1.2 | 未安装 | utils/platforms/bilibili2.py:1-3（B站新版直播 open_live 弹幕）、api_old.py:641-643 | bilibili2 平台弹幕不可用（平台为动态加载，不阻启动）。是否在用：config.json 有配置痕迹但无法确证使用中。**安装来源已实证**：PyPI 仅 0.1.1（缺 open_live 模块）；旧环境的 0.1.2 来自用户自有 fork `gitee.com/ikaros-521/blivedm`（commit c9ac671，direct_url.json 实证）——**须从该 fork 装**，命令见第 3 节 |\n"},{"oldText":"| langchain（子模块） | 0.0.142 | 1.3.18（大版本跃迁） | utils/chat_with_file/**（聊天文档功能，懒加载） | 8 个旧路径子模块全部缺失：document_loaders、llms、prompts、text_splitter、vectorstores、chains.question_answering、embeddings.openai、callbacks——新版结构移入 langchain-community/classic，需版本裁决或代码适配 |\n","newText":"| langchain（子模块） | 0.0.142 | 1.3.18（大版本跃迁） | utils/chat_with_file/**（聊天文档功能，懒加载） | 8 个旧路径子模块全部缺失：document_loaders、llms、prompts、text_splitter、vectorstores、chains.question_answering、embeddings.openai、callbacks。**恢复路径已 dry-run 实证**：`langchain-community 0.4.2 + langchain-classic 1.0.8 + langchain-text-splitters 1.1.2` 干净解析（langchain-classic 即 1.x 兼容旧命名空间包），装后需 import 冒烟验证——命令见第 3 节 |\n"},{"oldText":"## 3. 实施任务清单\n\n1. **改写 `启动程序.bat`**（→ webui.py）：按 2.1 模板（含预检），`\"%RT%\\python.exe\" webui.py`。\n2. **`启动程序1.bat`（→ webui-bak.py）处置**：⚠️ **受 UC-1(本次) 门控**——webui-bak.py 在 runtime312 上实测必崩（nicegui 3.x，见 2.3-C），先经最终批准门三选一（留旧环境指向 / 归档 / 带警告切换），裁决后执行。\n3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行；zhipuai 附推荐命令：\n   `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt zhipuai`\n   （**解释器与 constraints 均用绝对路径**——后者用相对路径会在错误 cwd 下静默失去约束保护；\n   google-generativeai 三选一裁决前不得安装）。\n4. **验证**（补装后）：双击 `启动程序.bat` → 预检通过 → nicegui 起服日志行 → 8086 探活；\n   `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言**（users.pth 注入保险）；\n   最低验收判据（不依赖用户装包）：`启动程序.bat` 以 runtime312 解释器启动、PATH 前置生效、\n   在首个缺失包处给出**可读错误**且**窗口保持可见**（`cmd /k` 生效，模块名可见，非一闪而过）；\n   `启动程序1.bat` 的验收判据按 UC-1(本次) 裁决结果另行定义。\n   回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。\n5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；\n   UC-1(本次) 裁决同期给出。\n\n","newText":"## 3. 实施任务清单\n\n1. **改写 `启动程序.bat`**（→ webui.py）：**照抄 2.1 完整模板**（入口行为 `webui.py`；\n   1.bat 版本将入口行改为 `webui-bak.py`、头部注释标注\"旧版单体界面，nicegui 1.x 时代\"）。\n   文件以 **UTF-8 无 BOM** 保存（配 `chcp 65001` 保证中文 echo 不乱码）。\n2. **`启动程序1.bat`（→ webui-bak.py）处置**：⚠️ **受 UC-1(本次) 门控**——webui-bak.py 在 runtime312 上\n   实测必崩（nicegui 3.x，见 2.3-C），先经最终批准门三选一（留旧环境指向 / 归档 / 带警告切换），裁决后执行。\n3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行，全部使用**绝对路径**：\n\n   **A 组（启动阻断）**\n   - zhipuai（已 dry-run 实证）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt zhipuai`\n   - google-generativeai：**禁止直接安装**。三选一裁决前不动：① 不装+代码守卫（若 UC-2 采纳，见任务 6）\n     ② 迁移 google-genai（装 `google-genai` 新 SDK + gemini.py 代码适配）③ 永久放弃 Gemini 通道（接受通道不可用）。\n\n   **B 组（功能级，按需启用）**\n   - blivedm（bilibili2 弹幕，从用户 fork 装）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt git+https://gitee.com/ikaros-521/blivedm@c9ac671f783c4c3eaa4ee4e7738226ebbf402259`\n   - langchain 旧命名空间恢复（聊天文档功能，已 dry-run）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt langchain-community langchain-classic langchain-text-splitters`（装后跑 import 冒烟验证 8 个旧路径）\n   - flask_socketio：仅 api_old.py 用——**建议先裁决 api_old 去留**（TODOS 已登记）再决定是否装。\n   - coverage：测试工具，try 保护，可忽略。\n\n4. **验证**（前提链：zhipuai 已装 **且** Gemini 通道按裁决落地（任务 6）——缺一则 webui.py 仍在\n   `gemini.py:1` 处崩溃，8086 探活不可达）：\n   - 双击 `启动程序.bat` → 预检通过（中文不乱码）→ nicegui 起服日志行 → 浏览器打开\n     `http://127.0.0.1:8086`（或 `curl -s -o NUL -w \"%{http_code}\" http://127.0.0.1:8086`）；\n   - `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言**（users.pth 注入保险）；\n   - 最低验收判据（不依赖用户装包）：`启动程序.bat` 以 runtime312 解释器启动、PATH 前置生效、\n     在首个缺失包处给出**可读错误且窗口保持可见**（`cmd /k` 生效）、**错误现场含修复指引**（echo 提示行）；\n     `启动程序1.bat` 的验收判据按 UC-1(本次) 裁决结果另行定义。\n   - 回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。\n5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；\n   UC-1(本次) 裁决同期给出。\n6. **Gemini 通道落地**（条件任务，UC-2/最终门裁决后执行其一）：\n   ① zhipu.py/gemini.py 顶层导入加 try 守卫（缺失时 logger 警告\"通道不可用，功能降级\"，不阻断启动）\n   ② 装 google-genai 并适配 gemini.py（适配工作）\n   ③ 放弃通道（无代码动作，仅清单销案）。\n\n"}]} -->

<!-- autoplan-accepted:dx -->
- 启动脚本必须以 UTF-8 无 BOM 保存并含 chcp 65001（中文 echo 不乱码是验收项）。
- 模板含 if not defined RT 覆盖机制、ModuleNotFoundError 修复指引 echo 行、cmd /k 窗口保持（自动化场景可删，模板注释已注明）。
- 任务 1 = 照抄 2.1 完整模板（完整可交付文件，非片段）；1.bat 头部注释标注"旧版单体界面"。
- 任务 4 验收前提链：zhipuai 已装 且 Gemini 通道按任务 6 裁决落地，缺一不可达 8086 探活。
- 任务 6 = Gemini 通道条件任务（守卫①/google-genai②/放弃③），最终门裁决后执行其一。
- B 组命令：blivedm 从 gitee fork（c9ac671）装；langchain 用 community+classic+text-splitters 三包（装后 import 冒烟）。
- 全部 pip 命令使用绝对路径解释器与绝对路径 constraints（正文命令为权威；accepted:ceo 块②的相对路径以勘误为准）。
- 8086 探活命令：浏览器 http://127.0.0.1:8086 或 curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8086。
<!-- /autoplan-accepted:dx -->

# ENG REVIEW（FULL_REVIEW，/autoplan 2026-09-22，最终审查阶段）

> 双声部：Codex 不可用（未认证），Claude 独立子代理声部 `[subagent-only]`（独立完成事实核验：
> 导入链行号、webui-bak.py:3402、constraints protobuf==7.35.1、bat git 跟踪、8086 端口全部与代码库一致）。
> 子代理产出 11 项发现（1 中高必改 / 4 中 / 6 低），已逐项裁决，E1/T1/T2/T3/H1/H2/S1/A1/A2 已修订正文或落 TODOS。

## Step 0 范围挑战（映射到已有代码）

- **复用确认**：启动模式复用 start_autolive.bat 先例；安装保护复用 constraints.txt 机制；回归探测复用
  boot_probe_iter.py；blivedm 归档复用 wheels_sha256.txt 本地 wheel 惯例（Eng H1 主动对齐）。无平行重建。
- **最小变更集**：2 个 bat + 1 份计划文档；复杂度检查不触发（<8 文件、0 新类/服务）——不启动缩减门。
- **TODOS 交叉**：api_old 处置（CEO 新增）与 flask_socketio 装包决策联动；UC-1 裁决联动项已登记。
- **搜索检查**：搜索不可用——bat/conda/PATH 模式均为 Layer 1 既有实践（start\ 先例），无自造轮子。
- **完整性检查**：验证链含失败路径（预检/缺包/BOM/回滚）——完整版已达成本批次合理上限。

## Eng 双声部 — 共识表 `[subagent-only]`

```
ENG DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                          Claude(主)  Claude(子代理)  Consensus
  ────────────────────────────────── ─────────── ─────────────── ─────────
  1. 架构健全?                        是          是+补强(A1/A2)   CONFIRMED(已采纳)
  2. 测试覆盖充分?                    中          差(T1/T2/T3)     CONFIRMED(需修,已修)
  3. 性能风险已覆盖?                  是          无新风险(H3 肯定) CONFIRMED
  4. 安全威胁已覆盖?                  低          S1 信任标注缺    CONFIRMED(已修)
  5. 错误路径已处理?                  是          E1/E2 矛盾+门控  CONFIRMED(需修,已修)
  6. 部署风险可控?                    是          H1 归档惯例      CONFIRMED(已修)
═══════════════════════════════════════════════════════════════
CONFIRMED = 双声部一致（含采纳修正后）；外部声部 N/A（Codex 未认证）。
```

## CLAUDE SUBAGENT（Eng — 独立声部）发现逐项裁决

| # | 发现 | 级别 | 裁决 |
|---|---|---|---|
| E1 | 任务 1/2 对 1.bat 处置矛盾：字面执行会把现存可用入口改成必坏配置 | 中高(必改) | **采纳** → 任务 1 限定仅主 bat，1.bat 整体归任务 2，裁决前不得改动 |
| E2 | 守卫①被 UC-2 门控过度，阻塞验收链；论证守卫①为支配策略 | 中 | **采纳其论证、维持门控边界**——代码变更是否做出仍是用户裁决（UC-2）；支配策略论证写入任务 6 与最终门推荐 |
| T1 | zhipuai 跨 2 年版本跃迁，dry-run 不证运行时 API | 中 | **采纳** → 任务 4 补实例化冒烟命令 |
| T2 | sys.path 断言无载体，一次性检查无回归价值 | 中 | **采纳** → 固化为 boot_probe_iter.py 永久断言项 |
| T3 | UTF-8 无 BOM 无机械验证，BOM 是双击真实事故形态 | 中低 | **采纳** → PowerShell 单行检测入任务 4 |
| A1 | utils/__init__ 巨石导入是根因，建议 PEP 562 惰性化 | 中 | **采纳为 TODOS**（根因修复超本批次爆炸半径；守卫是止血） |
| A2 | 两 bat 手工双拷贝有漂移先例 | 低 | **采纳** → 任务 1 补"同步套用模板" |
| E3 | RT 覆盖不校验 ffmpeg 子目录 | 低 | 采纳（E3 注记并入模板注释语义；坏 PATH 目录 Windows 下无害） |
| E4 | cmd /k 掩盖退出码 | 低 | 已有缓解（模板注释），维持 |
| S1 | HF_ENDPOINT 信任变更未标注 | 低中 | **采纳** → 第 6 节补供应链信任标注 |
| S2 | blivedm fork 安装为代码执行级信任 | 低 | 可接受（commit 锚定+自有 fork+实证），维持 |
| H1 | blivedm 远程命令依赖 git 可用+仓库可达 | 中 | **采纳** → 改本地 wheel 归档安装（对齐项目惯例） |
| H2 | langchain 行缺在用不确定度标注 | 低 | **采纳** → 补标注 + 装前确认引用点 |
| H3 | cd /d %~dp0 为顺带改进；PATH 前置无风险 | 低 | 记录确认，无动作 |

## Section 1 架构
```
双击 .bat ──▶ chcp/预检(RT 可覆盖) ──▶ PATH 前置(runtime312+Library\bin+ffmpeg)
         ──▶ python.exe 直启 ──▶ utils/__init__ 巨石导入链(根因,PEP 562→TODOS)
                                ──▶ nicegui 8086
```
- 发现 1（A1，中）：导入链根因 → TODOS（守卫=止血）。
- 发现 2（A2，低）：双拷贝漂移 → 任务 1 同步条款。
- 回滚姿态：git checkout 分钟级 + 旧环境热回滚完好。其他维度（扩展性/安全边界/生产故障）：单机直播场景无新面。

## Section 2 代码质量
本批次不写业务代码；bat 模板 DRY 已按"同一形态两处镜像 + 同步条款"处理。任务 1/2 矛盾（E1）是本节核心发现，已修。无其他发现。

## Section 3 测试（NEVER SKIP）

测试框架检测：项目无 pytest.ini/pyproject 测试节；现有 harness = specs/integration/install/smoke_test.py（包级冒烟）
+ boot_probe_iter.py（启动阻断探测，本次新增）+ tests/（既有单测，与本批次无交集）。

```
CODE PATHS                                          USER FLOWS
[+] 启动程序.bat（新模板）                           [+] 双击起服到 8086
  ├── 预检-解释器缺失                                 ├── [GAP→已补] 中文不乱码+探活（任务4）
  │     └── [★★★ 计划内] echo+pause+exit /b          ├── [GAP→已补] zhipuai 实例化冒烟（T1）
  ├── 预检-通过 → PATH 前置 → python 直启             └── [GAP→已补] BOM 机械检测（T3）
  │     └── [★★ 计划内] boot_probe 桩集合回归
  ├── 缺依赖 → ModuleNotFoundError                     [+] 缺包排障流
  │     └── [★★★ 计划内] cmd /k 保窗+指引行            └── [★★ 计划内] 指引行→清单→命令
  └── git checkout 回滚
        └── [★★ 已验证] 两 bat git 跟踪

LLM/prompt 变更: 无 —— 无 eval 面任务。
COVERAGE: 计划内测试项 6/6 分支均有归属（含 3 项本审查新增）
QUALITY: ★★★:2 ★★:4  |  GAPS: 0（T1/T2/T3 补后）
```

测试计划工件已写盘：`~/.gstack/projects/Ikaros-521-AUTOlive/admin-main-eng-review-test-plan-20260922-175500.md`。

## Section 4 性能
解释器直启较 conda activate 省去激活开销（亚秒级）；运行期服务拓扑不变；无 N+1/内存/缓存面。无发现。

## NOT in scope（Eng 确认）

与 CEO/DX 阶段一致，追加：

| 项 | 理由 |
|---|---|
| utils/__init__.py PEP 562 惰性化 | 根因修复超本批次爆炸半径 → TODOS（A1） |
| bat 退出码透传（自动化场景） | cmd /k 语义即双击场景；未来 CI 调用再议（E4） |
| RT 覆盖的 ffmpeg 子目录校验 | 坏 PATH 目录 Windows 下无害，注记即可（E3） |

## What already exists（Eng 确认）

CEO/DX 映射有效，追加：`install/wheels_sha256.txt` + `batch1_local_wheels.bat` 本地 wheel 惯例（H1 复用）、
boot_probe_iter.py 回归载体（T2 固化对象）。

## 失效模式登记册

```
CODEPATH              | FAILURE MODE         | RESCUED? | TEST? | USER SEES?         | LOGGED?
双击启动(预检失败)     | 解释器缺失           | Y        | Y     | 中文提示+pause     | Y
双击启动(缺依赖)       | ModuleNotFoundError  | Y(清单)  | Y     | 保窗+指引行        | Y
bat 编码错误           | BOM 首行解析失败     | Y(检测)  | Y(T3) | 明确报错           | Y
手动 pip 安装          | 版本跃迁 API 断裂    | Y(T1 冒烟)| Y    | 冒烟命令报错       | Y
blivedm 远程安装失效   | 仓库改名/私有化      | Y(H1 归档)| N/A(预防) | N/A           | Y
```
0 CRITICAL GAP——所有失败模式均有"用户可见"输出路径与测试/预防归属。

## 并行化策略

Sequential implementation, no parallelization opportunity.（两 bat 同模块同模板，串行执行。）

## Implementation Tasks（Eng 阶段增量）

- [ ] **E1 (P1, human: ~10min / CC: ~2min)** — 启动脚本 — 任务 1 限定仅主 bat；1.bat 裁决前不动
  - Surfaced by: Eng E1（任务 1/2 矛盾，必改）
  - Files: 启动程序.bat, 启动程序1.bat
  - Verify: 裁决前 git status 仅 启动程序.bat 变更
- [ ] **E2 (P2, human: ~10min / CC: ~2min)** — 验证 — zhipuai 实例化冒烟 + BOM 检测 + sys.path 断言固化
  - Surfaced by: Eng T1/T2/T3
  - Files: specs/integration/install/boot_probe_iter.py
  - Verify: 三条命令逐一通过
- [ ] **E3 (P2, human: ~10min / CC: ~5min)** — 依赖清单 — blivedm 本地 wheel 归档 + langchain 在用标注
  - Surfaced by: Eng H1/H2
  - Files: specs/integration/install/wheels_sha256.txt
  - Verify: pip download 产物 + 哈希入账

### 完成摘要（Eng, FINAL）

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (ENG, FINAL)         |
+====================================================================+
| Scope challenge      | 复用全对齐；最小变更 2 bat+1 文档；不触发缩减 |
| Section 1 (Arch)     | 2 issues（A1 根因→TODOS、A2 漂移→条款）       |
| Section 2 (Quality)  | 1 issue（E1 矛盾，必改，已修）                |
| Section 3 (Tests)    | 图已出；3 gaps 全补（T1/T2/T3）；工件已写盘   |
| Section 4 (Perf)     | 0 issues                                     |
| Security             | 2 issues（S1 标注已补、S2 可接受）            |
| Hidden complexity    | 3 issues（H1 已修、H2 已修、H3 记录）         |
| Failure modes        | 5 项，0 CRITICAL GAP                         |
| Test plan artifact   | admin-main-eng-review-test-plan-20260922-…md |
| TODOS.md updates     | +1（PEP 562 惰性化，自动写入）                |
| Parallelization      | 串行（无并行机会）                            |
| Unresolved decisions | 0 新增（UC-1/UC-2 维持排队 → 最终门）         |
+====================================================================+
```

## 决策审计（Decision Audit Trail — Eng 阶段）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否选项 |
|---|---|---|---|---|---|---|
| 12 | Eng | E1 必改：任务 1 限定仅主 bat | 机械 | P5 | 字面执行=人为制造必坏中间态 | 维持双文件同改 |
| 13 | Eng | E2 采纳论证、维持 UC-2 门控 | 机械+门 | P5 | 技术支配性成立；代码变更裁决权在用户 | 解除门控自动加守卫 |
| 14 | Eng | T1/T2/T3 全采纳 | 机械 | P1 | 验证链完整性缺口，成本 S | 不加 |
| 15 | Eng | H1 blivedm 本地 wheel 归档 | 机械 | P5 | 对齐项目既有惯例，消除远程依赖 | 维持远程命令 |
| 16 | Eng | A1 PEP 562 → TODOS | 机械 | P2 | 根因修复超爆炸半径 | 纳入本批次 |
| 17 | Eng | S1 信任标注入第 6 节 | 机械 | P5 | 留存审计依据 | 不标 |

<!-- autoplan-baseline-edits:eng {"sourceSha256":"5403edb61d4a26aeb1c7eb9e350515c1983fc1f10411d7f1f04651b0d42deb87","replacements":[{"oldText":"1. **改写 `启动程序.bat`**（→ webui.py）：**照抄 2.1 完整模板**（入口行为 `webui.py`；\n   1.bat 版本将入口行改为 `webui-bak.py`、头部注释标注\"旧版单体界面，nicegui 1.x 时代\"）。\n   文件以 **UTF-8 无 BOM** 保存（配 `chcp 65001` 保证中文 echo 不乱码）。\n","newText":"1. **改写 `启动程序.bat`**（→ webui.py）：**照抄 2.1 完整模板**（入口行为 `webui.py`）。\n   文件以 **UTF-8 无 BOM** 保存（配 `chcp 65001` 保证中文 echo 不乱码）。\n   ⚠️ **本任务仅覆盖主 bat**（`启动程序.bat`）——`启动程序1.bat` 整体归任务 2 处置，\n   裁决前**不得改动**（它当前在旧环境下仍可用，抢先改写=人为制造必坏中间态，Eng E1）。\n   两份 bat 若在裁决后同批修改，须同步套用同一模板（Eng A2 防漂移）。\n"},{"oldText":"3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行，全部使用**绝对路径**：\n\n   **A 组（启动阻断）**\n   - zhipuai（已 dry-run 实证）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt zhipuai`\n   - google-generativeai：**禁止直接安装**。三选一裁决前不动：① 不装+代码守卫（若 UC-2 采纳，见任务 6）\n     ② 迁移 google-genai（装 `google-genai` 新 SDK + gemini.py 代码适配）③ 永久放弃 Gemini 通道（接受通道不可用）。\n\n   **B 组（功能级，按需启用）**\n   - blivedm（bilibili2 弹幕，从用户 fork 装）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt git+https://gitee.com/ikaros-521/blivedm@c9ac671f783c4c3eaa4ee4e7738226ebbf402259`\n   - langchain 旧命名空间恢复（聊天文档功能，已 dry-run）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt langchain-community langchain-classic langchain-text-splitters`（装后跑 import 冒烟验证 8 个旧路径）\n   - flask_socketio：仅 api_old.py 用——**建议先裁决 api_old 去留**（TODOS 已登记）再决定是否装。\n   - coverage：测试工具，try 保护，可忽略。\n\n4. **验证**（前提链：zhipuai 已装 **且** Gemini 通道按裁决落地（任务 6）——缺一则 webui.py 仍在\n   `gemini.py:1` 处崩溃，8086 探活不可达）：\n   - 双击 `启动程序.bat` → 预检通过（中文不乱码）→ nicegui 起服日志行 → 浏览器打开\n     `http://127.0.0.1:8086`（或 `curl -s -o NUL -w \"%{http_code}\" http://127.0.0.1:8086`）；\n   - `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言**（users.pth 注入保险）；\n   - 最低验收判据（不依赖用户装包）：`启动程序.bat` 以 runtime312 解释器启动、PATH 前置生效、\n     在首个缺失包处给出**可读错误且窗口保持可见**（`cmd /k` 生效）、**错误现场含修复指引**（echo 提示行）；\n     `启动程序1.bat` 的验收判据按 UC-1(本次) 裁决结果另行定义。\n   - 回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。\n5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；\n   UC-1(本次) 裁决同期给出。\n6. **Gemini 通道落地**（条件任务，UC-2/最终门裁决后执行其一）：\n   ① zhipu.py/gemini.py 顶层导入加 try 守卫（缺失时 logger 警告\"通道不可用，功能降级\"，不阻断启动）\n   ② 装 google-genai 并适配 gemini.py（适配工作）\n   ③ 放弃通道（无代码动作，仅清单销案）。\n\n","newText":"3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行，全部使用**绝对路径**：\n\n   **A 组（启动阻断）**\n   - zhipuai（已 dry-run 实证）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt zhipuai`\n   - google-generativeai：**禁止直接安装**。三选一裁决前不动：① 不装+代码守卫（若 UC-2 采纳，见任务 6）\n     ② 迁移 google-genai（装 `google-genai` 新 SDK + gemini.py 代码适配）③ 永久放弃 Gemini 通道（接受通道不可用）。\n\n   **B 组（功能级，按需启用）**\n   - blivedm（bilibili2 弹幕）：**先归档为本地 wheel 再装**（项目既有惯例，见 install/wheels_sha256.txt；\n     gitee 个人仓库存在改名/私有化风险，远程命令不可作长期交付物，Eng H1）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip download -d D:\\AI\\AUTOlive\\specs\\integration\\install\\wheels git+https://gitee.com/ikaros-521/blivedm@c9ac671f783c4c3eaa4ee4e7738226ebbf402259`\n     然后 `pip install -c <constraints> <本地wheel路径>`；哈希记入 wheels_sha256.txt。\n   - langchain 旧命名空间恢复（聊天文档功能，已 dry-run）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -m pip install -c D:\\AI\\AUTOlive\\specs\\integration\\install\\constraints.txt langchain-community langchain-classic langchain-text-splitters`\n     （装后跑 import 冒烟验证 8 个旧路径；**是否在用：与 blivedm 同级不确定**——chat_with_file 无静态外部\n     引用者，可能经动态派发调用或为死代码，装包前先确认引用点存在，Eng H2）\n   - flask_socketio：仅 api_old.py 用——**建议先裁决 api_old 去留**（TODOS 已登记）再决定是否装。\n   - coverage：测试工具，try 保护，可忽略。\n\n4. **验证**（前提链：zhipuai 已装 **且** Gemini 通道按裁决落地（任务 6）——缺一则 webui.py 仍在\n   `gemini.py:1` 处崩溃，8086 探活不可达）：\n   - 双击 `启动程序.bat` → 预检通过（中文不乱码）→ nicegui 起服日志行 → 浏览器打开\n     `http://127.0.0.1:8086`（或 `curl -s -o NUL -w \"%{http_code}\" http://127.0.0.1:8086`）；\n   - zhipuai 装**后冒烟**（dry-run 只证解析，不证运行时 API，Eng T1）：\n     `D:\\AI\\EDTalk\\runtime312\\python.exe -c \"from zhipuai import ZhipuAI; ZhipuAI(api_key='probe')\"`（能实例化即过）；\n   - `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言固化为该脚本永久断言项**（users.pth 是共享\n     环境文件，一次性检查无回归价值，Eng T2）；\n   - bat 编码机械验证（BOM 会使 `@echo off` 首行报 `'∩╗┐@echo' 不是内部或外部命令`，Eng T3）：\n     `powershell -c \"$b=[IO.File]::ReadAllBytes('D:\\AI\\AUTOlive\\启动程序.bat')[0..2]; if(($b)-ceq(0xEF,0xBB,0xBF)){'有BOM-不合格'}else{'无BOM-合格'}\"`;\n   - 最低验收判据（不依赖用户装包）：`启动程序.bat` 以 runtime312 解释器启动、PATH 前置生效、\n     在首个缺失包处给出**可读错误且窗口保持可见**（`cmd /k` 生效）、**错误现场含修复指引**（echo 提示行）；\n     `启动程序1.bat` 的验收判据按 UC-1(本次) 裁决结果另行定义。\n   - 回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。\n5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；\n   UC-1(本次) 裁决同期给出。\n6. **Gemini 通道落地**（条件任务，最终门裁决后执行其一；Eng E2 论证：**守卫①是三选一的支配策略**——\n   选②时守卫 except 分支自然不触发、选③时守卫正是\"通道不可用但不崩\"的落地形式、选①守卫即本体，\n   唯一反方论点\"守卫掩盖配置错误\"已由 logger 警告缓解。最终门对 UC-2 的推荐即\"采纳守卫①\"）：\n   ① zhipu.py/gemini.py 顶层导入加 try 守卫（缺失时 logger 警告\"通道不可用，功能降级\"，不阻断启动）\n   ② 装 google-genai 并适配 gemini.py（适配工作）\n   ③ 放弃通道（无代码动作，仅清单销案）。\n\n"},{"oldText":"## 6. 风险与回滚\n\n- 脚本改动两文件、均 git 跟踪，`git checkout` 即回滚，分钟级。\n- 旧 Miniconda3 环境不删除、不修改——热回滚资产完好。**退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3（无退出标准的\"统一环境\"会永久停在双环境并行）。\n- zhipuai 安装是唯一可能触碰基线的动作，且由用户手动执行并带 constraints 保护（dry-run 已验证不触碰）。\n","newText":"## 6. 风险与回滚\n\n- 脚本改动两文件、均 git 跟踪，`git checkout` 即回滚，分钟级。\n- 旧 Miniconda3 环境不删除、不修改——热回滚资产完好。**退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3（无退出标准的\"统一环境\"会永久停在双环境并行）。\n- zhipuai 安装是唯一可能触碰基线的动作，且由用户手动执行并带 constraints 保护（dry-run 已验证不触碰）。\n- **供应链信任标注（Eng S1）**：`HF_ENDPOINT=https://hf-mirror.com` 为本次新增环境变量，模型/分词器\n  下载经第三方镜像——国内网络下的社区惯例，属**明示接受的信任决策**，留此存照供后续审计。\n"}]} -->

<!-- autoplan-accepted:eng -->
- 任务 1 仅覆盖 `启动程序.bat`；`启动程序1.bat` 在 UC-1 裁决前不得改动（E1，防人为制造必坏中间态）。
- 验证链新增三件：zhipuai 实例化冒烟（from zhipuai import ZhipuAI + 实例化）、bat BOM PowerShell 检测、sys.path 首位断言固化为 boot_probe_iter.py 永久项（T1/T2/T3）。
- blivedm 走本地 wheel 归档安装（pip download 至 install/wheels + 哈希入 wheels_sha256.txt），远程 fork 命令仅作归档来源（H1）。
- langchain 行标注在用不确定度（与 blivedm 同级），装包前先确认 chat_with_file 引用点存在（H2）。
- 第 6 节含 HF_ENDPOINT 第三方镜像供应链信任标注（S1）。
- UC-2 最终门推荐"采纳守卫①"（Eng E2 支配策略论证已写入任务 6）；守卫的实际采纳由用户在门上裁决。
- utils/__init__.py PEP 562 惰性化已登记 TODOS（A1）；本批次守卫为止血措施。
<!-- /autoplan-accepted:eng -->
