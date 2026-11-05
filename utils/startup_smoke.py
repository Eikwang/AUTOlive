# -*- coding: utf-8 -*-
"""
启动期冒烟自检（T4 前提表→运行时冒烟断言，/autoplan main-20261031-201249 终门批准批次）。

背景（TODOS 2026-10-20 #3 到期义务，native CEO F1/F3/F5）：26 条流程图前提中
"代码在位≠实际生效"类问题（A1-A4 实测：违禁词文件缺失过滤静默失效、变声链配置
残留每弹幕刷错）依赖启动期可验证检查复利化——每次架构审查的"断链考古"前移为
启动期一次 warn。

定位：**只 warn 不阻断**（缺失项不阻止系统启动——功能降级可见即可）；一次性
收集（启动线程延迟调用一次，config mtime 惰性重读已由各消费点自行承担）。

检查项（当前有效前提的运行期子集，静态面见 tests/test_premise_smoke.py）：
1. badwords 词表可达（filter.enable+badwords.enable 时）：缺失=过滤静默失效（A4）
2. 点歌曲目录存在（choose_song.enable 时）：缺失=点歌匹配恒失败
3. 助播/主侧词库桥（assistant_anchor.enable 时）：config 内嵌 data 与 file_path
   回退文件均空/缺失=问答恒 miss
4. 按键映射串口引用完整（key_mapping.enable 时）：config[].serial_name 必须在
   serial.config[].serial_name 中（断链=触发无动作）
5. 优先级映射覆盖（常量生产 type 集合 ⊆ 当前 config priority_mapping）：存量
   config 缺新键=该类型按未识别排队（满则丢弃，trends_copywriting 型断链例证）
"""
import os

from utils.my_log import logger


def _cfg_get(config, *keys, default=None):
    """双形态 config 读取（webui dict / 主进程 Config——对齐 autolive_paths.config_get）。"""
    if config is None:
        return default
    if isinstance(config, dict):
        node = config
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k)
        return default if node is None else node
    try:
        v = config.get(*keys)
        return default if v is None else v
    except Exception:
        return default


def _path_exists(p):
    return bool(p) and os.path.isfile(str(p))


# 入队生产 type 常量清单（与 tests/test_premise_smoke.py 的静态断言同一份口径——
# 新产线上线两处同步；file:line 认定见测试内注释）
KNOWN_PRODUCER_TYPES = frozenset({
    "comment", "talk", "gift", "entrance", "follow",
    "read_comment", "reread", "reread_top_priority",
    "schedule", "idle_time_task", "trends_copywriting",
    "integral", "key_mapping", "song",
    "local_qa_text", "local_qa_audio",
    "assistant_anchor_text", "assistant_anchor_audio",
})


def check_badwords_files(config):
    """违禁词表可达性（filter.enable+badwords.enable 双门控时检查）。"""
    warns = []
    try:
        if not _cfg_get(config, "filter", "enable"):
            return warns
        bw = _cfg_get(config, "filter", "badwords") or {}
        if isinstance(bw, dict) and not bw.get("enable", True):
            return warns
        for key, label in (("path", "违禁词表"), ("bad_pinyin_path", "违禁拼音表")):
            p = bw.get(key) if isinstance(bw, dict) else None
            if p and not _path_exists(p):
                warns.append(
                    f"违禁词过滤将静默失效：{label}文件不存在 {p}（filter.badwords.{key}）——"
                    f"缺失期间违禁内容按原样放行（A4 实测例证）；请补文件或关闭 badwords.enable")
    except Exception:
        pass  # 冒烟失败不阻断启动
    return warns


def check_song_audio_root(config):
    """点歌曲目录（choose_song.enable 时）。"""
    warns = []
    try:
        if not _cfg_get(config, "choose_song", "enable"):
            return warns
        from utils.autolive_paths import song_audio_root
        root = song_audio_root(config)
        if not os.path.isdir(root):
            warns.append(
                f"点歌模式已开启但曲源目录不存在：{root}——匹配成功的点歌将无音频可播"
                f"（仅应答文案）；请创建目录并放入歌曲文件")
    except Exception:
        pass
    return warns


def check_assistant_kb_bridge(config):
    """助播/主侧词库桥（config 内嵌 data 或 file_path 回退文件至少一路可用）。"""
    warns = []
    try:
        if not _cfg_get(config, "local_qa", "enable"):
            return warns
        text = _cfg_get(config, "local_qa", "text") or {}
        if not isinstance(text, dict) or not text.get("enable", True):
            return warns
        data = text.get("data")
        fp = text.get("file_path")
        has_inline = isinstance(data, list) and any(str(x).strip() for x in data)
        has_file = _path_exists(fp)
        if not has_inline and not has_file:
            warns.append(
                "本地问答文本词库为空（local_qa.text.data 内嵌行与 file_path 回退文件均无"
                "或文件缺失）——问答将恒 miss（LLM 失败托底同空）；请在 WebUI 本地问答页"
                "维护词库或检查 file_path 路径")
    except Exception:
        pass
    return warns


def check_serial_refs(config):
    """按键映射的串口引用完整性（key_mapping.config[].serial_name ⊆ serial.config[]）。"""
    warns = []
    try:
        if not _cfg_get(config, "key_mapping", "enable"):
            return warns
        km = _cfg_get(config, "key_mapping", "config") or []
        serials = {c.get("serial_name") for c in (_cfg_get(config, "serial", "config") or [])
                   if isinstance(c, dict)}
        for i, group in enumerate(km):
            if not isinstance(group, dict):
                continue
            name = group.get("serial_name")
            if name and name not in serials:
                warns.append(
                    f"按键映射组 {i}（关键词：{str(group.get('keywords'))[:20]}…）引用的串口"
                    f"「{name}」不在 serial.config 中——触发时发送将被跳过；请核对串口配置页")
    except Exception:
        pass
    return warns


