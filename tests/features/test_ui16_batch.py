# -*- coding: UTF-8 -*-
"""
UI-16 批次守卫测试（WebUI 功能调整与修复 16 项，终门 rec 批准实施）

四项完成定义守卫（EV-1c）+ talk 页渲染 smoke（EV-1i），锁定本批关键行为
不被回退：
(a) R10 念弹幕触发周期键迁移（periodic_time 单值/max 回填/旧键退役/分支隔离）
(b) R5 动态文案存量迁移（成功导入/不可读非破坏保留/合并去重幂等/体量上限）
(c) R3 过滤总开关迁移（ENABLE_PATHS 注册+per-path 确认注册表+persistent 全关闭路径）
(d) R8 页面移除清理（双页函数/导航注册/enable_paths 注册清零+config_templates 红线）
(e) E4 talk 页渲染 smoke（P1 password 参数修复+两列布局防回退）
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class _FakeSettings:
    """settings._ensure_defaults 的最小替身（只提供 .save() 与 _config）。"""

    def __init__(self, cfg: dict):
        self._config = cfg
        self.saved = False

    def save(self):
        self.saved = True


# ---------------------------------------------------------------------------
# (a) R10 念弹幕触发周期键迁移（EV-1c-a）
# ---------------------------------------------------------------------------

class TestR10PeriodicTimeMigration:
    def _migrate(self, cfg: dict):
        from frontend.config.settings import _ensure_defaults
        fake = _FakeSettings(cfg)
        _ensure_defaults(fake)
        return cfg

    def test_max_backfill_and_old_keys_retired(self):
        """存量 min/max → periodic_time=max 回填，旧键退役（EV-1g 迁移口径）。"""
        cfg = {"read_comment": {"periodic_trigger": {
            "enable": True, "periodic_time_min": 5, "periodic_time_max": 10,
            "trigger_num_min": 0, "trigger_num_max": 1}}}
        out = self._migrate(cfg)
        pt = out["read_comment"]["periodic_trigger"]
        assert pt["periodic_time"] == 10
        assert "periodic_time_min" not in pt
        assert "periodic_time_max" not in pt
        assert pt["trigger_num_min"] == 0  # 触发次数 min/max 保留

    def test_missing_keys_default_60(self):
        """新装/部分迁移配置缺 min/max → periodic_time 缺省 60 秒（EV-1g）。"""
        cfg = {"read_comment": {"periodic_trigger": {"enable": True}}}
        out = self._migrate(cfg)
        assert out["read_comment"]["periodic_trigger"]["periodic_time"] == 60

    def test_existing_value_not_overwritten(self):
        """periodic_time 已存在 → 不覆盖（只补缺失，_ensure_defaults 语义）。"""
        cfg = {"read_comment": {"periodic_trigger": {
            "periodic_time": 90, "periodic_time_min": 5, "periodic_time_max": 10}}}
        out = self._migrate(cfg)
        assert out["read_comment"]["periodic_trigger"]["periodic_time"] == 90

    def test_other_branches_isolated(self):
        """分支隔离：local_qa/thanks 各节的 min/max 保持不动（EV-1c-a 隔离断言）。"""
        # UI-16 二轮验收（任务 10）：thanks 三通道也合并单值；local_qa 不动（隔离）
        cfg = {
            "read_comment": {"periodic_trigger": {"periodic_time_min": 5, "periodic_time_max": 10}},
            "local_qa": {"periodic_trigger": {"periodic_time_min": 1, "periodic_time_max": 2}},
            "thanks": {"gift": {"periodic_trigger": {"periodic_time_min": 3, "periodic_time_max": 4}}},
        }
        out = self._migrate(cfg)
        assert out["local_qa"]["periodic_trigger"]["periodic_time_min"] == 1  # 隔离不动
        assert out["local_qa"]["periodic_trigger"]["periodic_time_max"] == 2
        assert out["thanks"]["gift"]["periodic_trigger"]["periodic_time"] == 4  # max 回填
        assert "periodic_time_min" not in out["thanks"]["gift"]["periodic_trigger"]
        assert out["thanks"].get("entrance", {}).get("periodic_trigger") is None  # 无节不创建

    def test_timer_handler_branch_isolation_source(self):
        """源级：timer_handler read_comment 分支单值化，其余分支保持 randint。"""
        src = _src("utils/handlers/timer_handler.py")
        assert 'self.my_handle.config.get(type, "periodic_trigger", "periodic_time")' in src
        assert "except (TypeError, ValueError):\n                periodic_time = 60" in src
        # 分支隔离：local_qa/thanks 分支的 min/max+randint 必须仍在
        assert src.count("random.randint(periodic_time_min, periodic_time_max)") == 1  # 仅 local_qa
        assert src.count('periodic_trigger", "periodic_time_min') == 1
        # thanks 三分支（type2 区分）+read_comment 均单值化
        assert src.count('"periodic_trigger", "periodic_time")') == 4

    def test_read_comment_ui_merged(self):
        """源级：read_comment.py 单参数「触发周期（秒）」，min/max 输入框已移除。"""
        src = _src("frontend/ui/tabs/read_comment.py")
        assert "触发周期（秒）" in src
        assert "periodic_time_min" not in src
        assert "periodic_time_max" not in src
        assert src.count("trigger_num_min") == 2  # 触发次数 min/max 保留（value+keys 各一）
        assert src.count("trigger_num_max") == 2


# ---------------------------------------------------------------------------
# (b) R5 动态文案存量迁移（EV-1c-b）
# ---------------------------------------------------------------------------

class TestR5CopyMigration:
    def test_success_import(self, tmp_path):
        """成功：目录内文件内容逐条导入（一文件=一条文案）。"""
        from frontend.ui.tabs.trends_copywriting import _migrate_folders
        folder = tmp_path / "文案"
        folder.mkdir()
        (folder / "a.txt").write_text("欢迎来到直播间", encoding="utf-8")
        (folder / "b.txt").write_text("感谢关注", encoding="utf-8")
        imported, succeeded, failed = _migrate_folders([str(folder)])
        assert imported == ["欢迎来到直播间", "感谢关注"]
        assert succeeded == [str(folder)]
        assert failed == []

    def test_unreadable_folder_non_destructive(self, tmp_path, monkeypatch):
        """不可读：目录抛错记入 failed（调用方保留 copywriting 条目重试）。
        注：get_all_file_paths 对不存在路径返回空列表（视为空目录完成），
        真实不可读=权限拒绝抛异常——monkeypatch 模拟。"""
        from frontend.ui.tabs import trends_copywriting as tc
        def _boom(self, path):
            raise PermissionError(f"拒绝访问: {path}")
        from utils.common import Common as _C
        monkeypatch.setattr(_C, "get_all_file_paths", _boom)
        imported, succeeded, failed = tc._migrate_folders([str(tmp_path / "locked")])
        assert imported == []
        assert succeeded == []
        assert len(failed) == 1 and "目录不可读" in failed[0][1]

    def test_mixed_partial_success(self, tmp_path, monkeypatch):
        """部分成功：可读目录导入+不可读目录进 failed（DV-1b⑥ 部分成功态）。"""
        from frontend.ui.tabs import trends_copywriting as tc
        good = tmp_path / "good"
        good.mkdir()
        (good / "x.txt").write_text("好文案", encoding="utf-8")
        from utils.common import Common as _C
        real_get = _C.get_all_file_paths
        def _selective(self, path):
            if "locked" in str(path):
                raise PermissionError(f"拒绝访问: {path}")
            return real_get(self, path)
        monkeypatch.setattr(_C, "get_all_file_paths", _selective)
        imported, succeeded, failed = tc._migrate_folders(
            [str(good), str(tmp_path / "locked")])
        assert imported == ["好文案"]
        assert succeeded == [str(good)]
        assert len(failed) == 1

    def test_file_size_cap(self, tmp_path):
        """EV-1a：单文件读取上限 100KB 截断。"""
        from frontend.ui.tabs import trends_copywriting as tc
        assert tc._MIGRATE_MAX_FILE_BYTES == 100 * 1024
        assert tc._MIGRATE_MAX_FILES_PER_FOLDER == 200

    def test_ui_migration_semantics_source(self):
        """源级：进页重试+非破坏退役+合并去重幂等语义在 UI 侧落地。"""
        src = _src("frontend/ui/tabs/trends_copywriting.py")
        assert "background_tasks.create(_migrate_folder_paths())" in src
        assert "dict.fromkeys(existing + imported)" in src  # 幂等去重
        assert 'set_config_callback("trends_copywriting", "copywriting", value=remaining)' in src
        assert "恢复该目录可读后重新进入本页即自动回填" in src  # 失败指引文案

    def test_task_scheduler_dual_chain_and_fastrag(self):
        """源级：消费链双链（copy 优先/旧链回退）+fastrag 放行+未知降级+S-3 截断。"""
        src = _src("utils/task_scheduler.py")
        assert 'self.config.get("trends_copywriting", "copy")' in src
        assert "copywriting.get(\"folder_path\", \"\")" in src  # 旧链回退保留
        assert 'llm_type not in ("custom_llm", "fastrag")' in src
        assert "resp_content = resp_content[:500]" in src  # S-3 截断保持
        assert 'self.config.get("trends_copywriting", "prompt_change_enable")' in src  # 页级


# ---------------------------------------------------------------------------
# (c) R3 过滤总开关迁移（EV-1c-c）
# ---------------------------------------------------------------------------

class TestR3FilterMasterSwitch:
    def test_enable_paths_registered(self):
        """enable_paths.py 注册 filter_config → filter.filter_master_enable。"""
        from frontend.config.enable_paths import ENABLE_PATHS
        assert ENABLE_PATHS.get("filter_config") == ("filter", "filter_master_enable")

    def test_confirm_registry_source(self):
        """源级：per-path 确认注册表含 filter_master_enable+persistent 全关闭路径。"""
        src = _src("frontend/ui/layout.py")
        assert '("filter", "filter_master_enable")' in src
        assert "CONFIRM_CLOSE_PATHS" in src
        assert "persistent" in src  # EV-1h：ESC/遮罩不可关闭
        assert "确认关闭过滤总开关" in src
        assert "违禁词过滤同时失效" in src  # D-24 风险文案保留

    def test_param_page_switch_removed(self):
        """源级：参数页旧开关+确认流已移除（唯一入口=二级菜单）。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        assert "ui.switch('过滤总开关'" not in src
        assert "_on_master_change" not in src


