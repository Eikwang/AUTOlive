# -*- coding: UTF-8 -*-
"""
点歌模式标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
- 歌曲翻唱模块（/autoplan 2026-10-14 C-3）：折叠分组承载（状态/参数/成品），
  队列状态经 main 侧 /cover/status 窄端点轮询（写者矩阵：webui 只读）
"""
import os
import threading

import requests
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change

# main 侧窄端点轮询（nicegui3 教训：requests trust_env=False 禁系统代理+线程池解耦）
_session = requests.Session()
_session.trust_env = False
_session.proxies = {"http": None, "https": None}


def _main_base(config) -> str:
    return f"http://127.0.0.1:{int(get_nested_value(config, 'api_port') or 8000)}"


def create_choose_song_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建点歌模式标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="choose_song")

    # 点歌模式
    with ui.card().style(card_css):
        ui.label('点歌模式')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.textarea(label='点歌触发命令', value=textarea_data_change(get_nested_value(config, "choose_song", "start_cmd")), placeholder='点歌触发命令，换行分隔').style("width:100%;"), keys=("choose_song", "start_cmd"))
            _auto_save(ui.textarea(label='取消点歌命令', value=textarea_data_change(get_nested_value(config, "choose_song", "stop_cmd")), placeholder='停止点歌命令，换行分隔').style("width:100%;"), keys=("choose_song", "stop_cmd"))
            _auto_save(ui.textarea(label='随机点歌命令', value=textarea_data_change(get_nested_value(config, "choose_song", "random_cmd")), placeholder='随机点歌命令，换行分隔').style("width:100%;"), keys=("choose_song", "random_cmd"))
        with ui.grid(columns=3):
            _auto_save(ui.input(label='歌曲路径', value=get_nested_value(config, "choose_song", "song_path"), placeholder='歌曲音频存放的路径').style("width:100%;"), keys=("choose_song", "song_path"))
            _auto_save(ui.input(label='匹配失败文案', value=get_nested_value(config, "choose_song", "match_fail_copy"), placeholder='匹配失败返回的音频文案').style("width:100%;"), keys=("choose_song", "match_fail_copy"))
            _auto_save(ui.input(label='匹配最低相似度', value=get_nested_value(config, "choose_song", "similarity"), placeholder='最低音频匹配相似度').style("width:100%;"), keys=("choose_song", "similarity"))

    # ---------- 歌曲翻唱（C-3：折叠分组防卡片墙，Design P4/P7） ----------
    with ui.card().style(card_css):
        with ui.expansion("歌曲翻唱（弹幕点歌自动翻唱→自动插播）", value=False).style("width:100%"):
            # ① 队列状态卡（/cover/status 轮询；to_thread 解耦事件循环——nicegui3 教训）
            status_lab = ui.label("队列状态查询中…").style("font-size:12px;color:var(--text-secondary)")

            def _safe_get(base):
                try:
                    r = _session.get(f"{base}/cover/status", timeout=4)
                    return r.json()
                except Exception as e:
                    return {"available": False, "reason": str(e)[:80]}

            async def _poll_task():
                import asyncio as _aio
                data = await _aio.to_thread(lambda: _safe_get(_main_base(config)))
                if not data.get("available"):
                    status_lab.set_text(f"○ 翻唱队列不可用：{data.get('reason', 'main 未运行')}")
                    return
                q = data.get("queue") or []
                cur = data.get("current")
                if cur:
                    status_lab.set_text(f"▶ 正在翻唱《{cur['song']}》（{cur.get('elapsed_s', 0):.0f}s）"
                                        f" · 排队 {len(q)} 个")
                elif q:
                    status_lab.set_text(f"○ 排队中：{len(q)} 个（下一首《{q[0]['song']}》）")
                else:
                    status_lab.set_text("○ 翻唱队列空闲（弹幕点歌自动入队）")

            ui.timer(5.0, _poll_task)

            # ② 翻唱参数（档案选择写 cover_profile_id、音高写 cover_pitch——
            # cover_worker 派发 -p 直传八度，音色变更下次点歌生效）
            with ui.grid(columns=2).style("width:100%;margin-top:8px"):
                from utils.voice_profiles import load_profiles
                profs = load_profiles()
                opts = {pid: (p.get("name") or pid) for pid, p in profs.items() if p.get("rvc_model")}
                if not opts:
                    opts = {"default": "（默认：models/rvc/ 第一个模型）"}
                # NiceGUI select 的 value 必须是 options 的键：config 未存过档案 id 时
                # 旧代码回落 "default"——有档案后 options 不含该键，页面加载即
                # "Invalid value: default"。归一到首个可用键（空档案时即 "default"）。
                saved_pid = get_nested_value(config, "choose_song", "cover_profile_id")
                if saved_pid not in opts:
                    saved_pid = next(iter(opts))
                _auto_save(ui.select(label='翻唱音色档案（RVC）',
                                     value=saved_pid,
                                     options=opts).style("width:100%;"),
                           keys=("choose_song", "cover_profile_id"))
                with ui.column().style("width:100%"):
                    pitch_val = int(get_nested_value(config, "choose_song", "cover_pitch") or 0)
                    plab = ui.label(f"翻唱音高（八度）: {pitch_val:+d}").style("font-size:12px")
                    ui.slider(min=-12, max=12, step=1, value=pitch_val,
                              on_change=lambda e: (plab.set_text(f"翻唱音高（八度）: {int(e.value):+d}"),
                                                   set_config_callback("choose_song", "cover_pitch", int(e.value))),
                              ).props("label-always").style("width:100%")
            ui.label("音高单位=八度（男转女 +1）；AI 翻唱内部换算半音").style(
                "font-size:11px;color:var(--text-secondary)")

            # ③ 成品列表（song/covers/）+ EMPTY 引导
            covers_dir = os.path.join(
                get_nested_value(config, "choose_song", "song_path") or "song", "covers")
            covers = sorted((f for f in (os.listdir(covers_dir) if os.path.isdir(covers_dir) else [])
                             if f.endswith(".wav")), reverse=True)
            if not covers:
                ui.label("暂无翻唱成品——弹幕点歌命中后将自动翻唱，成品在此列出").style(
                    "font-size:12px;color:var(--text-secondary)")
            else:
                with ui.column().style("width:100%;gap:2px;max-height:180px;overflow:auto"):
                    for fn in covers[:20]:
                        ui.label(f"🎵 {fn}").style("font-size:12px")
