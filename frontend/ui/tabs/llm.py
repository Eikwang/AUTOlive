from frontend.ui.components.config_helper import get_nested_value
"""
大语言模型标签页模块
从 webui-bak.py 的 llm_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
import json
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

    # 自定义LLM配置
    with ui.card().style(card_css):
        ui.label("自定义LLM")

        with ui.grid(columns=3):
            # API URL
            FormField.create_textarea(
                label="API URL",
                value=get_nested_value(config, "custom_llm", "url"),
                placeholder='发送HTTP请求的API链接',
                on_change=lambda e: set_config_callback("custom_llm", "url", e.value),
                tooltip='发送HTTP请求的API链接',
                style="width:100%;"
            )

            # API类型
            FormField.create_select(
                label="API类型",
                options={"GET": "GET", "POST": "POST"},
                value=get_nested_value(config, "custom_llm", "method"),
                on_change=lambda e: set_config_callback("custom_llm", "method", e.value),
                tooltip='API类型',
                style="width:100%;"
            )

            # 请求头
            FormField.create_textarea(
                label="请求头",
                value=get_nested_value(config, "custom_llm", "headers"),
                placeholder='换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk',
                on_change=lambda e: set_config_callback("custom_llm", "headers", e.value),
                tooltip='换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk',
                style="width:100%;"
            )

            # 代理
            FormField.create_textarea(
                label="代理",
                value=get_nested_value(config, "custom_llm", "proxies"),
                placeholder='requests库代理配置方法，json数据用"双引号',
                on_change=lambda e: set_config_callback("custom_llm", "proxies", e.value),
                tooltip='requests库代理配置方法，json数据用"双引号',
                style="width:100%;"
            )

        with ui.grid(columns=3):
            # 请求体类型
            FormField.create_select(
                label="请求体类型",
                options={"json": "json", "raw": "raw"},
                value=get_nested_value(config, "custom_llm", "body_type"),
                on_change=lambda e: set_config_callback("custom_llm", "body_type", e.value),
                tooltip='请求体类型',
                style="width:100%;"
            )

            # 请求体
            FormField.create_textarea(
                label="请求体",
                value=get_nested_value(config, "custom_llm", "body"),
                placeholder='请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号',
                on_change=lambda e: set_config_callback("custom_llm", "body", e.value),
                tooltip='请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号',
                style="width:100%;"
            )

            # 请求返回数据类型
            FormField.create_select(
                label="请求返回数据类型",
                options={"json": "json", "content": "content"},
                value=get_nested_value(config, "custom_llm", "resp_data_type"),
                on_change=lambda e: set_config_callback("custom_llm", "resp_data_type", e.value),
                tooltip='请求返回数据类型',
                style="width:100%;"
            )

            # 数据解析
            FormField.create_textarea(
                label="数据解析（eval执行）",
                value=get_nested_value(config, "custom_llm", "data_analysis"),
                placeholder='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析',
                on_change=lambda e: set_config_callback("custom_llm", "data_analysis", e.value),
                tooltip='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析',
                style="width:100%;"
            )

            # 返回内容模板
            FormField.create_textarea(
                label="返回内容模板",
                value=get_nested_value(config, "custom_llm", "resp_template"),
                placeholder='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成',
                on_change=lambda e: set_config_callback("custom_llm", "resp_template", e.value),
                tooltip='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成',
                style="width:100%;"
            )

    # ================= fastrag 知识库卡片（EDTalk功能集成计划 §6.1） =================
    _create_fastrag_card(config, theme_config, set_config_callback)


def _create_fastrag_card(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """fastrag 知识库 RAG 卡片。

    服务进程级开关（不进 ENABLE_PATHS）；server 由开关随系统启动；
    build-index 由构建知识库按钮手动触发。端口真源 = config.fastrag.port，
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
        """构建前先停 server（native H2：Windows 下 server 持有索引句柄会
        PermissionError）。在 webui 进程内无法直接拿到 my_subprocesses 的
        场景下用 taskkill 按映像名兜底。"""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "server.exe"], capture_output=True, timeout=10
            )
        except Exception as e:
            logger.warning(f"停止 fastrag server 失败（可能未运行）：{e}")

    def _run_build_index():
        """后台线程：构建知识库索引。"""
        try:
            import subprocess
            _stop_fastrag_server_if_running()
            proc = subprocess.Popen(
                [os.path.join(_fastrag_dir(), "build-index.exe")],
                cwd=_fastrag_dir(), shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                logger.info(f"[build-index] {line.rstrip()}")
            code = proc.wait()
            if code == 0:
                ui.notify(position="top", type="positive",
                          message="知识库构建完成。点击『重启 fastrag 服务』生效（需系统处于运行状态或下次运行系统时自动拉起）")
            else:
                ui.notify(position="top", type="negative", message=f"构建失败（exit {code}），详见日志")
        except Exception as e:
            logger.error(f"构建知识库异常：{e}")
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
            ).style("color: var(--color-positive, #21ba45)")
        except Exception:
            health_label.set_text(
                "服务未运行——开启开关并『运行系统』后自动拉起"
            ).style("color: var(--text-hint)")

    def _on_apply_template():
        """应用 fastrag 请求模板到 custom_llm（native F6/F8/F10）：
        ①实测 /query 校验 answer 键；②确认框展示将被覆盖的配置；
        ③原配置落盘备份；④应用后提供还原按钮；⑤端口引用配置变量。"""
        import requests
        port = get_nested_value(config, "fastrag", "port", default=11420)

        # ①实测 /query 校验响应字段
        try:
            resp = requests.post(
                f"http://127.0.0.1:{port}/query", json={"question": "测试"},
                timeout=30,
            )
            data = resp.json() if resp.status_code == 200 else {}
            if "answer" not in data:
                ui.notify(position="top", type="warning",
                          message=f"/query 响应无 answer 字段，模板不适用（实际响应键：{list(data.keys())}）")
                return
        except Exception as e:
            ui.notify(position="top", type="negative",
                      message=f"fastrag 服务未连接（{e}）——请先启动服务再应用模板")
            return

        # ②确认框展示将被覆盖的配置 + ③备份
        old_cfg = {
            "url": get_nested_value(config, "custom_llm", "url", default=""),
            "body": get_nested_value(config, "custom_llm", "body", default=""),
            "data_analysis": get_nested_value(config, "custom_llm", "data_analysis", default=""),
        }
        with ui.dialog() as dialog, ui.card():
            ui.label("应用 fastrag 请求模板？").style("font-weight: var(--font-weight-title)")
            ui.label(
                f"将覆盖 custom_llm 当前配置：\n"
                f"  url: {old_cfg['url']}\n"
                f"  body: {old_cfg['body'][:60]}\n"
                f"  data_analysis: {old_cfg['data_analysis']}\n\n"
                f"原配置将备份到 config.custom_llm.bak.json（可一键还原）。"
            ).style("white-space: pre-wrap")
            with ui.row():
                def _do_apply():
                    dialog.close()
                    # ③落盘备份
                    try:
                        base = str(get_base_path())
                        shutil.copy2(
                            os.path.join(base, "config.json"),
                            os.path.join(base, "config.custom_llm.bak.json"),
                        )
                    except Exception as e:
                        logger.warning(f"备份 custom_llm 失败：{e}")
                    # ④应用模板（端口引用配置变量，永不硬编码——native F8）
                    set_config_callback("custom_llm", "url",
                                        value=f"http://127.0.0.1:{port}/query")
                    set_config_callback("custom_llm", "body",
                                        value='{"question":"{{prompt}}"}')
                    set_config_callback("custom_llm", "data_analysis",
                                        value='resp["answer"]')
                    restore_btn.set_enabled(True)
                    ui.notify(position="top", type="positive",
                              message="fastrag 模板已应用（chat_type 保持 custom_llm）")
                ui.button("应用", on_click=_do_apply)
                ui.button("取消", on_click=dialog.close).props("flat")
        dialog.open()

    def _on_restore():
        """从 config.custom_llm.bak.json 还原 custom_llm 配置（native F10）。"""
        try:
            base = str(get_base_path())
            bak = json.load(open(os.path.join(base, "config.custom_llm.bak.json"), encoding="utf-8"))
            old = bak.get("custom_llm", {})
            for key in ("url", "body", "data_analysis", "resp_data_type", "resp_template", "headers", "method"):
                if key in old:
                    set_config_callback("custom_llm", key, value=old[key])
            ui.notify(position="top", type="positive", message="已还原上次 custom_llm 配置")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"还原失败：{e}")

    with ui.card().style(card_css):
        ui.label("fastrag 知识库")
        ui.switch(
            "启用 fastrag 服务（随系统启动）",
            value=bool(get_nested_value(config, "fastrag", "enable", default=False)),
            on_change=lambda e: set_config_callback("fastrag", "enable", value=e.value),
        ).tooltip("服务进程级开关：关闭后不随系统启动 server.exe；"
                  "LLM 侧仍需 chat_type=custom_llm 且 url 指向 /query 才会实际调用")

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
            build_btn = ui.button("构建知识库", on_click=_on_build_click).props("outline")
            build_spinner = ui.label("").style("color: var(--text-hint)")
            ui.button("检查服务状态", on_click=_on_health_check).props("outline")
            ui.button("应用 fastrag 请求模板", on_click=_on_apply_template).props("outline")
            restore_btn = ui.button("还原上次配置", on_click=_on_restore).props("outline flat")
            restore_btn.set_enabled(os.path.exists(
                os.path.join(str(get_base_path()), "config.custom_llm.bak.json")
            ))

        ui.label(
            "知识库格式：Markdown 按标题分块（H1=文档名，H2/H3=分块边界）；"
            "LLM 人设在 edtalk/fastrag/Config.toml 的 prompt.system_role 配置"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")