# ---------------------------------------------------------------------------
# (d) R8 页面移除清理（EV-1c-d）
# ---------------------------------------------------------------------------

class TestR8PageRemoval:
    def test_page_functions_removed(self):
        src = _src("frontend/ui/tabs/system_settings.py")
        assert "def create_config_template_tab" not in src
        assert "def create_local_dir_endpoint_tab" not in src

    def test_layout_registrations_removed(self):
        src = _src("frontend/ui/layout.py")
        assert "create_local_dir_endpoint_tab" not in src
        assert "create_config_template_tab" not in src

    def test_enable_paths_entry_removed(self):
        from frontend.config.enable_paths import ENABLE_PATHS
        assert "local_dir_endpoint" not in ENABLE_PATHS

    def test_dead_branch_removed_in_frontend_main(self):
        src = _src("frontend/main.py")
        assert "local_dir_to_endpoint" not in src

    def test_config_templates_directory_untouched(self):
        """红线（EV-1d）：config_templates/ 目录=动态模板活体存储，目录必须仍存在
        （文件内容/数量为用户数据随实播增删，不做断言——2026-10-24 用户清理测试模板）。"""
        d = REPO / "config_templates"
        assert d.is_dir()


# ---------------------------------------------------------------------------
# (e) E4 talk 页渲染 smoke（EV-1i）
# ---------------------------------------------------------------------------

