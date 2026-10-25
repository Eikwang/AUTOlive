# -*- coding: UTF-8 -*-
"""
数据分析标签页模块
从 webui-bak.py 的 data_analysis_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

2026-10-24 进页卡顿优化（原实现进页卡 2-3s，三因叠加）：
1. Data_Analysis 每次进页新建实例→新建 SQLite 连接池+守护清理线程且旧池
   永不关闭（线程/连接泄漏）。改为进程级单例（配置路径变化才重建）。
2. utils.data_analysis 顶层 import jieba→首次进页付 ~1s 词典库导入。已改
   延迟导入（utils 层），进页零导入成本。
3. 三个图表查询同步跑在 UI 线程（词云 jieba 全表分词随数据量线性变卡）。
   改为进页先渲占位图，ui.timer 异步经 asyncio.to_thread 后台取数，
   完成后 _set_option 原地更新（clear+update 底层 dict——EDTalk runtime312
   的 NiceGUI EChart.options 为只读 property，整体赋值会抛 no setter）。
   手动更新按钮同模式，不再 remove(0)+重建+move。
"""
import asyncio
import threading
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value

# ---------------- Data_Analysis 进程级单例 ----------------
# Data_Analysis 持有 SQLite 连接池（含守护清理线程），必须复用；
# 配置路径变化时重建（保持原"每次进页按最新配置实例化"的语义）。
_DATA_ANALYSIS_LOCK = threading.Lock()
_DATA_ANALYSIS_SINGLETON = None
_DATA_ANALYSIS_PATH = None


def _get_data_analysis():
    """取/建 Data_Analysis 单例（Config 类级缓存，重复实例化本身廉价，
    贵的是连接池与清理线程——单例后仅首次进页付出，后续零开销）。"""
    global _DATA_ANALYSIS_SINGLETON, _DATA_ANALYSIS_PATH
    from frontend.config.paths import get_config_path
    path = str(get_config_path())
    if _DATA_ANALYSIS_SINGLETON is None or _DATA_ANALYSIS_PATH != path:
        with _DATA_ANALYSIS_LOCK:
            if _DATA_ANALYSIS_SINGLETON is None or _DATA_ANALYSIS_PATH != path:
                from utils.data_analysis import Data_Analysis
                _DATA_ANALYSIS_SINGLETON = Data_Analysis(path)
                _DATA_ANALYSIS_PATH = path
    return _DATA_ANALYSIS_SINGLETON


def _placeholder_option(title: str, loading: bool) -> dict:
    """图表占位/兜底 option（进页首渲与加载失败态；空 series 零渲染负载）。"""
    return {
        'title': {'text': f'{title}（{"加载中…" if loading else "暂无数据"}）', 'left': 'center'},
        'series': [],
    }


def _set_option(echart, option: dict):
    """echart options 原地更新。

    2026-10-24 实测修复：EDTalk runtime312 的 NiceGUI 中 EChart.options 为
    只读 property（整体赋值抛 AttributeError: no setter）——必须修改底层
    dict 后调 update() 推送全量 props。
    """
    echart.options.clear()
    echart.options.update(option)
    echart.update()


