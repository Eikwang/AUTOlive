from frontend.ui.components.config_helper import get_nested_value
"""
文本转语音标签页模块
从 webui-bak.py 的 tts_page 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable, Optional
import logging
import os

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check
from frontend.utils.audio import Audio
from utils.audio_handle.tts_params import (
    EDGE_TTS_VOICES, EDGE_TTS_DEFAULT_VOICE, EDGE_TTS_DEFAULT_RATE,
    filter_edge_voices, normalize_ref_paths, normalize_prompt_texts)

logger = logging.getLogger(__name__)


def create_tts_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    audio: Optional[Audio] = None
):
    """
    创建文本转语音标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
        audio: 音频处理实例
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = get_nested_value(config, "button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 音频合成类型选项（D-19：与通用配置"语音合成"下拉共用同一 config 键
    # audio_synthesis_type；任务书：GPT_SoVITS / edge-tts 两选项）
    audio_synthesis_type_options = {
        'gpt_sovits': 'GPT-SoVITS',
        'edge_tts': 'edge-tts',
    }
    
    # 通用-合成试听音频
    async def tts_common_audio_synthesis():
        ui.notify(position="top", type="warning", message="音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        logger.warning("音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        
        content = input_tts_common_text.value
        audio_synthesis_type = select_tts_common_audio_synthesis_type.value

        # 使用本地配置进行音频合成，返回音频路径
        # webui 轻量进程（type=2 Audio）无合成能力——经 main 进程 /tts/synthesize 跨进程调用
        file_path = None
        if audio is not None and getattr(audio, "_synthesis_ready", False):
            file_path = await audio.audio_synthesis_use_local_config(content, audio_synthesis_type)
        else:
            import aiohttp
            base = "http://127.0.0.1:8082"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{base}/tts/synthesize",
                                            json={"content": content, "audio_synthesis_type": audio_synthesis_type},
                                            timeout=aiohttp.ClientTimeout(total=180)) as resp:
                        body = await resp.json()
                if body.get("code") == 200:
                    file_path = body.get("file_path")
                else:
                    logger.error(f"跨进程合成失败：{body.get('message')}")
                    ui.notify(position="top", type="negative", message=f"合成失败：{body.get('message')}")
                    return
            except Exception as e:
                logger.error(f"跨进程合成请求失败（main 未启动或不可达）：{e}")
                ui.notify(position="top", type="negative", message=f"合成请求失败：main 未启动或不可达——请先在首页点启动系统")
                return

        if file_path:
            logger.info(f"音频合成成功，存储于：{file_path}")
            ui.notify(position="top", type="positive", message=f"音频合成成功，存储于：{file_path}")
        else:
            logger.error(f"音频合成失败！请查看日志排查问题")
            ui.notify(position="top", type="negative", message=f"音频合成失败！请查看日志排查问题")
            return

        def clear_tts_common_audio_card(file_path):
            tts_common_audio_card.clear()
            import os
            if os.path.exists(file_path):
                os.remove(file_path)
                ui.notify(position="top", type="positive", message=f"删除文件成功：{file_path}")
            else:
                ui.notify(position="top", type="negative", message=f"删除文件失败：{file_path}")
        
        # 清空card
        tts_common_audio_card.clear()
        tmp_label = ui.label(f"音频合成成功，存储于：{file_path}")
        tmp_label.move(tts_common_audio_card)
        audio_tmp = ui.audio(src=file_path)
        audio_tmp.move(tts_common_audio_card)
        button_audio_del = ui.button('删除音频', on_click=lambda: clear_tts_common_audio_card(file_path), color=button_internal_color).style(button_internal_css)
        button_audio_del.move(tts_common_audio_card)
    
    # 合成测试卡片
    with ui.card().style(card_css):
        ui.label("合成测试（只是测试，若确认使用此TTS，请前往 通用配置 配置 语音合成）")
        with ui.grid(columns=3):
            select_tts_common_audio_synthesis_type = ui.select(
                label='语音合成',
                options=audio_synthesis_type_options,
                value=get_nested_value(config, "audio_synthesis_type")
            ).style("width:100%;")
            input_tts_common_text = ui.input(label='待合成音频内容', placeholder='此处填写待合成的音频文本内容', value="此处填写待合成的音频文本内容，用于试听效果，类型切换不需要保存即可生效。").style("width:100%;")
            button_tts_common_audio_synthesis = ui.button('试听', on_click=lambda: tts_common_audio_synthesis(), color=button_internal_color).style(button_internal_css)
        tts_common_audio_card = ui.card()
        with tts_common_audio_card.style(card_css):
            with ui.grid(columns=3):
                ui.label("此处显示生成的音频，仅显示最新合成的音频，可以在此操作删除合成的音频")

    # GPT-SoVITS配置
    # GPT-SoVITS 语音引擎（F-2 重做 → 2026-10-30 审查批次再简化：进程内引擎为唯一
    # 本地合成通道——「合成引擎（backend）」「外部 API 地址」配置项退役移除；
    # 「合成语种」退役——text_lang 由通用配置「回复语言」need_lang 映射决定
    # （synthesis_manager.tts_handle，{none→auto, zh→zh, en→en, jp→ja, ko→ko,
    # yue→auto_yue}）；新增「流式输出」开关 + 多音字/念法纠正即时配置）
    with ui.card().style(card_css):
        ui.label("语音引擎（GPT-SoVITS 进程内）")
        _auto_row = ui.switch(
            '流式输出',
            value=bool((get_nested_value(config, "gpt_sovits", "stream_reply") or {}).get("enable", False)
                       if isinstance(get_nested_value(config, "gpt_sovits", "stream_reply"), dict)
                       else get_nested_value(config, "gpt_sovits", "stream_reply", False)),
            on_change=lambda e: set_config_callback(
                "gpt_sovits", "stream_reply", value={"enable": bool(e.value)}),
        ).style(switch_internal_css).tooltip(
            '开启后合成按切分片段逐段产出并即时入队播放（首段不等全文合成完），'
            '显著降低首响延迟；数字人（EDTalk）逐段推送，单飞行句背压保序。')

        # 声音档案选择器（P2-4/D7：身份参数唯一入口——D-4 裁决双路径框退役）
        _render_voice_profile_selector(config, card_css)

        # 进程内参数面板（切片 3 参数精简：任务书"只保留参考音频/参考语种/合成语种/
        # 切分方式四项，其余使用系统默认"——top_k/top_p/temperature/batch_size/
        # speed_factor/fragment_interval/repetition_penalty/seed 等 UI 项移除，
        # 合成时引擎取系统默认值（my_tts 透传白名单同步精简）；
        # P25：多参考音频（ref_audio_paths）+ 无参考文本模式（ref_free 默认开）；
        # 2026-10-29 批次：关闭 ref_free 后恢复参考文本输入项，并与多参考音频
        # 逐行对应（prompt_texts[i] ↔ ref_audio_paths[i]，主参考=第一行参与合成）
        with ui.expansion("合成参数（进程内引擎）").style("width:100%"):
            _ref_free_val = bool(get_nested_value(config, "gpt_sovits", "params", "ref_free", default=True))
            _prompt_texts_cfg = normalize_prompt_texts(
                get_nested_value(config, "gpt_sovits", "params", "prompt_texts"))
            if not _prompt_texts_cfg:
                _legacy_pt = get_nested_value(config, "gpt_sovits", "params", "prompt_text")
                _prompt_texts_cfg = [_legacy_pt] if _legacy_pt else []

            def _on_ref_free_toggle(e):
                # 开关联动：关闭无参考文本模式 → 显示参考文本输入（2026-10-29）
                _prompt_area.set_visibility(not e.value)
                set_config_callback("gpt_sovits", "params", "ref_free", value=e.value)

            with ui.grid(columns=2):
                _ref_paths_cfg = normalize_ref_paths(
                    get_nested_value(config, "gpt_sovits", "params", "ref_audio_paths"))
                if not _ref_paths_cfg:
                    _legacy_single = get_nested_value(config, "gpt_sovits", "params", "ref_audio_path")
                    _ref_paths_cfg = [_legacy_single] if _legacy_single else []
                ui.textarea(
                    label='参考音频（多参考，每行一个路径）',
                    value="\n".join(_ref_paths_cfg),
                    placeholder='每行一个参考音频绝对路径；第一行为主参考，其余为辅助参考（实测 3 个以上音色质量最佳）',
                    on_change=lambda e: set_config_callback(
                        "gpt_sovits", "params", "ref_audio_paths",
                        value=normalize_ref_paths(e.value)),
                ).style("width:100%;")
                ui.switch(
                    '无参考文本模式（ref_free）',
                    value=_ref_free_val,
                    on_change=_on_ref_free_toggle,
                ).style(switch_internal_css)
            # 参考文本（2026-10-29 恢复）：逐行对应参考音频；ref_free 开启时隐藏
            _prompt_area = ui.textarea(
                label='参考文本（与参考音频逐行对应）',
                value="\n".join(_prompt_texts_cfg),
                placeholder='第 i 行文本 = 第 i 行参考音频的逐字转写（含标点）；第一行为主参考文本参与合成，辅助参考行存档匹配',
                on_change=lambda e: set_config_callback(
                    "gpt_sovits", "params", "prompt_texts",
                    value=normalize_prompt_texts(e.value)),
            ).style("width:100%;")
            _prompt_area.set_visibility(not _ref_free_val)
            with ui.grid(columns=2):
                FormField.create_select(label='参考语种', options={'zh':'中文', 'ja':'日文', 'en':'英文', 'ko':'韩文', 'yue':'粤语', 'auto':'自动'},
                    value=get_nested_value(config, "gpt_sovits", "params", "prompt_lang") or "zh",
                    on_change=lambda e: set_config_callback("gpt_sovits", "params", "prompt_lang", value=e.value), style="width:100%;")
                FormField.create_select(label='切分方式', options={
                        'cut0':'不切', 'cut1':'凑四句一切', 'cut2':'凑50字一切',
                        'cut3':'按中文句号。切', 'cut4':'按英文句号.切', 'cut5':'按标点符号切'
                    },
                    value=get_nested_value(config, "gpt_sovits", "params", "text_split_method") or "cut5",
                    on_change=lambda e: set_config_callback("gpt_sovits", "params", "text_split_method", value=e.value), style="width:100%;")

            # 念法纠正 / 多音字（2026-10-30 用户裁决：多音字统一走 GPT-SoVITS 引擎
            # 词典——本 textarea 直读直写仓库根 多音字.txt（g2p 层改发音不改正文，
            # 发音精确）；UI 拼写改写（params.polyphone_map）退役。念法纠正=整词
            # 改写仍存 config（引擎词典做不了字面改写），即时保存。
            #
            # 保存可靠性（刷新丢失修复，同念法纠正）：失焦（blur）保存 + 显式按钮
            # 兜底 + 解析 0 命中负反馈——on_change 每键 WebSocket 竞态退役。
            def _pairs_to_text(raw):
                from utils.audio_handle.tts_params import parse_fix_pairs
                return "\n".join(f"{k}={v}" for k, v in parse_fix_pairs(raw))

            _repo_root = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
            _polyphone_path = os.path.join(_repo_root, "多音字.txt")

            def _read_polyphone_file():
                try:
                    with open(_polyphone_path, encoding="utf-8") as f:
                        return f.read()
                except FileNotFoundError:
                    return ""

            def _save_pairs(key, box, label=""):
                from utils.audio_handle.tts_params import parse_fix_pairs
                mapping = {}
                for k, v in parse_fix_pairs(box.value):
                    mapping[k] = v
                if not mapping and (box.value or "").strip():
                    first_bad = next(
                        (ln.strip() for ln in (box.value or "").splitlines()
                         if ln.strip()), "")
                    ui.notify(position="top", type="warning", timeout=8,
                              message=f"{label}：未识别到有效条目——每行格式「原文=读法」"
                                      f"（支持 =/冒号/箭头/空格 分隔）。问题行：{first_bad[:30]}")
                    return False
                set_config_callback("gpt_sovits", "params", key, value=mapping)
                return True

            def _save_polyphone_file(box):
                """多音字.txt 原子写（引擎原生格式逐行校验；坏行警告不落盘）。"""
                from utils.audio_handle.tts_params import parse_polyphone_lines
                entries, bad = parse_polyphone_lines(box.value or "")
                if bad:
                    ui.notify(position="top", type="warning", timeout=8,
                              message=f"多音字：{len(bad)} 行格式无效未保存——"
                                      f"每行格式「词语 [\"拼音1\",\"拼音2\"]」，如：一行 [\"yi4\",\"hang2\"]\n"
                                      f"首条坏行：{bad[0][:40]}")
                    return False
                import tempfile
                fd, tmp = tempfile.mkstemp(dir=_repo_root, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(box.value or "")
                os.replace(tmp, _polyphone_path)
                ui.notify(position="top", type="positive",
                          message=f"多音字词典已保存（{len(entries)} 条）——若未生效请点击「重新加载模型」")
                return True

            with ui.grid(columns=2):
                _fix_box = ui.textarea(
                    label='念法纠正（每行：原文=读法）',
                    value=_pairs_to_text(get_nested_value(config, "gpt_sovits", "params", "pronunciation_fix")),
                    placeholder='整词改写，如：\nAI=人工智能\nTTS=文本转语音',
                ).style("width:100%;").tooltip('念法纠正优先于多音字替换；最长原文优先匹配。失焦即保存；合成时即时读取（改完下一条即生效）')
                _poly_box = ui.textarea(
                    label='多音字（GPT-SoVITS 引擎词典：多音字.txt）',
                    value=_read_polyphone_file(),
                    placeholder='每行一条：词语 ["拼音1","拼音2"]\n如：一行 ["yi4","hang2"]\n说服 ["shui4","fu2"]\n'
                                '# 开头为注释；引擎 g2p 按上下文选音（改发音不改正文，发音精确）',
                ).style("width:100%;").tooltip(
                    '直读直写仓库根「多音字.txt」（GPT-SoVITS 引擎 g2p 词典，编译引擎运行时读取）。'
                    '失焦即保存；保存后若未生效请点击「重新加载模型」')

            # 失焦保存（blur 在输入完成/点击他处时必触发；on_change 每键高频
            # 写盘+WebSocket 竞态退役）
            _fix_box.on('blur', lambda: _save_pairs(
                "pronunciation_fix", _fix_box, "念法纠正"))
            _poly_box.on('blur', lambda: _save_polyphone_file(_poly_box))

            def _save_both_fixes():
                ok1 = _save_pairs("pronunciation_fix", _fix_box, "念法纠正")
                ok2 = _save_polyphone_file(_poly_box)
                if ok1 or ok2:
                    ui.notify(position="top", type="positive",
                              message="念法纠正/多音字已保存")

            ui.button('保存念法纠正/多音字', on_click=_save_both_fixes,
                      color=button_internal_color).style(button_internal_css).tooltip(
                '显式保存兜底：失焦自动保存之外，点击此按钮强制落盘（防刷新竞态丢失）')

            ui.button('重新加载模型（按当前参数）', on_click=lambda: _load_gpt_sovits_model(config),
                      color=button_internal_color).style(button_internal_css)

        # edge-tts 配置（切片 3：WebTTS 退役改用 EDGE-TTS——任务书；
        # D-18：voice 搜索式下拉（内置常见 zh 声音清单+关键字过滤），默认值写明具体 voice 名；
        # 语速 rate 对接 P26 音频播放变速改造，默认 100%；其余参数系统默认不设配置项。
        # 2026-10-30：高级参数 JSON 透传（extra_args_json）退役移除——pitch 恒由
        # 随机语速联动接管（speed_to_edge_pitch），用户显式覆盖通道删除）
        with ui.card().style(card_css):
            ui.label("edge-tts 配置")
            with ui.grid(columns=2):
                ui.select(
                    label='音色（voice，输入关键字过滤）',
                    options={v: v for v in EDGE_TTS_VOICES},
                    value=get_nested_value(config, "edge_tts", "voice") or EDGE_TTS_DEFAULT_VOICE,
                    with_input=True,
                    on_change=lambda e: set_config_callback("edge_tts", "voice", value=e.value),
                ).style("width:100%;").tooltip('输入关键字过滤（如 xiaoxiao / zh）；支持键入清单外音色名（键入后需按回车确认，手动点保存栏位旁空白触发落盘）')
                FormField.create_input(
                    label='语速 rate（默认 100%）',
                    value=get_nested_value(config, "edge_tts", "rate") or EDGE_TTS_DEFAULT_RATE,
                    placeholder='加快 1.2 倍填 +20%，减慢 0.9 倍填 -10%',
                    on_change=lambda e: set_config_callback("edge_tts", "rate", value=e.value),
                    style="width:100%;")
        # v2_api_0821 配置卡与 WebTTS 配置卡已随切片 3 参数精简移除
        # （任务书：v2_api_0821 旧参数与 WebTTS 相关配置退役，改用 EDGE-TTS；
        # config 残留键由收尾批次键迁移清单处置，此处不再渲染）


def _load_gpt_sovits_model(config: Dict[str, Any]):
    """重新加载模型（F-2 接通）：经控制面 /tts/reload 触发引擎按当前 params 重建
    （换模事务：失败保持旧模型并 notify——CEO-S2 平移）。前端进程无引擎，走窄端点。"""
    import aiohttp
    from nicegui import ui as _ui
    try:
        from frontend.ui.tabs.platform_config import _api_base
        base = _api_base(config)
    except Exception:
        port = int(config.get("api_port") or 8000)
        base = f"http://127.0.0.1:{port}"

    async def _do():
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.post(f"{base}/tts/reload",
                                        timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    body = await resp.json()
                    if resp.status == 200 and body.get("success"):
                        _ui.notify(position="top", type="positive", message=f"模型重载完成：{body.get('message')}")
                    else:
                        _ui.notify(position="top", type="negative",
                                   message=f"重载失败（已保持旧模型）：{body.get('detail') or body.get('message')}")
        except Exception as e:
            try:
                _ui.notify(position="top", type="negative",
                           message=f"无法连接主系统控制面（main 进程未启动？）：{e}")
            except RuntimeError:
                # 后台 task 无 UI 上下文（slot empty）——降级日志
                logger.error(f"无法连接主系统控制面（main 进程未启动？）：{e}")

    import asyncio as _aio
    try:
        _loop = _aio.get_running_loop()
        _loop.create_task(_do())
    except RuntimeError:
        import threading as _th
        _th.Thread(target=lambda: _aio.run(_do()), daemon=True).start()

def _render_voice_profile_selector(config: Dict[str, Any], card_css: str):
    """P2-4/D7/D8/B-6：声音档案分组选择器 + 模型对写入 gpt_sovits 节。

    - 2026-10-30 审查批次：仅显示「数字人-声音训练-gpt-sovits」管线训练的档案
      （gpt_model/sovits_model 非空；RVC 管线档案不显示）
    - 2026-11-07 模型管理批次：删除按钮退役——模型文件管理统一收口到
      「数字人-声音训练-声音模型管理」区块（本页只选不删；原删除按钮删的是
      档案记录且 UI 不刷新，用户感知"不能生效"，语义与预期不符）
    - 点选档案 → 把 GPT-SoVITS 模型对/参考音频写入 gpt_sovits 节
    - 零档案 → 空态卡片指向训练页
    """
    from nicegui import ui as _ui
    from utils.voice_profiles import load_profiles

    all_profiles = load_profiles()
    # 管线过滤：仅 gpt-sovits 训练管线产出的档案（gpt_model 或 sovits_model 非空）
    profiles = {pid: p for pid, p in all_profiles.items()
                if p.get("gpt_model") or p.get("sovits_model")}
    if not profiles:
        with _ui.card().style(card_css):
            _ui.label("声音档案").style("font-weight:var(--font-weight-emphasis)")
            _ui.label("暂无 GPT-SoVITS 档案——「数字人-声音训练」完成后自动出现在此处").style(
                "font-size:12px;color:var(--text-secondary)")
        return

    with _ui.card().style(card_css):
        _ui.label("声音档案（GPT-SoVITS）")
        with _ui.grid(columns=2):
            current_gpt = get_nested_value(config, "gpt_sovits", "gpt_model_path") or ""
            for pid, prof in profiles.items():
                is_current = prof.get("gpt_model") and (
                    os.path.normcase(prof["gpt_model"]) == os.path.normcase(current_gpt))
                with _ui.row().style(
                    "width:100%;align-items:center;border:1px solid var(--border-color);"
                    "border-radius:var(--radius-md);padding:6px 10px;"
                ):
                    mark = "● " if is_current else ""
                    _ui.label(f"{mark}{prof.get('name', pid)}").style("flex:1")
                    if prof.get("note"):
                        _ui.label(prof["note"]).style(
                            "font-size:11px;color:var(--text-secondary)")
                    _ui.button("选用" if not is_current else "当前",
                               on_click=lambda pid=pid, prof=prof: _apply_profile(
                                   config, pid, prof),
                               ).props("dense unelevated no-caps")


def _apply_profile(config: Dict[str, Any], pid: str, prof: dict):
    """选用档案：模型对/参考音频写入 gpt_sovits 节 + 进程内换模事务（R-4）。

    换模语义：backend=inproc 时引擎实例重建式切换（新加载成功才 swap，失败保持旧模型
    并 notify——CEO-S2 平移）；backend=api 时提示用户自行重载外部服务。
    manual_restart() 托管重启依赖已退役（api_v2 托管不再存在）。
    """
    from nicegui import ui as _ui
    # gpt_sovits 节字段写入（经 config 回调持久化）
    try:
        from frontend.utils.common import notify_ok
    except ImportError:
        notify_ok = None
    fields = {
        "gpt_model_path": prof.get("gpt_model", ""),
        "sovits_model_path": prof.get("sovits_model", ""),
        "ref_audio_path": prof.get("ref_audio", ""),
        "prompt_text": prof.get("prompt_text", ""),
        "prompt_language": prof.get("prompt_lang", "zh"),
    }
    # 经前端已有的 set_config 机制由页面保存——这里直接改内存 config 并持久化
    from utils.config import Config
    import json, tempfile, os as _os
    cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))), "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        data = json.load(f)
    data["gpt_sovits"].update(fields)
    data["gpt_sovits"]["voice_profile_id"] = pid
    # 2026-10-29 批次：params 节同步（面板读 params.ref_audio_paths/prompt_texts，
    # 档案只写根节 ref_audio_path/prompt_text 会造成新输入项与档案参考串档）
    _params_sync = data["gpt_sovits"].setdefault("params", {})
    if prof.get("ref_audio"):
        _params_sync["ref_audio_paths"] = [prof["ref_audio"]]
        _params_sync["prompt_texts"] = [prof.get("prompt_text", "")]
    fd, tmp = tempfile.mkstemp(dir=_os.path.dirname(cfg_path), suffix=".json")
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    _os.replace(tmp, cfg_path)
    # 重载共享 config 缓存（E-4 单写者：本处经 voice_profiles 同款原子替换）
    Config.reload(cfg_path)
    # R-4：进程内换模事务（后台线程执行，切换中 UI loading 由调用方置禁）
    import threading as _th

    def _do_switch():
        # 后台线程无 UI 上下文（页面可能已关）——notify 统一降级日志（slot 异常防御）
        from utils.gpt_sovits_inproc import get_engine
        eng = get_engine()
        if eng is None:
            logger.warning("进程内引擎不可用（backend=api 模式下请自行重载外部服务）")
            return
        ok, msg = eng.switch_model(prof.get("gpt_model", ""), prof.get("sovits_model", ""))
        (logger.info if ok else logger.error)(
            f"声音档案「{prof.get('name', pid)}」切换{'成功' if ok else f'失败，已保持旧模型：{msg}'}")

    _th.Thread(target=_do_switch, daemon=True).start()
    _ui.notify(position="top", type="positive",
               message=f"已选用声音档案「{prof.get('name', pid)}」，模型切换中…")