class TestTalkPageSmoke:
    def test_password_param_fixed(self):
        """P1：password_icon_visibility（NiceGUI 3.16 非法参数）清零。
        2026-10-25 精简：百度区块（原 2 处密码框）已随区块移除，全页无密码框。"""
        src = _src("frontend/ui/tabs/talk.py")
        assert "password_icon_visibility" not in src
        assert src.count("password_toggle_button=True") == 0

    def test_two_column_layout(self):
        """R1：参数网格全部两列（防回退三列挤掉聊天记录区）。
        2026-10-25 精简：谷歌/百度/faster_whisper/SenseVoice 四区块移除，8→4 处。"""
        src = _src("frontend/ui/tabs/talk.py")
        assert src.count("with ui.grid(columns=2):") == 4
        assert "with ui.grid(columns=3):" not in src

    def test_module_imports(self):
        """渲染 smoke：talk 模块可导入（签名漂移会在导入/渲染期整页失败）。"""
        from frontend.ui.tabs import talk  # noqa: F401
        assert callable(talk.create_talk_tab)


# ---------------------------------------------------------------------------
# 批次杂项守卫
# ---------------------------------------------------------------------------

class TestBatchMisc:
    def test_database_path_readonly(self):
        """R4：数据库路径输入框已移除，只读展示在位，三开关仍在。"""
        src = _src("frontend/ui/tabs/database_config.py")
        assert 'ui.input(label=\'数据库路径\'' not in src
        assert "数据库路径（系统内置，不可修改）" in src
        assert src.count("ui.switch('弹幕日志'") == 1
        assert src.count("ui.switch('入场日志'") == 1
        assert src.count("ui.switch('礼物日志'") == 1
        assert "gap:16px" in src  # 横向排列

    def test_web_captions_param_switch_removed(self):
        """R2：参数页「启用」开关移除（同键二级菜单唯一入口）。"""
        src = _src("frontend/ui/tabs/web_captions_printer.py")
        assert "ui.switch('启用'" not in src
        assert '("web_captions_printer", "enable")' not in src

    def test_hint_callout_no_hardcoded_tailwind(self):
        """DV-1d：classes() 调用参数中硬编码 Tailwind 调色类全站清零。"""
        import re
        ui_dir = REPO / "frontend" / "ui"
        offenders = []
        for py in ui_dir.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for m in re.finditer(r'''classes\(\s*['"]([^'"]*)['"]''', text):
                if any(tok in m.group(1) for tok in (
                        "bg-blue-", "bg-gray-", "bg-red-", "bg-green-",
                        "bg-yellow-", "bg-slate-", "text-gray-", "text-blue-")):
                    offenders.append(f"{py.name}:{m.group(1)}")
        assert offenders == []

    def test_thanks_labels_chinese(self):
        """R10：答谢页周期触发标签全中文（半中半英清零）。"""
        src = _src("frontend/ui/tabs/thanks.py")
        assert "label=f'触发{pt_key}'" not in src
        assert "触发周期（秒）" in src  # 二轮任务 10：单值周期
        assert src.count("['periodic_time', 'trigger_num_min', 'trigger_num_max']") == 3  # 旧键循环清零（注释提及不计）
        assert "触发次数最大值" in src

    def test_thanks_single_lowest_price_in_gift_section(self):
        """R10：双「最低答谢礼物价格」收敛——礼物区唯一入口且在礼物文案下方。"""
        src = _src("frontend/ui/tabs/thanks.py")
        assert src.count("ui.input(label='最低答谢礼物价格'") == 1  # 唯一入口（注释不计）
        gift_block = src[src.find("'礼物设置'"):src.find("'关注设置'")]
        assert "最低答谢礼物价格" in gift_block
        assert gift_block.find("'礼物文案'") < gift_block.find("最低答谢礼物价格")  # 文案下方

    def test_stage_button_class_toggle(self):
        """R9：训练阶段按钮两态类切换（style 累积叠加根因根治）。"""
        src = _src("frontend/ui/tabs/train_streamer.py")
        assert "stage-btn-selected" in src
        assert "stage-btn-unselected" in src
        assert "_render_stage_btn(_i)" in src  # 初始类渲染

    def test_data_analysis_buttons_flex_row(self):
        """R9/D-4：数据分析四按钮 flex 一行等宽。"""
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert "display:flex" in src
        assert src.count("flex:1 1 140px") == 4

    def test_idle_textarea_height_quadrupled(self):
        """R11：闲时任务三 textarea 高度四倍（min-height:16rem）。"""
        src = _src("frontend/ui/tabs/idle_time_task.py")
        assert src.count("input-style=") == 3 and src.count("min-height: 16rem") == 3

    def test_filter_queue_copy_replaced(self):
        """R7：队列提示文案替换（免责段删除+笔误修正）。"""
        src = _src("frontend/ui/tabs/filter_queue.py")
        assert "由于shi山原因" not in src
        assert "降序编号降序" not in src
        assert "统一列表按当前优先级降序编号，修改数值后刷新页面会重排" in src

    def test_trends_config_layout_and_delete(self):
        """R6/D-5/DV-1c：动态模板布局顺序+条目删除+去重+空态。"""
        src = _src("frontend/ui/tabs/system_settings.py")
        assert "with ui.grid(columns=2).style(\"width:100%;\")" in src
        assert "_delete_entry" in src
        assert "已清理" in src  # 存量重复清理
        assert "暂无人数段绑定" in src  # 空态提示


