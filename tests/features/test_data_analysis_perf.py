# -*- coding: UTF-8 -*-
"""
数据分析进页卡顿优化守卫（2026-10-24 批次）

病根三因：Data_Analysis 每次进页新建（连接池+清理线程泄漏）、jieba 顶层
导入（首进 ~1s）、三查询同步跑 UI 线程（词云随数据量线性变卡）。

守卫：
1. utils 层：jieba 延迟导入、词云查询限量、debug 日志不落全量数据
2. 前端层：进程级单例 + 异步取数 + options 原地更新（禁 remove 重建模式）
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class TestUtilsDataAnalysis:
    """utils/data_analysis.py 性能约束。"""

    def test_jieba_lazy_import(self):
        """jieba 不得在模块顶层导入（顶层导入=每次首渲卡 ~1s）。"""
        src = _src("utils/data_analysis.py")
        head = src.split("def get_most_common_words")[0]
        assert "import jieba" not in head, "jieba 必须延迟到 get_most_common_words 内导入"
        assert "import jieba" in src  # 函数体内确实导入
        assert "except ImportError" in src  # 缺依赖降级

    def test_wordcloud_query_bounded(self):
        """词云查询必须限量（防数据量增长后 jieba 全表分词线性变卡）。"""
        src = _src("utils/data_analysis.py")
        assert "DANMU_WORDCLOUD_SCAN_LIMIT" in src
        assert "LIMIT" in src and "ORDER BY rowid DESC" in src

    def test_debug_logs_no_full_dump(self):
        """debug 日志不落全量数据集（原实现 list_list/dict_list 全量序列化）。"""
        src = _src("utils/data_analysis.py")
        assert 'logger.debug(dict_list)' not in src
        assert 'logger.debug(f"list_list=' not in src
        assert 'logger.debug(f"username_list=' not in src
        assert 'logger.debug(f"total_price_list=' not in src


class TestFrontendTabAsyncLoading:
    """frontend/ui/tabs/data_analysis.py 加载逻辑约束。"""

    def test_singleton_no_per_entry_rebuild(self):
        """进程级单例：连接池/清理线程不随进页重复创建。"""
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert "_DATA_ANALYSIS_SINGLETON" in src
        assert "_DATA_ANALYSIS_LOCK" in src
        assert "Data_Analysis(path)" in src  # 单例路径内构建

    def test_async_off_thread_loading(self):
        """取数必须走 asyncio.to_thread（禁同步查询阻塞 UI 线程）。"""
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert "asyncio.to_thread" in src
        assert "ui.timer(0.05, _initial_load, once=True)" in src  # 进页异步取数

    def test_options_inplace_update(self):
        """图表更新为 options 原地更新（禁 remove(0)+重建+move 抖动模式）。

        2026-10-24 实测：EDTalk runtime312 的 NiceGUI EChart.options 为只读
        property（整体赋值抛 no setter）——必须 _set_option（clear+update
        底层 dict 后 update() 推送）。
        """
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert ".remove(0)" not in src
        assert ".move(" not in src
        assert "echart.options = " not in src, "EChart.options 只读，禁止整体赋值"
        assert "_set_option(" in src
        assert "echart.options.clear()" in src and "echart.update()" in src

    def test_placeholder_first_render(self):
        """进页首渲为占位 option（渲染路径零查库零导入）。"""
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert "ui.echart(\n            _placeholder_option" in src
        assert "get_comment_word_cloud_option(\n" not in src.split("def _refresh")[0].split("_charts.append")[0]
        # 首渲不调用任何 get_*_option（取数只在 _refresh/_initial_load 中）
        render_section = src.split("async def _refresh")[0]
        assert "get_comment_word_cloud_option" not in render_section
        assert "get_integral_option" not in render_section
        assert "get_gift_option" not in render_section


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
