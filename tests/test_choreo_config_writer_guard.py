# -*- coding: UTF-8 -*-
"""编排配置写者测试侧收口守卫（2026-11-05 调查批次）。

事故链：test_choreography_completion.py::TestEndpoints 的 POST /choreography/rules
测试（test_rules_endpoint_non_loopback_403_with_allow）无 _config_path 隔离——
applied=True 路径调 _write_choreography_section 写 cwd 相对 "config.json"
（pytest cwd=项目根=真实生产配置）。测试固件 _cfg_node(enable=False)
（girl_A/小心心/default_segments ["1","55"]）整节覆盖用户编排规则——
每跑一次全量 pytest 清一次（「每次修复动作编排问题都清空配置」根因）。

实测复现（2026-11-05）：埋哨兵规则 USER_REAL_SENTINEL → 跑单条该测试
（0.15s）→ 真实 config.json 的 choreography 节被测试固件整节替换。

防御三层（本文件为第 3 层，删任一层立即红）：
1. conftest autouse fixture（会话级默认重定向 tmp）
2. TestEndpoints 类级 autouse（显式隔离）
3. 本守卫：断言重定向在位 + 无隔离路由测试不再出现
"""
import glob
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_config_path_redirects_into_tmp_in_test_session(tmp_path):
    """conftest 防御生效：测试会话内 choreo_api._config_path() 必须指向 tmp，
    不得指向项目根真实 config.json（cwd 相对默认值的等价形态也拒绝）。"""
    import utils.orchestration.api as choreo_api
    p = os.path.abspath(choreo_api._config_path())
    real = os.path.abspath(os.path.join(ROOT, "config.json"))
    assert str(tmp_path) in p, (
        f"choreo_api._config_path() 在测试会话内返回 {p}（未重定向 tmp）——"
        f"conftest._isolate_choreo_config_writer 防御被删除/失效，"
        f"编排端点测试将覆盖生产配置 {real}")
    assert p != real, "测试写者路径与生产 config.json 同径"


def test_routes_tests_must_isolate_config_path():
    """结构守卫：注册编排路由的测试文件必须含 _config_path 隔离证据
    （显式 monkeypatch 或隔离 fixture 名）。新增无隔离测试文件时本守卫红。"""
    evidence = ("_config_path", "_isolate_choreo_config_writer", "_isolate_config_path")
    offenders = []
    for f in glob.glob(os.path.join(ROOT, "tests", "**", "*.py"), recursive=True):
        if os.path.basename(__file__) == os.path.basename(f):
            continue
        try:
            src = open(f, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        if "register_choreography_routes" not in src:
            continue
        if not any(e in src for e in evidence):
            offenders.append(os.path.relpath(f, ROOT))
    assert not offenders, (
        f"以下测试文件注册 /choreography/* 路由但无 _config_path 隔离"
        f"（applied 分支会写真实 config.json）：{offenders}；"
        f"修复=类级 autouse fixture 重定向 tmp（参照 test_choreography_completion.py）")