class TestRound2Fixes:
    """二轮验收修复守卫（2026-10-24：7 项任务）。"""

    def test_split_lines_normalization(self):
        """任务 10：auto_save split_lines 归一化+调用点计数。"""
        src = _src("frontend/utils/config_auto_save.py")
        assert "split_lines: bool = False" in src
        assert "value.split" in src and "if line.strip()" in src
        for rel, n in [("frontend/ui/tabs/thanks.py", 3), ("frontend/ui/tabs/idle_time_task.py", 3),
                       ("frontend/ui/tabs/trends_copywriting.py", 1)]:
            assert _src(rel).count("split_lines=True") == n

    def test_textarea_data_change_str_guard(self):
        """任务 10 根因：textarea_data_change 字符串原样返回（不按字符迭代）。"""
        from frontend.utils.common import textarea_data_change
        assert textarea_data_change("单字符串") == "单字符串"
        assert textarea_data_change(["a", "b"]) == "a" + chr(10) + "b" + chr(10)
        assert textarea_data_change(None) == ""

    def test_backend_copy_dual_shape(self):
        """后端消费点 str/list 双形态（G7 模式）。"""
        assert 'isinstance(copy_list, str)' in _src("utils/handlers/event_handler.py")
        assert 'isinstance(_copy_raw, str)' in _src("utils/task_scheduler.py")
        sched = _src("utils/task_scheduler.py")
        assert sched.count('isinstance(tmp, str)') == 1  # 闲时任务三列表加载

    def test_chat_log_buffer_wired(self):
        """任务 14：/callback TODO 空实现修复+talk 页轮询渲染。"""
        assert 'TODO: 实现聊天记录显示' not in _src("frontend/main.py")
        assert 'append_chat(payload)' in _src("frontend/main.py")
        talk_src = _src("frontend/ui/tabs/talk.py")
        assert 'from frontend.utils.chat_log_buffer import snapshot' in talk_src
        assert 'ui.timer(2.0, _refresh_chat_log)' in talk_src

    def test_chat_log_semantics_chat_only(self):
        """任务 14 + 2026-10-25 重构：聊天记录收聊天功能互动
        （voice=语音对话/chatbox=聊天框发送/comment=弹幕监听问答），
        点歌/积分等其余来源在 core 网关与 webui 白名单双层过滤；
        talk.show_chat_log 开关已退役（固定记录）。"""
        ws = _src("utils/web_server.py")
        assert ws.count('data_json["chat_source"] = "chatbox"') == 1  # 聊天框发送打标
        conv = _src("utils/conversation_manager.py")
        assert '"chat_source": "voice"' in conv  # 语音对话打标
        uh = _src("utils/handlers/utils_handler.py")
        assert 'chat_source not in ("voice", "chatbox", "comment")' in uh  # core 网关白名单
        assert 'return  # 点歌/积分等非聊天互动来源不进聊天记录' in uh
        # 开关退役：发送侧不再读 show_chat_log 配置（键已从 example 移除；
        # 注意方法名 webui_show_chat_log_callback 含子串，断言须锚定配置门卫）
        assert 'config.get("talk", "show_chat_log")' not in uh
        assert 'config.get("talk", "show_chat_log")' not in _src("utils/handlers/chat_handler.py")
        assert 'config.get("talk", "show_chat_log")' not in _src("utils/handlers/comment_handler.py")
        fm = _src("frontend/main.py")
        assert 'payload.get("chat_source") in ("voice", "chatbox", "comment")' in fm  # webui 白名单
        # 发送对：语音输入 question（talk_handle）+语音回复 answer（补发）
        chat = _src("utils/handlers/chat_handler.py")
        assert '"type": "语音对话"' in chat
        # 回复侧补发 ×2（2026-10-27 流式链路：全文回复 + 流式逐句回复各一处，语义同源）
        assert chat.count('content_type="answer"') == 2
        # 弹幕：输入 question+回复 answer 成对（弹幕监听默认源 comment 打标）
        cm = _src("utils/handlers/comment_handler.py")
        assert 'content_type="question"' in cm
        assert 'content_type="answer"' in cm
        assert '"弹幕信息"' in cm
        assert cm.count('_is_chatbox') >= 2  # 标记判定复用（输入+回复）
        assert 'dict(data, chat_source=_chat_src)' in cm  # 副本打标不污染原 data

    def test_trends_labels_renamed_and_chatgpt_removed(self):
        """任务 13：随机生成/生成间隔+chatgpt 选项彻底移除。"""
        src = _src("frontend/ui/tabs/trends_copywriting.py")
        assert "ui.switch('随机播放'" not in src and "label='文案播放间隔'" not in src
        assert "ui.switch('随机生成'" in src and "label='生成间隔'" in src
        assert "（已停用）" not in src  # 置灰方案被用户指令替换为彻底移除
        # 顺序：LLM类型 → 随机生成 → 生成间隔（同 grid 内先 select 后 column）
        _sq = chr(39)  # 单引号（label 值引号形态，避免引号嵌套）
        _llm_pos = src.find("label=" + _sq + "LLM类型" + _sq)
        _rand_pos = src.find("ui.switch(" + _sq + "随机生成" + _sq)
        _interval_pos = src.find("label=" + _sq + "生成间隔" + _sq)
        assert -1 < _llm_pos < _rand_pos < _interval_pos

    def test_read_comment_single_column(self):
        """任务 11：触发周期/次数纵向单列（原三列网格移除）。"""
        src = _src("frontend/ui/tabs/read_comment.py")
        assert "一列显示" in src
        # 单列顺序：周期 → 次数min → 次数max
        _sq = chr(39)
        _period_pos = src.find("label=" + _sq + "触发周期（秒）" + _sq)
        _min_pos = src.find("label=" + _sq + "触发次数最小值" + _sq)
        _max_pos = src.find("label=" + _sq + "触发次数最大值" + _sq)
        assert -1 < _period_pos < _min_pos < _max_pos

    def test_char_split_damage_repair(self):
        """三轮验收：存量损坏形态（每行单字符）重组，合法多行文案不受影响。
        损坏机制=修复前显示文本被 auto_save 原样存回（str 原样返回只防新损坏）。"""
        from frontend.utils.common import textarea_data_change as tdc
        nl = chr(10)
        # 用户截图两例（闲时任务/回复模板）
        assert tdc("当" + nl + "前" + nl + "时" + nl + "间" + nl + "是") == "当前时间是"
        assert tdc("回" + nl + "复" + nl + "{" + nl + "u" + nl + "s" + nl + "e" + nl + "r" + nl + "}") == "回复{user}"
        # 合法多行文案（含长行）原样
        assert tdc("第一条完整文案" + nl + "第二条完整文案") == "第一条完整文案" + nl + "第二条完整文案"
        # 单行/两行（不满足 >2 全单字符判据）原样
        assert tdc("单独一行") == "单独一行"
        assert tdc("a" + nl + "b") == "a" + nl + "b"
        # None/空串
        assert tdc(None) == "" == tdc("")

    def test_split_lines_persists_normalized_value(self):
        """三轮验收：落盘传归一化后的 value（原 bug 传 e.value 原始 str——
        内存 list 但 config.json 落盘文本，系统无法解析）。行为级：模拟
        on_change 验证 set_config_callback 收到 list。"""
        from frontend.utils import config_auto_save as cas
        captured = {}
        def fake_set_cb(*keys, value, _notify=True):
            captured["value"] = value
            return True
        class FakeTA:
            label = "文案"
            def on_value_change(self, cb):
                self._cb = cb
            def on(self, *a, **k):
                pass
        ta = FakeTA()
        cfg = {"idle_time_task": {"copywriting": {"copy": []}}}
        cas.auto_bind(ta, cfg, ("idle_time_task", "copywriting", "copy"),
                      fake_set_cb, tab_key="test", split_lines=True)
        nl = chr(10)
        ta._cb(type("V", (), {"value": f"第一行{nl}第二行{nl}"})())
        assert cfg["idle_time_task"]["copywriting"]["copy"] == ["第一行", "第二行"]  # 内存
        assert captured["value"] == ["第一行", "第二行"]  # 落盘同步归一化（原 bug 处）
        # 源级：set_config_callback 传 value（归一化后）而非 e.value
        src = _src("frontend/utils/config_auto_save.py")
        assert "set_config_callback(*keys, value=value)" in src
        assert "set_config_callback(*keys, value=e.value)" not in src

    def test_reply_template_split_lines(self):
        """三轮验收：回复模板绑定补 split_lines（common_config 漏网——二轮只改了
        thanks/idle/trends 三文件，回复模板在 common_config.py 未覆盖）。"""
        src = _src("frontend/ui/tabs/common_config.py")
        assert '("reply_template", "copywriting"), split_lines=True)' in src

