<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Ikaros-521-AI-Vtuber\\main-autoplan-restore-20260922-165941.md" -->
## Implementation plan
# AI-Vtuber 启动脚本切换 runtime312（Python 3.12）+ 依赖缺口清单

> 生成日期：2026-09-22 ｜ 任务来源：用户指令（/autoplan）
> 目标：将 `启动程序.bat` / `启动程序1.bat` 从 Miniconda3（activate.bat 激活）切换为
> `D:\AI\EDTalk\runtime312`（Python 3.12.12，**无 activate.bat**，直接解释器启动），
> 分别运行 `webui.py` / `webui-bak.py`；缺失依赖**不安装**，调查使用方模块并列清单由用户手动补装。

## 1. 背景与现状

- 2026-09-02 已完成六项目统一环境整合（见 `specs/integration/统一环境依赖整合计划.md`）：
  runtime312 补装 ~130 包、冒烟 42/43、统一启动脚本建于 `D:\AI\start\`（并行期模式，旧 .bat 未动）。
- 本次为用户明确指令：**直接修改 AI-Vtuber 自有的两个启动脚本**（不再走并行期新建模式），
  并产出依赖缺口清单。
- 旧环境：`D:\AI\AI-Vtuber\Miniconda3`（Python 3.10.11）；新环境：`D:\AI\EDTalk\runtime312`
  （Python 3.12.12，conda 环境但 `Scripts\activate.bat` 已不存在——实测验证，grep 无结果）。

## 2. 调查结果（关键事实，均已实测）

### 2.1 启动方式（B6 修订）

runtime312 是 conda 环境（conda-meta 存在）但 **activate.bat 缺失**（本次实测确认）。
可用启动模式 = 直接调用解释器 + PATH 前置，先例为 2026-09-02 的
`D:\AI\start\start_aivtuber.bat`（42/43 冒烟使用同一模式）：

```bat
set RT=D:\AI\EDTalk\runtime312
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
cd /d <脚本所在目录>
"%RT%\python.exe" webui.py
```

原 bat 已有的 `FFMPEG_PATH` 设置（恰好已指向 runtime312\ffmpeg\bin）与
`KMP_DUPLICATE_LIB_OK=TRUE` 保留；`CONDA_PATH`/`CALL activate.bat` 整体移除；
新增 `%~dp0` 定位（消除对双击时工作目录的依赖）。

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
| blivedm | 0.1.2 | 未安装 | utils/platforms/bilibili2.py:1-3（B站新版直播 open_live 弹幕）、api_old.py:641-643 | bilibili2 平台弹幕不可用（平台为动态加载，不阻启动） |
| flask_socketio | — | 未安装 | api_old.py:16 | 仅旧版 api 入口使用，webui 两入口不用 |
| langchain（子模块） | 0.0.142 | 1.3.18（大版本跃迁） | utils/chat_with_file/**（聊天文档功能，懒加载） | 8 个旧路径子模块全部缺失：document_loaders、llms、prompts、text_splitter、vectorstores、chains.question_answering、embeddings.openai、callbacks——新版结构移入 langchain-community/classic，需版本裁决或代码适配 |
| coverage | — | 未安装 | run_tests.py:31（try 保护） | 仅测试工具，可忽略 |

**C. 非依赖阻断（装包解决不了）**

- `webui-bak.py` 是按 nicegui 1.4.30 写的单体旧版；runtime312 是 nicegui 3.16.0。
  实测 `webui-bak.py:3402` `ui.select(options={'custom_llm':...}, value='chatgpt')` 抛
  `ValueError: Invalid value: chatgpt`（1.x 静默容忍非法 value，3.x 严格校验）。
  即使补齐全部依赖，webui-bak.py 也无法出界面，需逐处代码适配（属"后期适配"范围）。
- `webui.py`（模块化新版）桩测 **BOOT_OK**，是可正常启动的入口。

### 2.4 环境附带发现（记录在案）

- runtime312 的 `Lib\site-packages\users.pth` 硬编码注入 6 个 GPT-SoVITS 路径进所有进程
  （潜在模块遮蔽风险；当前两入口未受影响，AI-Vtuber 自有目录在 sys.path 前列）。
- 基础栈健康（干净进程逐项验证）：torch 2.11.0+cu128、numpy 2.4.6、requests 2.34.2、
  urllib3 1.26.20、socketio、nicegui 3.16.0 全部 import OK。

## 3. 实施任务清单

1. **改写 `启动程序.bat`**（→ webui.py）：按 2.1 模板（含预检），`"%RT%\python.exe" webui.py`。
2. **`启动程序1.bat`（→ webui-bak.py）处置**：⚠️ **受 UC-1(本次) 门控**——webui-bak.py 在 runtime312 上实测必崩（nicegui 3.x，见 2.5），先经最终批准门三选一（留旧环境指向 / 归档 / 带警告切换），裁决后执行。
3. **产出依赖补装清单**（本文件 2.3 节）交付用户手动执行；zhipuai 附推荐命令：
   `D:\AI\EDTalk\runtime312\python.exe -m pip install -c specs/integration/install/constraints.txt zhipuai`
   （**必须用绝对路径解释器**，防 pip 打错环境；google-generativeai 三选一裁决前不得安装）。
4. **验证**（补装后）：双击 `启动程序.bat` → 预检通过 → nicegui 起服日志行 → 8086 探活；
   `boot_probe_iter.py` 回归（桩集合应为空）+ **sys.path 首位断言**（users.pth 注入保险）；
   最低验收判据（不依赖用户装包）：两脚本以 runtime312 解释器启动、PATH 前置生效、
   在首个缺失包处给出**可读错误**（模块名可见，非黑屏 traceback）。
   回滚方式：`git checkout -- 启动程序.bat 启动程序1.bat`（两文件均为 git 跟踪文件，旧环境未动）。
5. **Miniconda3 退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3；
   UC-1(本次) 裁决同期给出。

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
| runtime312 无 activate.bat 的启动模式 | D:\AI\start\start_aivtuber.bat（PATH 前置） | 是（模板照抄） |
| 安装保护机制 | specs/integration/install/constraints.txt | 是（清单附命令引用） |
| 冒烟/探测方法 | install/smoke_test.py、boot_probe_iter.py（本次新增） | 是 |
| 旧环境回滚 | git 跟踪的两个 .bat + Miniconda3 未动 | 是 |

## 6. 风险与回滚

- 脚本改动两文件、均 git 跟踪，`git checkout` 即回滚，分钟级。
- 旧 Miniconda3 环境不删除、不修改——热回滚资产完好。**退役线**：runtime312 稳定运行 2 周 + 功能冒烟通过 → 归档 Miniconda3（无退出标准的"统一环境"会永久停在双环境并行）。
- zhipuai 安装是唯一可能触碰基线的动作，且由用户手动执行并带 constraints 保护（dry-run 已验证不触碰）。

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
## Review record

# CEO REVIEW（SELECTIVE_EXPANSION，/autoplan 2026-09-22）

> 双声部：Codex 不可用（未认证，`codex login` 可恢复），Claude 独立子代理声部 `[subagent-only]`。
> 子代理产出 14 项发现 + "有条件放行"裁决（3 条修正前置），已逐项裁决并入下文。

## 0A 前提挑战

| # | 前提 | 评估 | 结论 |
|---|---|---|---|
| P1 | runtime312 无 activate.bat，需解释器直启 | 已实证：conda-meta 存在但 Scripts\activate.bat 缺失（grep 无结果） | 接受 |
| P2 | start_aivtuber.bat 的 PATH 前置模式适用于自有 bat | 同一环境同一模式，42/43 冒烟先例 | 接受 |
| P3 | 两 bat 均 git 跟踪、可回滚 | 已实证：`git ls-files` 命中两文件 | 接受（子代理发现 5 已验证） |
| P4 | zhipuai 可装且兼容 | **已实证（子代理发现 4 采纳）**：`pip install --dry-run -c constraints.txt zhipuai` 干净解析，装 zhipuai 2.1.5.20250825（2025-08 新版，比旧环境 2023 版新）+ PyJWT 2.8.0，不触碰基线包 | 接受（假设已变证据） |
| P5 | webui.py BOOT_OK = 可用 | 模块级验证；nicegui 服务真实起服、页面渲染未端到端验证——列入验证步骤 | 接受（带缓解） |
| P6 | "调整两个脚本"=字面执行 | **子代理发现 7/11 证伪其意图**：1.bat 指向实测必坏入口（nicegui 3.x），忠实执行产出坏脚本。意图是"两个入口在新环境可用" | 修正 → **UC-1 留门** |

无其他明显错误前提。UC-1/UC-2 排队至最终批准门（见 0D）。

## 0B 已有资产复用

- `D:\AI\start\start_aivtuber.bat`（解释器直启模板）——照抄模式。
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
本计划不改业务代码（UC-2 未决前）；bat 模板与 start_aivtuber.bat 一致（DRY：同一形态两处镜像，差异仅入口文件名——可接受的最小重复）。无发现。

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

本计划完成后：AI-Vtuber 两个自有启动脚本脱离 conda 激活语义（12 个月理想态的"一键+健康检查"达成前半）；依赖缺口从"启动即崩不可见"变为"分级清单+使用方定位"；Miniconda3 首次有了可验证的退出标准。距理想态还差：UC-1（webui-bak 命运）、UC-2（守卫化）、start\ 脚本收敛三步，均已显式在账。

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