def create_data_analysis_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    data_analysis=None
):
    """创建数据分析标签页"""
    from utils.my_log import logger

    card_css = theme_config.get("card", "")
    echart_css = theme_config.get("echart", "height: 300px;")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="data_analysis")

    # 实例持有器：注入实例（测试）优先；否则异步后台建单例（UI 线程零阻塞）
    _holder = {"da": data_analysis}
    _charts = []  # (echart, base_title) 登记表：模块加载失败时兜底占位

    async def _ensure_da():
        """确保单例就绪（首次在后台线程构建：含连接池建立与模块导入）。"""
        if _holder["da"] is None:
            _holder["da"] = await asyncio.to_thread(_get_data_analysis)
        return _holder["da"]

    _seq: dict = {}  # 审查修复（发布批次）：每图序号 token——丢弃迟到的旧结果

    async def _refresh(echart, base_title: str, fetch: Callable):
        """后台线程取数 → echart.options 原地更新（UI 线程零阻塞）。"""
        if _holder["da"] is None:
            ui.notify(position="top", type="warning", message="数据分析模块尚未加载完成，请稍候重试")
            return
        seq_key = id(echart)
        _seq[seq_key] = _seq.get(seq_key, 0) + 1
        my_seq = _seq[seq_key]
        _set_option(echart, _placeholder_option(base_title, loading=True))
        try:
            option = await asyncio.to_thread(fetch)
        except Exception as e:
            logger.error(f"数据分析图表加载失败（{base_title}）: {e}")
            option = None
        if _seq.get(seq_key) != my_seq:
            return  # 已有更新的请求在途/完成，丢弃本次旧结果（防旧覆盖新）
        _set_option(echart, option if option else _placeholder_option(base_title, loading=False))

    # 弹幕词云
    data_analysis_comment_word_cloud_card = ui.card()
    with data_analysis_comment_word_cloud_card.style("width:100%;"):
        word_top_n = int(get_nested_value(config, "data_analysis", "comment_word_cloud", "top_num") or 10)
        echart_comment_word_cloud = ui.echart(
            _placeholder_option('弹幕关键词统计', loading=True)
        ).style(echart_css)
        _charts.append((echart_comment_word_cloud, '弹幕关键词统计'))

        with ui.grid(columns=3):
            input_data_analysis_comment_word_cloud_top_num = _auto_save(ui.input(
                label='前N个关键词',
                value=get_nested_value(config, "data_analysis", "comment_word_cloud", "top_num"),
                placeholder='筛选前N个弹幕关键词做为词云数据'
            ).style("width:100%;"), ("data_analysis", "comment_word_cloud", "top_num"))

            async def update_echart_comment_word_cloud():
                top_n = int(input_data_analysis_comment_word_cloud_top_num.value or 10)
                await _refresh(echart_comment_word_cloud, '弹幕关键词统计',
                               lambda: _holder["da"].get_comment_word_cloud_option(top_n))

            ui.button('更新数据', on_click=update_echart_comment_word_cloud)

    # 积分榜
    data_analysis_integral_card = ui.card()
    with data_analysis_integral_card.style("width:100%;"):
        integral_top_n = int(get_nested_value(config, "data_analysis", "integral", "top_num") or 10)
        echart_integral = ui.echart(
            _placeholder_option('积分表数据统计', loading=True)
        ).style(echart_css)
        _charts.append((echart_integral, '积分表数据统计'))

        with ui.grid(columns=3):
            input_data_analysis_integral_top_num = _auto_save(ui.input(
                label='Top N个数据',
                value=get_nested_value(config, "data_analysis", "integral", "top_num"),
                placeholder='筛选Top N个数据'
            ).style("width:100%;"), ("data_analysis", "integral", "top_num"))

            async def update_echart_integral(type: str):
                top_n = int(input_data_analysis_integral_top_num.value or 10)
                await _refresh(echart_integral, '积分表数据统计',
                               lambda: _holder["da"].get_integral_option(type, top_n))

            def _integral_btn(btn_type: str):
                async def _h(_e=None):
                    await update_echart_integral(btn_type)
                return _h

        # UI-16 批次 R9/D-4/DV-1g：四按钮一行显示 flex 均分等宽（移出 3 列网格，
        # 防按钮被网格切行；窄视口自动 2×2 换行）
        with ui.row().style('width:100%; display:flex; gap:8px; flex-wrap:wrap;'):
            ui.button('获取积分榜', on_click=_integral_btn('integral')).style('flex:1 1 140px; min-width:140px;')
            ui.button('获取观看榜', on_click=_integral_btn('view_num')).style('flex:1 1 140px; min-width:140px;')
            ui.button('获取签到榜', on_click=_integral_btn('sign_num')).style('flex:1 1 140px; min-width:140px;')
            ui.button('获取金额榜', on_click=_integral_btn('total_price')).style('flex:1 1 140px; min-width:140px;')

    # 礼物榜
    data_analysis_gift_card = ui.card()
    with data_analysis_gift_card.style("width:100%;"):
        gift_top_n = int(get_nested_value(config, "data_analysis", "gift", "top_num") or 10)
        echart_gift = ui.echart(
            _placeholder_option('礼物榜单', loading=True)
        ).style(echart_css)
        _charts.append((echart_gift, '礼物榜单'))

        with ui.grid(columns=3):
            input_data_analysis_gift_top_num = _auto_save(ui.input(
                label='Top N个数据',
                value=get_nested_value(config, "data_analysis", "gift", "top_num"),
                placeholder='筛选Top N个数据'
            ).style("width:100%;"), ("data_analysis", "gift", "top_num"))

            async def update_echart_gift():
                top_n = int(input_data_analysis_gift_top_num.value or 10)
                await _refresh(echart_gift, '礼物榜单',
                               lambda: _holder["da"].get_gift_option(top_n))

            ui.button('更新数据', on_click=update_echart_gift)

    # 进页首渲完成 50ms 后异步取数（不阻塞 UI 线程：单例构建/查询/jieba 分词
    # 全在后台线程，完成后 options 原地就位）
    async def _initial_load():
        try:
            await _ensure_da()
        except Exception as e:
            logger.error(f"数据分析模块加载失败: {e}")
            ui.notify(position="top", type="negative", message=f"数据分析模块加载失败: {e}")
            for echart, base_title in _charts:
                _set_option(echart, _placeholder_option(base_title, loading=False))
            return
        # 审查修复（发布批次）：三图并行取数（原串行 await——填充时间=三者
        # 之和，最慢的词云 jieba 阻塞另外两图；gather 后=最慢者）
        await asyncio.gather(
            update_echart_comment_word_cloud(),
            update_echart_integral("integral"),
            update_echart_gift(),
        )

    ui.timer(0.05, _initial_load, once=True)