def check_priority_mapping_coverage(config):
    """优先级映射覆盖（生产 type 常量清单 ⊆ 当前 config priority_mapping）。"""
    warns = []
    try:
        pm = _cfg_get(config, "filter", "priority_mapping") or {}
        if isinstance(pm, dict):
            missing = sorted(KNOWN_PRODUCER_TYPES - set(pm.keys()))
            if missing:
                warns.append(
                    f"filter.priority_mapping 缺键：{missing}——这些类型的消息将按未识别排队"
                    f"（队尾+满则丢弃，trends_copywriting 断链例证）；请在优先级页补配或"
                    f"对照 config.example.json")
    except Exception:
        pass
    return warns


def check_choreography_materials(config):
    """编排素材/触发链自检（2026-11-04 erro3 批次 T6/R7/E5）——曾无任何启动期
    校验：规则引用段不在素材 meta（60s 兜底卡序列）、song 触发挂点断链，全靠
    实播踩坑暴露。三项可校验断言（E5，连通性不做静态证明只查装配面）：
    ①编排器注册面+worker（get_choreographer/worker_alive——enable 时）
    ②启用规则引用段在素材 meta 且 duration_s>0（缺=60s 兜底风险）
    ③触发词/引用段名合法性（触发词低于最小长度的存量告知，F-6）。
    enable=false/零启用规则=零输出（显式态）。"""
    warns = []
    try:
        ch = _cfg_get(config, "choreography") or {}
        if not ch.get("enable"):
            return warns
        rules = [r for r in (ch.get("rules") or []) if isinstance(r, dict)]
        if not rules:
            return warns
        # ① 注册面+就绪面（E5-①②：编排器装配与 worker 线程）
        try:
            from utils.orchestration import get_choreographer
            ch_obj = get_choreographer()
            if ch_obj is None:
                warns.append("[编排自检] 编排器未装配（get_choreographer=None）——"
                             "主进程启动链编排模块缺失，规则将不会触发")
            elif not ch_obj.worker_alive():
                warns.append("[编排自检] 编排 worker 线程未运行——检查启动日志"
                             "[编排] 三段式告警")
        except Exception:
            pass
        # ② 启用规则引用段 ⊆ 素材 meta 且 duration_s>0（E3：0.0 病态值同判缺）
        try:
            from utils.edtalk_realtime.materials import get_material_meta
            referenced = []
            for rule in rules:
                seq = rule.get("sequence") or {}
                for step in ("enter", "main", "exit"):
                    seg = str((seq.get(step) or {}).get("segment") or "")
                    if seg:
                        referenced.append((rule.get("rule_id") or "?", step, seg))
                for tier in (rule.get("emotion_tiers") or []):
                    for step, seg in ((tier.get("segment_overrides") or {}).items()):
                        if seg:
                            referenced.append((rule.get("rule_id") or "?", f"tier:{step}", str(seg)))
            missing = []
            for rid, step, seg in referenced:
                meta = get_material_meta(seg)
                if float(meta.get("duration_s") or 0) <= 0:
                    missing.append(f"{rid}:{step}→{seg}")
            if missing:
                warns.append(
                    f"[编排自检] {len(missing)} 个规则引用段在素材清单缺失或缺时长"
                    f"（{'; '.join(missing[:8])}{'…' if len(missing) > 8 else ''}）——"
                    f"触发将按 60s 兜底推进（画面卡步）；请在动作编排页核对这些段名，"
                    f"或在素材管理重新处理对应素材")
        except Exception:
            pass
        # ③ 触发词存量告知（F-6：低于最小长度的触发词在点歌意图收敛后静默失效）
        # ——start_cmd 实存形态=换行分隔字符串（曾逐字符误报），先归一
        try:
            raw_tw = _cfg_get(config, "choose_song", "start_cmd") or []
            if isinstance(raw_tw, str):
                raw_tw = raw_tw.replace("；", "\n").replace(";", "\n") \
                               .replace("，", "\n").replace(",", "\n").replace("、", "\n") \
                               .split("\n")
            tws = [str(t).strip() for t in raw_tw if str(t).strip()]
            short = [t for t in tws if len(t) < 2]
            if short:
                warns.append(f"[编排自检] 点歌触发词低于 2 字：{short}——意图收敛后"
                             f"短触发词不再可靠（误判敞口），建议在点歌页改为 ≥2 字")
        except Exception:
            pass
    except Exception:
        pass
    return warns


def run_startup_smoke(config):
    """运行全部冒烟检查，warn 一次并返回 warns（供测试断言与调用方日志）。"""
    warns = []
    for check in (check_badwords_files, check_song_audio_root,
                  check_assistant_kb_bridge, check_serial_refs,
                  check_priority_mapping_coverage, check_choreography_materials):
        try:
            warns.extend(check(config) or [])
        except Exception:
            pass  # 单项失败不影响其余检查
    for w in warns:
        logger.warning(f"[启动冒烟] {w}")
    if warns:
        logger.warning(f"[启动冒烟] 共 {len(warns)} 项降级风险——以上为一次性提示，"
                       f"修复后重启复检")
    else:
        logger.info("[启动冒烟] 全部通过（词表/曲源/词库/串口引用/优先级覆盖/编排）")
    return warns