class TestT10VoiceTalkChain:
    """T10 语音对话链接通守卫（2026-10-24）：采集→ASR→对话链中段补全。"""

    def test_asr_engine_exists_and_dispatch(self):
        """ASR 适配层：faster_whisper/sensevoice 分派+云端未接入明示。"""
        from utils.asr_engine import transcribe, _ENGINE_SINGLETON
        src = _src("utils/asr_engine.py")
        assert "WhisperModel" in src and "AutoModel" in src
        assert "本地离线链未接入" in src  # google/baidu 明示而非静默
        # 失败语义=空串（调用方静默跳过）
        assert transcribe("", None) == ""
        assert transcribe("nonexistent.wav", None) == ""

    def test_asr_aligns_project_models(self):
        """四轮用户澄清：ASR 对齐项目既有实现（apps/gpt-sovits/tools/asr/）——
        本地模型优先零下载+HF 镜像+need_lang 直选路由（zh/yue→FunASR，其余→whisper）。"""
        from utils.asr_engine import _local_fw_model_dir, _funasr_model_paths
        import os
        src = _src("utils/asr_engine.py")
        assert "HF_ENDPOINT" in src and "hf-mirror.com" in src  # 镜像（上游脚本同款）
        # 本地模型探测：faster-whisper-large-v3-turbo-ct2（项目资产，非配置的 small）
        fw = _local_fw_model_dir()
        assert fw and "large-v3-turbo" in os.path.basename(fw)
        # FunASR zh 路径：paraformer/vad/punc 全部解析到本地目录
        zh = _funasr_model_paths("zh")
        for k in ("model", "vad", "punc"):
            assert os.path.isdir(zh[k]), zh[k]
        assert "paraformer-large" in zh["model"]
        # 语言路由（2026-10-25 重构）：need_lang 一次定模型——zh/yue→FunASR，
        # 其余→whisper；"whisper 先转写再检测 zh 转发"的冗余双模型链已退役
        assert 'need_lang in ("zh", "yue")' in src
        assert '_transcribe_funasr(wav_path, need_lang)' in src
        assert 'detected == "zh"' not in src
        # 粤语：UniASR 无 VAD（funasr_asr.py 注释——带 VAD 会 shape 报错）
        yue = _funasr_model_paths("yue")
        assert yue["vad"] is None and "UniASR" in yue["model"]

    def test_record_audio_flag_mode(self):
        """录音改标志式（is_recording 控制，input_handler 单击模型对齐）。"""
        src = _src("utils/audio_processor.py")
        assert "while self.is_recording:" in src
        assert "RIGHT_SHIFT" not in src  # 上游按住 Shift 模型退役
        assert "last_recording_path" in src
        assert "frames_to_wav" in src  # 连续模式帧转 wav

    def test_stop_recording_wires_asr_to_talk(self):
        """main.py 停止录音 → ASR → conversation_manager.talk_handle 串接。"""
        src = _src("main.py")
        assert "from utils.asr_engine import transcribe" in src
        assert "self.conversation_manager.talk_handle(text)" in src

    def test_do_listen_filled(self):
        """连续对话循环填充：VAD→ASR→唤醒→对话链（空框架退役）。"""
        src = _src("utils/conversation_manager.py")
        assert "这里需要集成音频处理器的功能" not in src
        assert "ap.audio_listen(" in src
        assert "self.check_talk_awake(text)" in src
        assert "self.talk_handle(text)" in src

    def test_filter_four_switches_one_row(self):
        """任务 16：四开关同行并列。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        row_block = src[src.find("四开关同一行并列"):src.find("违禁词路径")]
        for label in ("违禁词过滤", "违禁语句丢弃", "用户名中的数字转中文", "弹幕表情过滤"):
            assert f"ui.switch('{label}'" in row_block

    def test_stage_button_text_white(self):
        """任务 6：selected 态 text-white 双保险。"""
        src = _src("frontend/ui/tabs/train_streamer.py")
        assert "stage-btn-selected text-white" in src  # 双保险
        assert "remove='stage-btn-selected stage-btn-unselected text-white'" in src


class TestRereadProductionChain:
    """reread 生产端接线守卫（TODOS 2026-10-22 音频批次节 #1）：弹幕词表→
    reread/reread_top_priority 生产→999 暂停-续播链路真实触发。"""

    def test_comment_handler_keyword_trigger(self):
        """comment_handle 弹幕链复读触发（2026-10-25 识别中枢批次上收）：
        词表匹配（高优→reread_top_priority / 普通→reread，str/list 双形态）
        移入识别中枢 KeywordHub._match_reread；弹幕链凭 rec.reread_type 分发，
        内联词表扫描不得回归。"""
        src = _src("utils/handlers/comment_handler.py")
        assert "if rec.reread_type:" in src
        assert "return self.reread_handle(data, type=rec.reread_type, hub_result=rec)" in src
        assert "_top_kws" not in src and "_rd_kws" not in src
        # 高优/普通词表匹配语义在中枢内保持
        hub_src = _src("utils/keyword_hub.py")
        assert '"reread_top_priority"' in hub_src
        assert 'reread_top_priority" if' in hub_src or "top_priority_keywords" in hub_src

    def test_reread_section_defaults(self):
        """settings _ensure_defaults：reread 节 defaults（此前 config 无此节，
        reread_handle 的 enable 恒 None→复读整体死链）。"""
        from frontend.config.settings import _ensure_defaults
        src = _src("frontend/config/settings.py")
        assert 'if "reread" not in cfg:' in src
        assert '"top_priority_keywords": []' in src
        # 生产消费语义：reread_handle enable 三参 get 安全形式（A6）
        ch_src = _src("utils/handlers/comment_handler.py")
        assert 'self.my_handle.config.get("reread", "enable")' in ch_src

    def test_filter_config_reread_card(self):
        """filter_config 复读触发卡：enable 开关+两词表 textarea（split_lines）。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        assert "复读触发" in src
        assert 'keys=("reread", "enable")' in src
        assert 'keys=("reread", "keywords"), split_lines=True' in src
        assert 'keys=("reread", "top_priority_keywords"), split_lines=True' in src

    def test_talk_three_buttons_wired(self):
        """talk 页三按钮 on_click 接线（发送/直接复读/直接复读-插队首）。"""
        src = _src("frontend/ui/tabs/talk.py")
        assert 'ui.button(\'发送\', on_click=lambda: _send_chat("comment")' in src
        assert 'ui.button(\'直接复读\', on_click=lambda: _send_chat("reread")' in src
        assert 'ui.button(\'直接复读-插队首\', on_click=lambda: _send_chat("reread_top_priority")' in src

    def test_main_send_chat_message(self):
        """main.py send_chat_message：POST core /send（chatbox 打标 payload）。"""
        src = _src("main.py")
        assert "def send_chat_message(self, msg_type: str, content: str) -> bool:" in src
        assert "{main_api_port}/send" in src
        assert '"chat_source": "chatbox"' in src
        # layout 透传（app 存在性守卫）
        layout_src = _src("frontend/ui/layout.py")
        assert "chat_send_callback=" in layout_src
        assert "hasattr(self.app, \"send_chat_message\")" in layout_src


