from frontend.ui.components.config_helper import get_nested_value
"""
大语言模型标签页模块
从 webui-bak.py 的 llm_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
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

# fastrag 文档上传暂存（auto_upload=False：逐文件收集，落盘按钮统一写入 knowledgebase）


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

        # {{prompt}} 变量说明（C4：请求模板退役，弹幕内容自动注入 user 消息）
        ui.label(
            "请求固定为 OpenAI-compatible 协议：POST {base_url}/chat/completions；"
            "弹幕内容自动作为 user 消息发送（等价旧版请求体模板中的 {{prompt}} 变量，无需再手写请求体）；"
            "响应固定解析 choices[0].message.content。"
            "各平台差异仅在 base_url / model / api_key 三项，示例见 docs/llm-request-template.md。"
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

    def _stop_fastrag_server_if_running():
        """构建前先停 server（python 路线：Windows 下 server 持有索引句柄会
        PermissionError）。wmic 精确按命令行特征杀 python server.py（避免误伤
        其他 python）；wmic 缺失（Win11 24H2+ 按需功能，DX F7）时回退
        PowerShell Get-CimInstance，均失败输出可行动日志。"""
        killed = False
        try:
            out = subprocess.run(
                ["wmic", "process", "where",
                 "name='python.exe' and commandline like '%fastrag-python%server.py%'",
                 "get", "processid"],
                capture_output=True, text=True, timeout=15,
            )
            for line in (out.stdout or "").splitlines():
                pid = line.strip()
                if pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid, "/T"],
                                   capture_output=True, timeout=10)
                    killed = True
        except FileNotFoundError:
            logger.warning("wmic 不可用（Win11 24H2+ 按需功能），回退 PowerShell 探测 fastrag server 进程")
        except Exception as e:
            logger.warning(f"wmic 探测 fastrag server 失败（可能未运行）：{e}")
        if not killed:
            # DX F7 备选链：PowerShell Get-CimInstance 按命令行特征精确杀
            try:
                _ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                       "Where-Object { $_.CommandLine -like '*fastrag-python*server.py*' } | "
                       "Select-Object -ExpandProperty ProcessId")
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", _ps],
                    capture_output=True, text=True, timeout=20,
                )
                for line in (out.stdout or "").splitlines():
                    pid = line.strip()
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", pid, "/T"],
                                       capture_output=True, timeout=10)
                        killed = True
            except Exception as e:
                logger.warning(
                    f"停止 fastrag server 失败（可能未运行）：{e}；"
                    f"fix=手动结束 python server.py 进程后重试构建，否则索引文件可能被占用")

    def _run_build_index():
        """后台线程：构建知识库索引（『构建索引』按钮）。"""
        try:
            import subprocess
            _stop_fastrag_server_if_running()
            # 解释器解析与启动路径同源（Eng A1：resolve_interpreter 四级链，
            # 消除硬编码默认 llm.py:159 旧分叉——解释器挪位后构建/启动不再分裂）
            from utils.edtalk_realtime.runtime import resolve_interpreter
            interpreter = resolve_interpreter(config)
            if not interpreter or not os.path.exists(interpreter):
                ui.notify(position="top", type="negative",
                          message="problem=解释器未找到；cause=runtime312 缺失；fix=系统设置页配置 edtalk_realtime 解释器路径")
                return
            proc = subprocess.Popen(
                [interpreter, os.path.join(_fastrag_dir(), "fastrag-python", "build_index_native.py")],
                cwd=os.path.join(_fastrag_dir(), "fastrag-python"), shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                logger.info(f"[build-index] {line.rstrip()}")
            code = proc.wait()
            if code == 0:
                ui.notify(position="top", type="positive",
                          message="索引构建完成。重启系统后新索引生效（启动物=python 路线；索引缺失时启动预检会指引先构建再重启）")
            else:
                ui.notify(position="top", type="negative", message=f"构建失败（exit {code}），详见日志")
        except Exception as e:
            logger.error(f"构建索引异常：{e}")
            ui.notify(position="top", type="negative", message=f"构建异常：{e}")
        finally:
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
        """GET /health 状态探测（离线态灰字引导，design 义务）。"""
        import requests
        port = get_nested_value(config, "fastrag", "port", default=11420)
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            data = resp.json() if resp.status_code == 200 else {}
            health_label.set_text(
                f"运行中 | 文本块:{data.get('chunks', '?')} | rerank:{data.get('rerank_enabled', '?')}"
            ).style("color: var(--color-positive)")
        except Exception:
            health_label.set_text(
                "服务未运行——开启开关并『运行系统』后自动拉起"
            ).style("color: var(--text-hint)")

    def _on_query_test():
        """基础功能节#1：一次性真实 /query 试连（health 只证服务存活，本按钮证
        RAG 契约链路——{"question"}→{"answer"} 全链）。与 R2-2 不轮询裁决不冲突。"""
        import requests as _rq
        port = get_nested_value(config, "fastrag", "port", default=11420)
        try:
            resp = _rq.post(f"http://127.0.0.1:{port}/query",
                            json={"question": "连接测试"}, timeout=15)
            if resp.status_code != 200:
                ui.notify(position="top", type="negative",
                          message=f"problem=/query 返回 HTTP {resp.status_code}；cause=服务异常或索引未就绪；fix=查 server 日志后重试")
                return
            answer = (resp.json() or {}).get("answer")
            if answer is None:
                ui.notify(position="top", type="negative",
                          message="problem=响应无 answer 字段；cause=fastrag 版本契约漂移；fix=核对 edtalk/fastrag/api_client.py 契约")
                return
            preview = str(answer)[:60] + ("…" if len(str(answer)) > 60 else "")
            ui.notify(position="top", type="positive",
                      message=f"测试问答连通：{preview}")
        except Exception as e:
            ui.notify(position="top", type="negative",
                      message=f"problem=连接失败；cause={type(e).__name__}；fix=确认服务已启动（先点检查服务状态）")

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
            health_label = ui.label("").style("color: var(--text-hint)")

        with ui.row():
            build_btn = ui.button("构建索引", on_click=_on_build_click).props("outline")
            build_spinner = ui.label("").style("color: var(--text-hint)")
            ui.button("检查服务状态", on_click=_on_health_check).props("flat")
            ui.button("测试问答", on_click=_on_query_test).props("flat").tooltip(
                "POST /query 一次真实试连（验证 RAG 契约链路，非仅服务存活）")

        # ---------- 上传文档 UI（义务块"fastrag 系统级整合"：多文档+文件名清洗 F-E5+五件套） ----------
        upload_docs_label = ui.label("尚未选择文档").style("color: var(--text-hint)")
        doc_status_col = ui.column().style("width:100%;gap:2px")  # 逐文件状态行（D4）

        def _collect_doc(e):
            """选择文档即校验+落盘（2026-10-29 用户裁决：去掉『落盘所选文档』
            中间按钮——上传→自动落盘→点『构建索引』两步完成）。"""
            import re as _re
            name = getattr(e, "name", "")
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".md", ".txt"):
                ui.notify(position="top", type="negative",
                          message=f"problem=不支持的格式；cause={name}；fix=仅支持 .md/.txt（其他格式随路线能力扩展）")
                return
            kb = os.path.join(_fastrag_dir(), "knowledgebase")
            os.makedirs(kb, exist_ok=True)
            safe = os.path.basename(getattr(e, "name", "doc.md"))
            stem = _re.sub(r"[^A-Za-z0-9_一-鿿-]", "_", os.path.splitext(safe)[0]) or "doc"
            target = os.path.join(kb, stem + os.path.splitext(safe)[1].lower())
            n = 1
            while os.path.exists(target):
                target = os.path.join(kb, f"{stem}_{n}{os.path.splitext(safe)[1].lower()}")
                n += 1
            try:
                with open(target, "wb") as out:
                    out.write(e.content.read())
                doc_status_col.clear()
                with doc_status_col:
                    ui.label(f"✓ 已落盘：{os.path.basename(target)}——点『构建索引』生成索引").style(
                        "font-size: var(--font-size-assist, 12px); color: var(--color-positive, #16a34a);")
                upload_docs_label.set_text(f"已落盘到 edtalk/fastrag/knowledgebase/（点『构建索引』生效）")
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
            multiple=True, auto_upload=False, on_upload=_collect_doc,
        ).props('accept=".md,.txt"').style("width:100%")
        _doc_uploader.set_visibility(False)

        def _pick_docs():
            # NiceGUI 元素根节点无 DOM id（getElementById 仅作快速路径）；
            # 本应用唯一 ui.upload——按 q-uploader 原生 input 类名全局选取
            ui.run_javascript(
                "(() => { const root = document.getElementById("
                f"'{_doc_uploader.id}');"
                "const input = (root || document)"
                ".querySelector('input.q-uploader__input, input[type=file]');"
                "if (input) input.click(); })()")

        ui.button("上传知识库文档（.md/.txt，可多选）", icon='upload_file',
                  on_click=_pick_docs).props("outline")

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
                ("system_role", "人设（SYSTEM_ROLE）", "你是一位专业、友好的线上销售，现在正在进行直播。"),
                ("answer_rules", "回答规则（ANSWER_RULES）", ""),
                ("temperature", "生成温度 temperature（0-1）", 0.3),
                ("top_p", "Top-p（0-1）", 0.9),
                ("max_new_tokens", "最大生成 token 数", 128),
                ("max_answer_length", "答案最大字符数", 80),
            ]
            tpl_inputs = {}
            with ui.grid(columns=2):
                for key, label, default in _TPL_KEYS:
                    tpl_inputs[key] = FormField.create_input(
                        label=label,
                        value=str(get_nested_value(config, "fastrag", "template", key, default=default)),
                        on_change=lambda e: _mark_tpl_dirty(),
                        style="width:100%;",
                    )
            tpl_dirty_label = ui.label("").style("color: var(--color-warning); display:none")

            def _mark_tpl_dirty():
                tpl_dirty["v"] = True
                tpl_dirty_label.set_text("有未生效修改——点击『应用并重启 fastrag 服务』生效")
                tpl_dirty_label.style("color: var(--color-warning); display:block")

            def _apply_template_and_restart():
                """模板生效（D6：保存落盘 override+显式重启+脏标记清零；DX F-7 失败回滚）。"""
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
                        lines.append(f"ANSWER_RULES = {vals['answer_rules']!r}")
                    lines.append(f"LLM_TEMPERATURE = {float(vals['temperature'] or 0.3)}")
                    lines.append(f"LLM_TOP_P = {float(vals['top_p'] or 0.9)}")
                    lines.append(f"LLM_MAX_NEW_TOKENS = {int(vals['max_new_tokens'] or 128)}")
                    lines.append(f"MAX_ANSWER_LENGTH = {int(vals['max_answer_length'] or 80)}")
                    with open(override, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines) + "\n")
                except Exception as e:
                    ui.notify(position="top", type="negative", message=f"模板落盘失败：{e}")
                    return
                # 重启服务（python 路线：杀 server.py+由系统重启或手动拉起）
                _stop_fastrag_server_if_running()
                import requests
                try:
                    requests.get(f"http://127.0.0.1:{get_nested_value(config, 'fastrag', 'port', default=11420)}/health", timeout=2)
                    # 仍在运行（其他实例）——回滚 override（DX F-7）
                    if os.path.exists(backup):
                        shutil.copy2(backup, override)
                    ui.notify(position="top", type="negative",
                              message="problem=服务重启失败；cause=旧服务仍在运行；fix=停止系统后重新运行系统（模板已回滚）")
                    return
                except Exception:
                    pass  # 已停止=正常
                tpl_dirty["v"] = False
                tpl_dirty_label.style("display:none")
                ui.notify(position="top", type="positive",
                          message="模板已生效（fastrag 服务将随下次『运行系统』拉起；直播中重启请错峰）")

            ui.button("应用并重启 fastrag 服务", on_click=_apply_template_and_restart).props("outline")

        # ---------- 区分文案（native F8；C4 收敛后仅余生成参数一侧） ----------
        ui.label("本页『对话人设与生成参数』= fastrag 生成行为（语气/长度）——保存后需应用重启；"
                 "LLM 请求通道（端点/格式）在上方『自定义LLM』卡片配置（OpenAI-compatible 固定协议）。"
                 "两侧分工勿混淆，改错一侧会出现『模板不生效』。").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        ui.label(
            "知识库格式：Markdown 按标题分块（H1=文档名，H2/H3=分块边界）；"
            "人设与生成参数在上方『对话人设与生成参数』折叠区配置（python 路线经 config_override.py 生效）"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")
