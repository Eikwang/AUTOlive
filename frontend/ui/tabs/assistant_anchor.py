# -*- coding: UTF-8 -*-
"""
助播标签页模块
从 webui-bak.py 的 assistant_anchor_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

/autoplan main-20261026-214845 批次 T5/T7：
- 触发类型 v2 十项枚举（AI对话/聊天/念弹幕/礼物/入场/关注/定时任务/闲时任务/
  积分/点歌；复读归拢"聊天"，三项移除）——checkbox 补落盘（修 F2：原实现无
  _auto_save 无 on_value_change，勾选重启即失效）
- "聊天"副标题：含聊天页消息与复读内容（native CEO 3.4）
- 音色下拉对齐主链 audio_synthesis_type 选项集（gpt_sovits/edge_tts 规范值）
- 文本匹配格式下拉退役 json 项（F6 实证两格式同一加载器；存量经 settings
  迁移为 text）——唯一格式=一问一答 TXT（问题|答案）
- 路由观测面板（D11/E-S2）：每类型最近路由决策+累计计数，只读，经主进程 API
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_assistant_anchor_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建助播标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="assistant_anchor")

    # 音频合成类型选项（T5：对齐主链 audio_synthesis_type 选项集——tts.py D-19
    # 同一选项族；规范值 gpt_sovits/edge_tts，运行时 E-E1 规范化回落兜底）
    audio_synthesis_type_options = {
        'gpt_sovits': 'GPT-SoVITS',
        'edge_tts': 'edge-tts',
    }

    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.input(label='助播名', value=get_nested_value(config, "assistant_anchor", "username"), placeholder='助播的用户名').style("width:100%;"), ("assistant_anchor", "username"))
        _audio_syn_val = get_nested_value(config, "assistant_anchor", "audio_synthesis_type")
        _audio_syn_options = dict(audio_synthesis_type_options)
        if _audio_syn_val and _audio_syn_val not in _audio_syn_options:
            # 存量非法值保留展示（运行时回落主链音色，E-E1），修正后自动落盘
            _audio_syn_options[_audio_syn_val] = f"{_audio_syn_val}（非法值，请改选）"
        _auto_save(ui.select(
            label='语音合成（助播音色）',
            options=_audio_syn_options,
            value=_audio_syn_val
        ).style("width:100%;"), ("assistant_anchor", "audio_synthesis_type"))

    # 触发类型（v2 十项枚举；checkbox 补落盘修 F2）
    with ui.card().style(card_css):
        ui.label("触发类型（勾选后该类信息走助播流程：助播 TTS 合成并系统消费，与数字人并行）")
        # (config 值, 显示文案, 副标题)——复读归拢"聊天"（D2 终门 uc1a）
        assistant_anchor_type_list = [
            ("ai_chat", "AI对话", "弹幕由助播播报：助播知识库优先，未命中走 LLM"),
            ("chat", "聊天", "含聊天页消息与复读内容（复读不再抢占数字人）"),
            ("read_comment", "念弹幕", "念弹幕功能的播报由助播播出"),
            ("gift", "礼物", "礼物答谢文案由助播播出"),
            ("entrance", "入场", "入场欢迎文案由助播播出"),
            ("follow", "关注", "关注答谢文案由助播播出"),
            ("schedule", "定时任务", "定时任务消息由助播播出"),
            ("idle_time_task", "闲时任务", "闲时任务文本消息由助播播出"),
            ("integral", "积分", "积分查询回复由助播播出"),
            ("song", "点歌", "点歌匹配结果文案由助播播出（歌曲音频走编排）"),
        ]
        _current_types = set(str(t) for t in (get_nested_value(config, "assistant_anchor", "type") or []))
        _type_boxes: Dict[str, Any] = {}

        def _persist_types():
            """勾选落盘（修 F2）：整表重建写回 assistant_anchor.type（窄端点链）。"""
            checked = [t for t, box in _type_boxes.items() if box.value]
            set_config_callback("assistant_anchor", "type", value=checked)

        with ui.row().style("flex-wrap:wrap; gap:8px;"):
            for _t, _label, _sub in assistant_anchor_type_list:
                with ui.column().style("min-width:180px;"):
                    _type_boxes[_t] = ui.checkbox(
                        text=_label,
                        value=_t in _current_types,
                        on_value_change=lambda: _persist_types(),
                    )
                    ui.label(_sub).style('font-size: var(--font-size-assist, 12px); opacity: 0.7;')

    # 文本匹配（唯一格式=一问一答 TXT：问题|答案；json 项已退役——F6 实证
    # 两格式历来同一管道行加载器，存量 format=json 经 settings 迁移为 text）
    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用助播文本知识库', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "enable")).style(switch_internal_css), ("assistant_anchor", "local_qa", "text", "enable"))
            _auto_save(ui.input(label='文本问答数据路径', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "file_path"), placeholder='一问一答 TXT 路径（每行：问题|答案）').style("width:100%;").tooltip('一问一答 TXT：每行一条，问题|答案；同答案多问法写多行'), ("assistant_anchor", "local_qa", "text", "file_path"))
            _auto_save(ui.input(label='文本最低相似度', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "similarity"), placeholder='最低文本匹配相似度').style("width:100%;"), ("assistant_anchor", "local_qa", "text", "similarity"))

    # 音频匹配（匹配问题→目录内音频文件标题，命中直接播放该音频）
    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用助播音频匹配', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "enable")).style(switch_internal_css), ("assistant_anchor", "local_qa", "audio", "enable"))
            _auto_save(ui.select(
                label='匹配档位',
                options={'包含关系': '包含关系（精确+包含）', '相似度匹配': '相似度匹配（精确+包含+相似度）'},
                value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "type")
            ).style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "type"))
            _auto_save(ui.input(label='音频存储路径', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "file_path"), placeholder='本地问答音频文件存储路径').style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "file_path"))
            _auto_save(ui.input(label='音频最低相似度', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "similarity"), placeholder='最低音频匹配相似度').style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "similarity"))

    # 路由观测面板（D11/E-S2：只读，经主进程 API 取数——"所配即所行"验证）
    with ui.card().style(card_css):
        ui.label("路由观测（最近决策与累计计数）")
        _stats_label = ui.label("尚未加载").style('font-size: var(--font-size-assist, 12px); white-space:pre-wrap;')

        def _main_api_url() -> str:
            api_port = get_nested_value(config, "api_port", default=8082)
            api_ip = get_nested_value(config, "api_ip", default="127.0.0.1")
            if str(api_ip) == "0.0.0.0":
                api_ip = "127.0.0.1"
            return f"http://{api_ip}:{api_port}"

        async def _refresh_stats():
            import aiohttp
            try:
                async with aiohttp.ClientSession(trust_env=False) as session:
                    async with session.get(f"{_main_api_url()}/assistant_anchor/route-stats",
                                           timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        body = await resp.json() if resp.status == 200 else {}
                counters = body.get("counters") or {}
                last = body.get("last") or {}
                if not counters:
                    _stats_label.set_text("暂无路由记录（勾选触发类型后，直播/聊天触发一条即可见）")
                    return
                lines = ["产线|来源        计数   最近决策（命中层→去向）"]
                for key in sorted(counters, key=lambda k: -counters[k]):
                    info = last.get(key) or {}
                    arrows = f"{'助播' if info.get('routed') else '原路'} ← {info.get('hit_layer', '-')}"
                    lines.append(f"{key:<14} {counters[key]:>5}   {arrows}")
                _stats_label.set_text("\n".join(lines))
            except Exception as e:
                _stats_label.set_text(f"读取失败（主系统未启动？）：{e}")

        ui.button("刷新路由观测", on_click=_refresh_stats).props("outline")