class TestD15RetiredKeysNotice:
    """D-15 首启退役键面告知守卫（基础功能 2026-10-22 节 #9）。"""

    def test_retired_keys_listed(self):
        """退役键面清单：四键面全覆盖（metahuman_stream/game/priority_mapping/lang_filter）。"""
        src = _src("frontend/main.py")
        assert "_RETIRED_CONFIG_KEYS" in src
        assert '("metahuman_stream",)' in src
        assert '("game",)' in src
        assert '("audio_player", "priority_mapping")' in src
        assert '("filter", "lang_filter")' in src

    def test_notice_once_flag(self):
        """一次性告知：标志键防重复+notify 失败不设标志。

        2026-10-24：set 须关键字传参（Settings.set(*keys, value) 签名）——
        原位置传参 TypeError 被吞→标志永不落盘→每次启动重复告知。
        """
        src = _src("frontend/main.py")
        assert "retired_keys_notice_shown" in src
        assert "def _notify_retired_config_keys(self):" in src
        assert 'self.config.set("webui", "retired_keys_notice_shown", value=True)' in src
        assert "ui.timer(2.5, self._notify_retired_config_keys, once=True)" in src


class TestV2FallbackSingleChannel:
    """backend=api 兜底数据链收敛守卫（TODOS 基础功能节 #10 评估落地）。"""

    def test_dead_branches_removed(self):
        """六死分支退役：gradio_0322/api/api_0322/api_0706/v2_api_0821/webtts。"""
        src = _src("utils/audio_handle/my_tts.py")
        api_src = src[src.find("async def gpt_sovits_api"):]
        for dead in ('gradio_0322', '"api_0322"', '"api_0706"', '"v2_api_0821"', '"webtts"',
                     'file_to_data_url', 'import websockets'):
            assert dead not in api_src, f"死分支残留：{dead}"

    def test_fallback_single_channel_params_sourced(self):
        """兜底单通道：v2 请求体从 params 白名单+运行时值装配（与 inproc 同源）。"""
        src = _src("utils/audio_handle/my_tts.py")
        api_src = src[src.find("async def gpt_sovits_api"):]
        assert 'urljoin(data.get("api_ip_port", ""), \'/tts\')' in api_src
        assert '_params.get("text_lang") or data.get("language")' in api_src
        assert '"speed_factor": _speed' in api_src
        # 测试链 config 直读兜底（data 无 params 键时）
        assert 'self.config.get("gpt_sovits") or {}' in api_src

    def test_synthesis_manager_dead_keys_removed(self):
        """弹幕链 data 构造死键清理（透传键无消费端）。"""
        src = _src("utils/audio/synthesis_manager.py")
        assert '"api_0322": _gdata.get("api_0322", {})' not in src
        assert '"api_0706": _gdata.get("api_0706", {})' not in src
        assert '"v2_api_0821": _gdata.get("v2_api_0821", {})' not in src
        assert '"webtts": _gdata.get("webtts", {})' not in src
        assert '"gradio_ip_port": _gdata.get("gradio_ip_port", "")' not in src
        # 活键保留
        assert '"api_ip_port": _gdata.get("api_ip_port", "")' in src
        assert '"params": _params' in src


