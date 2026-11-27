from frontend.ui.components.config_helper import get_nested_value
"""
大语言模型标签页模块
从 webui-bak.py 的 llm_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
import asyncio
import os
import shutil
import subprocess
import threading

from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check
from frontend.config.paths import get_base_path
from utils.my_log import logger

# fastrag 文档上传（auto_upload=True：选择即上传触发 on_upload 落盘——
# 2026-10-29 实测修复：False+隐藏外壳时文件只进 Quasar 客户端队列，
# on_upload 永不触发、服务端零日志，落盘静默失败）


def create_llm_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """
    创建大语言模型标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
    """
    card_css = theme_config.get("card", "")

    # ================= 自定义LLM（C4 收敛：OpenAI-compatible 三字段+可选参数） =================
    with ui.card().style(card_css):
        ui.label("自定义LLM")

        with ui.grid(columns=3):
            # 服务地址
            FormField.create_input(
                label="服务地址（base_url）",
                value=get_nested_value(config, "custom_llm", "base_url"),
                placeholder="如 http://127.0.0.1:11434/v1 或 https://api.deepseek.com/v1",
                on_change=lambda e: set_config_callback("custom_llm", "base_url", value=e.value),
                tooltip='OpenAI-compatible 服务前缀，系统自动拼接 /chat/completions',
                style="width:100%;"
            )

            # 模型
            FormField.create_input(
                label="模型（model）",
                value=get_nested_value(config, "custom_llm", "model"),
                placeholder="如 qwen:latest / deepseek-chat",
                on_change=lambda e: set_config_callback("custom_llm", "model", value=e.value),
                tooltip='模型名（Ollama 用 ollama list 查看）',
                style="width:100%;"
            )

            # API Key（密码型输入）
            FormField.create_input(
                label="API Key",
                value=get_nested_value(config, "custom_llm", "api_key"),
                placeholder="云端平台必填；本地 Ollama 留空",
                on_change=lambda e: set_config_callback("custom_llm", "api_key", value=e.value),
                tooltip='以 Authorization: Bearer <key> 发送；Ollama 等本地服务无需填写',
                style="width:100%;"
            ).props("type=password")

        with ui.grid(columns=2):
            # 可选：max_tokens
            FormField.create_input(
                label="max_tokens（可选，留空不限制）",
                value=str(get_nested_value(config, "custom_llm", "max_tokens") or ""),
                placeholder="如 1024",
                on_change=lambda e: set_config_callback("custom_llm", "max_tokens", value=e.value),
                tooltip='回复最大 token 数；留空则不发送该参数',
                style="width:100%;"
            )
            # 可选：temperature
            FormField.create_input(
                label="temperature（可选，留空用服务默认）",
                value=str(get_nested_value(config, "custom_llm", "temperature") or ""),
                placeholder="如 0.7",
                on_change=lambda e: set_config_callback("custom_llm", "temperature", value=e.value),
                tooltip='采样温度 0-2；留空则不发送该参数',
                style="width:100%;"
            )

        # system 人设（2026-10-30 审查批次：对齐各平台 cURL 通用结构——messages
        # 首条 {"role":"system","content":...}；留空不发送该消息）
        ui.textarea(
            label='人设提示词（system，可选）',
            value=str(get_nested_value(config, "custom_llm", "system_prompt") or ""),
            placeholder='如：你是一位专业、友好的线上销售，现在正在进行直播。',
            on_change=lambda e: set_config_callback("custom_llm", "system_prompt", value=e.value),
        ).style("width:100%;").tooltip(
            '以 {"role": "system", "content": ...} 注入请求消息首位（对齐各平台 '
            'cURL 通用调用结构）；留空则请求仅含 user 消息。即时保存。')

        # {{prompt}} 变量说明（C4：请求模板退役，弹幕内容自动注入 user 消息）
        ui.label(
            "请求固定为 OpenAI-compatible 协议：POST {base_url}/chat/completions；"
            "弹幕内容自动作为 user 消息发送（等价旧版请求体模板中的 {{prompt}} 变量，无需再手写请求体）；"
            "响应固定解析 choices[0].message.content。"
            "各平台差异仅在 base_url / model / api_key 三项（人设提示词可选），示例见 docs/llm-request-template.md。"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")

    # ================= fastrag 知识库卡片（EDTalk功能集成计划 §6.1） =================
    _create_fastrag_card(config, theme_config, set_config_callback)


def _create_fastrag_card(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """fastrag 知识库 RAG 卡片。

    服务进程级开关（不进 ENABLE_PATHS）；server 由开关随系统启动；
    build-index 由『构建索引』按钮手动触发。端口真源 = config.fastrag.port，
    启动时经 FASTRAG__SERVER__PORT 环境变量注入（Eng 义务⑬，零 TOML 手术）。
    """
    card_css = theme_config.get("card", "")

    def _fastrag_dir() -> str:
        return os.path.join(str(get_base_path()), "edtalk", "fastrag")

    def _check_build_prereq() -> str | None:
        """构建前置校验（design 义务⑤：空目录提示）。"""
        kb = os.path.join(_fastrag_dir(), "knowledgebase")
        if not os.path.isdir(kb) or not any(
            f.endswith((".md", ".txt")) for f in os.listdir(kb)
        ):
            return "请先放入 .md/.txt 文件到 edtalk/fastrag/knowledgebase/ 目录"
        return None

    # 停服实现已收编至 frontend/main.py（AutoliveApp.stop_fastrag_server，
    # 2026-11-19：与应用重启共用同一 wmic/PowerShell 精确杀链路）

    # 渲染期捕获 client（2026-11-20 修复）：原实现调 nicegui.client.get()——
    # nicegui 3.16 已无该属性（module has no attribute 'get'，AttributeError
    # 被兜底吞掉→构建结果 toast 全部丢失只留日志）。对齐 trends_copywriting
    # 范式（2026-10-30）：后台线程通知须 `with client:` 显式进入渲染期捕获的
    # client 上下文；页面已刷新/断开则异常降级日志。
    from nicegui import context as _ng_context
    _build_client = _ng_context.client

    def _notify_alive(kind: str, message: str):
        """后台线程通知（client 存活守卫）：构建耗时 ~20s，期间页面刷新/重连
        会替换 NiceGUI client——向已删除 client 发通知会抛错（实测 log2.txt
        「Client has been deleted」，成功提示丢失致用户误判失败）。
        `with client:` 显式进入上下文；断开/异常由降级日志兜底，不外溢。"""
        try:
            with _build_client:
                ui.notify(position="top", type=kind, message=message)
        except Exception as _e:
            logger.info(f"[build-index] 前端通知失败（{kind}）：{message}（{_e}）")

    def _run_build_index():
        """后台线程：构建知识库索引（『构建索引』按钮）。"""
        from frontend.main import AutoliveApp
        try:
            import subprocess
            # 计划内停服（构建需释放索引句柄）：置标记屏噪——监控线程观测到
            # fastrag 退出时跳过「外围程序退出」告警（2026-11-19 修复误报）
            AutoliveApp._fastrag_planned_stop = True
            AutoliveApp.stop_fastrag_server()
            # 解释器解析与启动路径同源（Eng A1：resolve_interpreter 四级链，
            # 消除硬编码默认 llm.py:159 旧分叉——解释器挪位后构建/启动不再分裂）
            from utils.edtalk_realtime.runtime import resolve_interpreter
            interpreter = resolve_interpreter(config)
            if not interpreter or not os.path.exists(interpreter):
                _notify_alive("negative",
                              "problem=解释器未找到；cause=runtime312 缺失；fix=系统设置页配置 edtalk_realtime 解释器路径")
                return
            env = os.environ.copy()
            # 子进程中文编码：Windows 管道默认 GBK，父进程按 UTF-8 解读→
            # 控制台乱码（实测 log2.txt）；强制 UTF-8 对齐读侧
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            proc = subprocess.Popen(
                [interpreter, os.path.join(_fastrag_dir(), "fastrag-python", "build_index_native.py")],
                cwd=os.path.join(_fastrag_dir(), "fastrag-python"), shell=False,
                env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                logger.info(f"[build-index] {line.rstrip()}")
            code = proc.wait()
            if code == 0:
                _notify_alive("positive",
                              "索引构建完成。重启系统后新索引生效（启动物=python 路线；索引缺失时启动预检会指引先构建再重启）")
            else:
                _notify_alive("negative", f"构建失败（exit {code}），详见日志")
        except Exception as e:
            logger.error(f"构建索引异常：{e}")
            _notify_alive("negative", f"构建异常：{e}")
        finally:
            from frontend.main import AutoliveApp as _A
            _A._fastrag_planned_stop = False
            build_btn.set_enabled(True)
            build_spinner.set_text("")

    def _on_build_click():
        err = _check_build_prereq()
        if err:
            ui.notify(position="top", type="warning", message=err)
            return
        build_btn.set_enabled(False)
        build_spinner.set_text("构建中…")
        threading.Thread(target=_run_build_index, daemon=True).start()

    def _on_health_check():
        """GET /health 状态探测（完善：全量运行态字段+连接拒绝/HTTP 错误分支区分）。"""
        import requests
        port = get_nested_value(config, "fastrag", "port", default=11420)
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
        except requests.exceptions.ConnectionError:
            health_label.set_text(
                "服务未运行——开启开关并『运行系统』后自动拉起"
            ).style("color: var(--text-hint)")
            return
        except Exception as _e:
            health_label.set_text(
                f"探测异常：{type(_e).__name__}——查 log/fastrag_server.log"
            ).style("color: var(--color-warning, #f59e0b)")
            return
        if resp.status_code != 200:
            health_label.set_text(
                f"服务异常：HTTP {resp.status_code}（503=索引未就绪）——查 log/fastrag_server.log"
            ).style("color: var(--color-danger, #dc2626)")
            return
        data = resp.json() or {}
        health_label.set_text(
            f"运行中 | 文本块:{data.get('chunks', '?')} | rerank:{data.get('rerank_enabled', '?')}"
            f" | LLM:{data.get('llm_quant', '?')} | Rerank量化:{data.get('reranker_quant', '?')}"
            f" | 显存:{data.get('vram_gb', '?')}GB"
        ).style("color: var(--color-positive)")

    def _on_query_test():
        """基础功能节#1：一次性真实 /query 试连（health 只证服务存活，本按钮证
        RAG 契约链路——{"question"}→{"answer"} 全链）。与 R2-2 不轮询裁决不冲突。"""
        import time as _time
        import requests as _rq
        port = get_nested_value(config, "fastrag", "port", default=11420)
        t0 = _time.perf_counter()
        try:
            resp = _rq.post(f"http://127.0.0.1:{port}/query",
                            json={"question": "连接测试"}, timeout=60)
        except _rq.exceptions.ConnectionError:
            ui.notify(position="top", type="negative",
                      message="problem=连接被拒绝；cause=fastrag 服务未启动；fix=开启开关并『运行系统』拉起，或先点检查服务状态")
            return
        except _rq.exceptions.Timeout:
            ui.notify(position="top", type="negative",
                      message="problem=问答超时（60s）；cause=模型推理未返回（首次冷启动加载或服务挂起）；fix=查 log/fastrag_server.log")
            return
        elapsed = _time.perf_counter() - t0
        if resp.status_code != 200:
            ui.notify(position="top", type="negative",
                      message=f"problem=/query 返回 HTTP {resp.status_code}；cause=服务异常或索引未就绪；fix=查 log/fastrag_server.log 后重试")
            return
        answer = (resp.json() or {}).get("answer")
        if answer is None:
            ui.notify(position="top", type="negative",
                      message="problem=响应无 answer 字段；cause=fastrag 版本契约漂移；fix=核对 PATCHES.md 契约锚")
            return
        preview = str(answer)[:60] + ("…" if len(str(answer)) > 60 else "")
        ui.notify(position="top", type="positive",
                  message=f"测试问答连通（{elapsed:.1f}s）：{preview}")

    # （C4 收敛注：原『应用 fastrag 请求模板』/『还原上次配置』按钮已退役——
    #  fastrag /query 为私有 schema（{"question"}→{"answer"}），S2 裁决响应解析
    #  固定 OpenAI schema 后其配置载体 url/body/data_analysis 全部退役，通道不再适用。）

    with ui.card().style(card_css):
        ui.label("fastrag 知识库")
        ui.switch(
            "启用 fastrag 服务（随系统启动）",
            value=bool(get_nested_value(config, "fastrag", "enable", default=False)),
            on_change=lambda e: set_config_callback("fastrag", "enable", value=e.value),
        ).tooltip("服务进程级开关：关闭后不随系统启动 server；"
                  "fastrag /query 为私有 schema，C4 收敛后不再作为 custom_llm 端点"
                  "（custom_llm 固定 OpenAI-compatible 协议）")

        with ui.grid(columns=3):
            FormField.create_input(
                label="服务端口（启动时环境变量注入）",
                value=str(get_nested_value(config, "fastrag", "port", default=11420)),
                on_change=lambda e: set_config_callback("fastrag", "port", value=int(e.value or 11420)),
                tooltip="config.json 为 UI 真源，启动时经 FASTRAG__SERVER__PORT 注入；改后需重启 server",
                style="width:100%;",
            )
            FormField.create_input(
                label="知识库目录",
                value="edtalk/fastrag/knowledgebase/",
                style="width:100%;",
            ).props("disable")


        # ---------- 上传文档 UI（义务块"fastrag 系统级整合"：多文档+文件名清洗 F-E5+五件套） ----------
        upload_docs_label = ui.label("尚未选择文档").style("color: var(--text-hint)")
        doc_status_col = ui.column().style("width:100%;gap:2px")  # 逐文件状态行（D4）

        async def _collect_doc(e):
            """选择文档即校验+落盘（2026-10-29 用户裁决：去掉『落盘所选文档』
            中间按钮——上传→自动落盘→点『构建索引』两步完成）。

            NiceGUI 3.16 事件形态：UploadEventArguments.file=FileUpload
            （.name + await .save(path)）——旧版 e.name/e.content 直挂形态
            已随 3.x 破坏性变更移除（实测空名根因）。"""
            import re as _re
            file = getattr(e, "file", None)
            name = getattr(file, "name", "") if file is not None else ""
            ext = os.path.splitext(name)[1].lower()
            if not name or ext not in (".md", ".txt"):
                ui.notify(position="top", type="negative",
                          message=f"problem=不支持的格式；cause={name or '(事件未携带文件名)'}；fix=仅支持 .md/.txt（其他格式随路线能力扩展）")
                return
            kb = os.path.join(_fastrag_dir(), "knowledgebase")
            os.makedirs(kb, exist_ok=True)
            safe = os.path.basename(name)
            stem = _re.sub(r"[^A-Za-z0-9_一-鿿-]", "_", os.path.splitext(safe)[0]) or "doc"
            target = os.path.join(kb, stem + os.path.splitext(safe)[1].lower())
            n = 1
            while os.path.exists(target):
                target = os.path.join(kb, f"{stem}_{n}{os.path.splitext(safe)[1].lower()}")
                n += 1
            try:
                await file.save(target)
                with doc_status_col:
                    ui.label(f"✓ 已落盘：{os.path.basename(target)}——点『构建索引』生成索引").style(
                        "font-size: var(--font-size-assist, 12px); color: var(--color-positive, #16a34a);")
                upload_docs_label.set_text("已落盘到 edtalk/fastrag/knowledgebase/（点『构建索引』生效）")
                _refresh_kb_list()
            except Exception as ex:
                ui.notify(position="top", type="negative",
                          message=f"problem=文档落盘失败；cause={safe}——{ex}；fix=检查磁盘与目录权限后重试")

        # 2026-10-29 批次：q-uploader 自带拾取按钮过小不易点击——隐藏原生上传器
        # 外壳，改用与页面其它按钮一致的 outline 标准按钮触发文件选择。
        # 实测勘误：Quasar pick() 方法不可用（Method "pick" not found）——
        # 改为直接 click 上传器内部原生 input[type=file]（隐藏态程序化点击
        # 有效，且处于按钮点击的用户激活上下文内）
        _doc_uploader = ui.upload(
            label="上传知识库文档（.md/.txt，可多选）",
            multiple=True, auto_upload=True, on_upload=_collect_doc,
        ).props('accept=".md,.txt"').style("width:100%")
        _doc_uploader.set_visibility(False)
        # 静默失败面收口：文件选择被浏览器/Quasar 拒绝（类型不符 accept 等）
        # 时给出可见提示——不留无声失败（2026-10-29 实测教训二则）
        _doc_uploader.on_rejected(lambda _e: ui.notify(
            position="top", type="negative",
            message="problem=文件被拒绝；cause=浏览器/上传器拒绝该文件；fix=确认文件为 .md/.txt 且可读后重试"))

        def _pick_docs():
            # NiceGUI 元素根节点无 DOM id（getElementById 仅作快速路径）；
            # 按上传器元素 id 选取内部原生 input[type=file]（choose_song 页
            # 上传歌曲为同模式第二处，均按 id 作用域选取不互串）
            ui.run_javascript(
                "(() => { const root = document.getElementById("
                f"'{_doc_uploader.id}');"
                "const input = (root || document)"
                ".querySelector('input.q-uploader__input, input[type=file]');"
                "if (input) { input.value = ''; input.click(); } })()")  # 选前清空：同文件重选也触发 change

        # 按钮并列行（2026-10-29 用户裁决：上传文档+构建索引并列；诊断两键同行）
        with ui.row().style("align-items:center; gap:8px; flex-wrap:wrap"):
            ui.button("上传文档", icon='upload_file',
                      on_click=_pick_docs).props("outline").tooltip(
                "选择 .md/.txt 文档，选择后自动落盘到 knowledgebase/")
            build_btn = ui.button("构建索引", on_click=_on_build_click).props("outline").tooltip(
                "对 knowledgebase/ 全量文档重建向量索引（构建前自动暂停 fastrag 服务）")
            build_spinner = ui.label("").style("color: var(--text-hint)")
            ui.button("检查服务状态", on_click=_on_health_check).props("flat")
            ui.button("测试问答", on_click=_on_query_test).props("flat").tooltip(
                "POST /query 一次真实试连（验证 RAG 契约链路+耗时，非仅服务存活）")
            health_label = ui.label("").style("color: var(--text-hint)")

        # ---------- 已入库文档列表（D4：读 knowledgebase/ 目录+删除按钮） ----------
        # 说明：utils/fastrag 服务端点仅 /health /query /batch_query /clear_cache，无上传 API——
        # 前端直写 knowledgebase/ 目录即入库源（构建索引后生效），无需动 utils/fastrag。
        ui.label("已入库文档（edtalk/fastrag/knowledgebase/）").style(
            "font-size: var(--font-size-body, 14px);")
        ui.label("删除文档后需重新『构建索引』生成索引").style(
            "color: var(--text-hint); font-size: var(--font-size-assist, 12px);")
        kb_list_col = ui.column().style("width:100%;gap:2px;max-height:200px;overflow:auto")

        def _refresh_kb_list():
            kb = os.path.join(_fastrag_dir(), "knowledgebase")
            files = sorted(
                fn for fn in (os.listdir(kb) if os.path.isdir(kb) else [])
                if fn.endswith((".md", ".txt"))
            )
            kb_list_col.clear()
            with kb_list_col:
                if not files:
                    ui.label("（空）——上传文档并落盘后在此列出").style(
                        "color: var(--text-hint); font-size: var(--font-size-assist, 12px);")
                for fn in files:
                    with ui.row().style("width:100%;align-items:center;gap:4px"):
                        ui.label(f"📄 {fn}").style("font-size: var(--font-size-assist, 12px);")

                        def _del_doc(name=fn):
                            try:
                                os.remove(os.path.join(kb, name))
                                ui.notify(position="top", type="positive",
                                          message=f"已删除 {name}（重新构建索引后生效）")
                            except Exception as e:
                                ui.notify(position="top", type="negative",
                                          message=f"删除失败：{e}")
                            _refresh_kb_list()

                        ui.button(icon='delete', on_click=_del_doc).props('flat dense round')

        _refresh_kb_list()

        # ---------- 推理配置（对话模型/量化——2026-10-29 用户裁决显式暴露；改后需重启系统生效） ----------
        with ui.expansion("推理配置（对话模型/量化——改后需重启系统生效）").style("width:100%"):
            ui.label(
                "对话模型与量化项经启动 env 注入服务（config.py 补丁 P7/P8 域校验，非法值拒绝启动），"
                "改后需重启系统生效。显存参考：INT4 约 -1.0GB/项；"
                "1.5B 模型效果更好，但显存占用与推理耗时更高（首次启动冷加载更慢）。"
            ).style("color: var(--text-hint); font-size: var(--font-size-assist, 12px)")
            with ui.grid(columns=3):
                ui.select(
                    options={"Qwen2.5-0.5B-Instruct": "千问2.5-0.5B（默认，性能开销低）",
                             "Qwen2.5-1.5B-Instruct": "千问2.5-1.5B（效果更好，开销更高）"},
                    value=str(get_nested_value(config, "fastrag", "llm_model",
                                               default="Qwen2.5-0.5B-Instruct")),
                    on_change=lambda e: set_config_callback("fastrag", "llm_model", value=e.value),
                    label="对话模型",
                ).props("outlined").style("width:100%;").tooltip(
                    "知识库问答/闲聊/动态文案共用的对话 LLM，模型位于 "
                    "edtalk/fastrag/chekpoint/（相对路径随仓库部署便携）")
                ui.select(
                    options={4: "INT4（省约 1.0GB）", 8: "INT8（省约 0.5GB）", 16: "BF16（默认，无量化）"},
                    value=get_nested_value(config, "fastrag", "llm_quant_bits", default=16),
                    on_change=lambda e: set_config_callback("fastrag", "llm_quant_bits", value=e.value),
                    label="LLM 量化",
                ).props("outlined").style("width:100%;")
                ui.select(
                    options={4: "INT4（省约 0.3GB）", 8: "INT8（省约 0.3GB）", 16: "BF16（默认，无量化）"},
                    value=get_nested_value(config, "fastrag", "reranker_quant_bits", default=16),
                    on_change=lambda e: set_config_callback("fastrag", "reranker_quant_bits", value=e.value),
                    label="Reranker 量化",
                ).props("outlined").style("width:100%;")
                ui.select(
                    options={"cpu": "CPU（省显存 ~1.8GB）", "cuda-fp16": "GPU FP16（推荐生产）", "cuda": "GPU FP32（显存较高）"},
                    value=get_nested_value(config, "fastrag", "embedder_device", default="cuda-fp16"),
                    on_change=lambda e: set_config_callback("fastrag", "embedder_device", value=e.value),
                    label="Embedder 设备",
                ).props("outlined").style("width:100%;")

        # ---------- 请求模板参数页（R9 义务：六参数暴露+落盘 override+应用重启+脏标记 D6） ----------
        with ui.expansion("对话人设与生成参数（fastrag 生成行为——保存后需应用重启）").style("width:100%"):
            # 流式输出独立开关（2026-10-29 用户裁决：fastrag.stream_reply 独立键，
            # 取代此前跨通道复用 custom_llm.stream_reply 的保守方案）
            ui.switch(
                "流式输出（LLM 逐段生成→逐句 TTS，首句提前出声）",
                value=bool(get_nested_value(config, "fastrag", "stream_reply", default=True)),
                on_change=lambda e: set_config_callback("fastrag", "stream_reply", value=e.value),
            ).tooltip("关闭后 fastrag 通道走全文链路（生成完整回答后一次性合成）；"
                      "流式失败零产出时自动回退全文链")
            tpl_dirty = {"v": False}
            _TPL_KEYS = [
                ("system_role", "人设（SYSTEM_ROLE）", "你叫老王，你是一位专业、友好的线上销售，现在正在进行直播工作，在直播间为客户答疑解惑。"),
                # 2026-11-20 上游同步：服务端键 ANSWER_RULES → SYSTEM_RULES；
                # config 存储键统一为 answer_rules（修复：此前 UI 键集误用
                # system_rules 与保存链 vals['answer_rules'] 错位→应用重启时
                # KeyError 模板落盘失败；回显真源 config.fastrag.template.answer_rules）
                ("answer_rules", "回答规则（SYSTEM_RULES，每条一行）", ""),
                ("temperature", "生成温度 temperature（0-1）", 0.3),
                ("top_p", "Top-p（0-1）", 0.9),
                ("max_new_tokens", "最大生成 token 数", 256),
                ("max_answer_length", "答案最大字符数", 80),
            ]
            tpl_inputs = {}

            def _save_tpl_input(key, value):
                """输入即落盘 config.fastrag.template.*（2026-10-30 修复：模板
                输入框此前仅标脏不落盘——用户输入人设/回答规则后刷新页面值丢失
                （『应用并重启』按钮是唯一持久化路径，实测反馈）。_notify=False
                防逐键 toast 轰炸；服务端 config_override.py 仍由『应用并重启』
                统一落盘，脏标记提示语义不变。"""
                set_config_callback("fastrag", "template", key, value=value, _notify=False)

            with ui.grid(columns=2):
                for key, label, default in _TPL_KEYS:
                    _val = str(get_nested_value(config, "fastrag", "template", key, default=default))
                    if key == "answer_rules":
                        # 2026-11-20 整合对齐批次：回答规则为多行块（每条一行）——
                        # 单行 ui.input 会把粘贴的多行规则折叠成一行（规则序号
                        # 语义被打平，用户实测反馈）；textarea 保留换行
                        tpl_inputs[key] = ui.textarea(
                            label=label,
                            value=_val,
                            on_change=lambda e, k=key: (_save_tpl_input(k, e.value), _mark_tpl_dirty()),
                        ).style("width:100%;").props("outlined autosize")
                    else:
                        tpl_inputs[key] = FormField.create_input(
                            label=label,
                            value=_val,
                            on_change=lambda e, k=key: (_save_tpl_input(k, e.value), _mark_tpl_dirty()),
                            style="width:100%;",
                        )
            tpl_dirty_label = ui.label("").style("color: var(--color-warning); display:none")

            def _mark_tpl_dirty():
                tpl_dirty["v"] = True
                tpl_dirty_label.set_text("有未生效修改——点击『应用并重启 fastrag 服务』生效")
                tpl_dirty_label.style("color: var(--color-warning); display:block")

            async def _apply_template_and_restart():
                """模板生效（D6：保存落盘 override+显式重启+脏标记清零；DX F-7 失败回滚）。

                2026-10-30 修复（回答规则重启丢失）：此前仅落盘 config_override.py
                （服务端运行态真源），UI 初始值读 config.fastrag.template.*——
                config.json 无此节 → 重启后 UI 回显默认值（看似丢失）；再次点应用
                会把 override 覆盖回默认=真丢失。修复：落盘 override 的同时把六参
                数写回 config（set_config_callback 支持任意深度路径并自动落盘），
                UI 回显与服务端 override 双源一致。
                2026-11-09 断联审查修复：停服（wmic subprocess timeout=15s，Win11
                首调常 1-3s）+health 探测（timeout=2）原在事件循环上同步执行——
                合计可达 5s 超 ping_timeout=2.0s → 断联重载；阻塞段移出
                （asyncio.to_thread，对齐 audio_play 断联根修模式）。"""
                try:
                    # 落盘 config_override.py（fastrag-python 侧 P3 补丁加载）
                    py_dir = os.path.join(_fastrag_dir(), "fastrag-python")
                    override = os.path.join(py_dir, "config_override.py")
                    backup = override + ".bak"
                    if os.path.exists(override):
                        shutil.copy2(override, backup)
                    lines = []
                    vals = {k: (i.value or "") for k, i in tpl_inputs.items()}
                    lines.append(f"SYSTEM_ROLE = {vals['system_role']!r}")
                    if vals["answer_rules"]:
                        # 2026-11-20 上游同步：服务端键 ANSWER_RULES → SYSTEM_RULES
                        # （config 存储键仍为 template.answer_rules，仅 override 写入键迁移）
                        lines.append(f"SYSTEM_RULES = {vals['answer_rules']!r}")
                    lines.append(f"LLM_TEMPERATURE = {float(vals['temperature'] or 0.3)}")
                    lines.append(f"LLM_TOP_P = {float(vals['top_p'] or 0.9)}")
                    lines.append(f"LLM_MAX_NEW_TOKENS = {int(vals['max_new_tokens'] or 128)}")
                    lines.append(f"MAX_ANSWER_LENGTH = {int(vals['max_answer_length'] or 80)}")
                    with open(override, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines) + "\n")
                    # 同步写回 config（UI 重启后回显真源；_FASTRAG_DEFAULTS.template
                    # 提供缺省骨架）——answer_rules 空串也写入（回显与服务端默认语义一致）
                    set_config_callback("fastrag", "template", "system_role",
                                        value=vals["system_role"], _notify=False)
                    set_config_callback("fastrag", "template", "answer_rules",
                                        value=vals["answer_rules"], _notify=False)
                    set_config_callback("fastrag", "template", "temperature",
                                        value=float(vals["temperature"] or 0.3), _notify=False)
                    set_config_callback("fastrag", "template", "top_p",
                                        value=float(vals["top_p"] or 0.9), _notify=False)
                    set_config_callback("fastrag", "template", "max_new_tokens",
                                        value=int(vals["max_new_tokens"] or 128), _notify=False)
                    set_config_callback("fastrag", "template", "max_answer_length",
                                        value=int(vals["max_answer_length"] or 80), _notify=False)
                except Exception as e:
                    ui.notify(position="top", type="negative", message=f"模板落盘失败：{e}")
                    return
                # 重启服务（2026-11-19 修复：原实现只停不启——server 被杀后仅
                # 提示"随下次运行系统拉起"，系统运行中场景知识库服务直接失联，
                # 监控线程还会误报「外围程序退出」。现经 restart_fastrag 完成停服
                # →等端口释放→重新拉起闭环，拉起链路与『运行系统』完全同源。
                # 阻塞段（wmic 停服+端口等待+Popen）在 worker 线程执行——对齐
                # audio_play 断联根修模式，防事件循环阻塞断联重载）
                from frontend.main import AutoliveApp
                if not bool(get_nested_value(config, "fastrag", "enable", default=False)):
                    # 服务未启用：无进程可重启，模板落盘即生效路径（下次启用随系统拉起）
                    tpl_dirty["v"] = False
                    tpl_dirty_label.style("display:none")
                    ui.notify(position="top", type="positive",
                              message="模板已保存（fastrag 未启用——开关开启后随『运行系统』生效）")
                    return
                _app = AutoliveApp.APP_HOLDER.get("app")
                if _app is None:
                    # 主实例不可达（边缘态）：回退只停语义，由下次『运行系统』拉起
                    await asyncio.to_thread(AutoliveApp.stop_fastrag_server)
                    tpl_dirty["v"] = False
                    tpl_dirty_label.style("display:none")
                    ui.notify(position="top", type="positive",
                              message="模板已生效（服务管理器不可达，fastrag 将随下次『运行系统』拉起）")
                    return
                ok, reason, fix_hint = await asyncio.to_thread(_app.restart_fastrag)
                if not ok:
                    # 回滚 override（DX F-7：失败场景保持文件与运行态一致）
                    if os.path.exists(backup):
                        shutil.copy2(backup, override)
                    ui.notify(position="top", type="negative", timeout=10,
                              message=f"fastrag 服务重启失败：{reason}" +
                                      (f"｜{fix_hint}" if fix_hint else "（模板已回滚）"))
                    return
                tpl_dirty["v"] = False
                tpl_dirty_label.style("display:none")
                ui.notify(position="top", type="positive",
                          message="模板已生效，fastrag 服务已重启")

            ui.button("应用并重启 fastrag 服务", on_click=_apply_template_and_restart).props("outline")

        # ---------- 区分文案（native F8；C4 收敛后仅余生成参数一侧） ----------
        ui.label("本页『对话人设与生成参数』= fastrag 生成行为（人设/回答规则/长度/温度），"
                 "由系统全面接管：保存经 config_override.py 覆盖服务端内置值，随服务重启生效"
                 "（SYSTEM_RULES 为显式配置，不同功能模块可各设不同规则）；"
                 "LLM_DO_SAMPLE=True 与 enable_thinking=False 为系统固定默认，不提供配置；"
                 "LLM 请求通道（端点/格式）在上方『自定义LLM』卡片配置（OpenAI-compatible 固定协议）。"
                 "两侧分工勿混淆，改错一侧会出现『模板不生效』。").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        ui.label(
            "知识库格式：Markdown 按标题分块（H1=文档名，H2/H3=分块边界）；"
            "人设与生成参数在上方『对话人设与生成参数』折叠区配置（python 路线经 config_override.py 生效）"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")