class TestTask12SplitGroupTrace:
    """任务 12 根修守卫（TODOS 2026-10-22 音频批次节）：长回复切分段组绑定+
    丢段可见面恢复（UI-16 R7 免责段删除后失效模式失去用户可见面的欠账）。"""

    def test_split_group_injected(self):
        """切分循环注入组绑定（id/seq/total）+容量预检。"""
        src = _src("utils/audio/synthesis_manager.py")
        assert 'message_copy["split_group"] = {"id": _group_id, "seq": _seq, "total": _total}' in src
        assert "[长回复切分]" in src  # 容量预检 warning
        assert "message_queue_max_len" in src
        # 音频条目组绑定透传（put_to_queue）
        assert 'data_json["split_group"] = message["split_group"]' in src

    def test_drop_points_annotated(self):
        """队列取代/丢弃点组附注+丢段 warning 升级。"""
        src = _src("utils/audio/queue_manager.py")
        assert "def _split_group_note(data_json):" in src
        assert '_split_group_note(data_json)' in src
        # message_queue 丢段：info → warning
        assert 'logger.warning(f"message_queue 已满，数据丢弃' in src
        # 音频规则 4 舍弃：info → warning
        assert 'logger.warning(f"voice_tmp_path_queue 已满，音频丢弃' in src
