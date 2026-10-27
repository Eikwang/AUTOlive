# -*- coding: utf-8 -*-
"""数字人素材管理后端（动作编排二级页数据面；沿 danmaku_api/orchestration.api 模式）。

职责：把"添加/删除动作素材"变成系统内一键流程——
  添加：原始视频 → 暂存目录（original_videos/）→ runtime312 跑 crop_head_video.py
        （预处理步骤10，产四件套 orifra/count/metadata/facemark）→ 落盘 character_dir
        → 服务运行中则 POST /segment/add 热上线（未运行则下次启动自动扫描发现）。
  删除：引用保护（编排规则步骤段/情绪段覆盖/默认池/显式段表）+ 服务侧保护
        （当前渲染段/播放调度）→ /segment/remove 热卸载 → 删盘上四件套。

写者矩阵：character_dir 下段目录的唯一写者=本模块（删除）与预处理产出（添加）；
EDTalk 服务只做内存注册/卸载（/segment/add、/segment/remove），不碰盘。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

from utils.my_log import logger

# 段名白名单（=orifra/ 目录名）：拒绝路径分隔符/非法字符（目录名注入防护）
_SEG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".flv", ".webm", ".wmv")

# 预处理四件套（+可选第五件 gaze_data）
_PIECE_DIRS = ("orifra", "count")
_PIECE_FILES = ("metadata", "facemark")

# 素材类型三分类（R-ceo3：上场/主线/离场）
_VALID_MATERIAL_TYPES = ("enter", "main", "exit")
# 素材元数据 sidecar（R-eng2：主项目侧 data/，main 单写者；红线=不写 edtalk/ 四件套格式）
_META_FILENAME = "edtalk_materials_meta.json"
_META_LOCK = threading.Lock()
# sidecar 字段缺省（R-ceo9 几何契约：固定默认值，UI 后置；F-M3 单一来源=本文件）
_META_DEFAULTS = {
    "type": "main",        # enter|main|exit（X3 迁移默认=主线）
    "lip_flag": True,      # 口型推理（R-ceo2② 存量迁移素材视为 on）
    "duration_s": None,    # total_frames/target_fps（X2：规则引擎播放时长来源）
    "fps": 25.0,           # 渲染目标帧率（edtalk_realtime.target_fps，F-L2① sidecar 另存）
    "background": None,    # 背景画布图路径（uc3a/R-dsg11；None=画布默认背景）
    "head_position": [0.5, 0.42],  # 头部中心在画布的归一化位置（R-ceo9 几何契约）
    "head_scale": 0.5,     # 头部占画布短边比例（R-ceo9 几何契约）
    "composite": True,     # 是否合成背景画布（False=默认背景直出，非黑）
    "closeup": False,      # 特写标记（X1：crop 覆盖全帧两轴，F-L1 覆盖判据）
    "preview": None,       # 首帧+背景合成缩略图路径（R-dsg11 D2-6）
    "conflict": False,     # 迁移跨类引用冲突标记（R-ceo3/R-dsg5）
    "server_background": None,  # A 方案：character_dir/backgrounds/ 路径（R-eng2 F-H3）
    "canvas_w": 1280,      # 会话画布（R-ceo9：单一来源=AUTOlive 配置键，发布时快照）
    "canvas_h": 720,
}


class MaterialError(Exception):
    """素材操作语义失败（problem/cause/fix 三段式消息；API 层映射 4xx）。"""


# ----------------------------------------------------------------------
# 素材元数据 sidecar（T1/R-eng2：data/edtalk_materials_meta.json）
# ----------------------------------------------------------------------

def _meta_path() -> str:
    from frontend.config.paths import get_base_path
    return os.path.join(str(get_base_path()), "data", _META_FILENAME)


def _load_meta() -> dict:
    """读 sidecar（损坏降级空表+告警，不阻塞规则链——Section 2 裁决）。"""
    path = _meta_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.error(f"[materials] 素材元数据损坏（降级空清单语义）：{e}; "
                     "problem=素材元数据文件损坏；cause=JSON 解析失败；"
                     "fix=删除该文件后重新添加素材（data/edtalk_materials_meta.json）")
        return {}


def _save_meta(meta: dict) -> None:
    """原子写 sidecar（沿 orchestration.api 单写者先例：tmp+os.replace）。"""
    path = _meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_material_meta(segment: str) -> dict:
    """单素材元数据（缺省字段回填 _META_DEFAULTS；未登记素材=全默认）。"""
    with _META_LOCK:
        raw = (_load_meta().get(segment) or {})
    out = dict(_META_DEFAULTS)
    for k in _META_DEFAULTS:
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    return out


def set_material_meta(segment: str, **fields) -> dict:
    """更新单素材元数据（改类型/口型重处理/背景换绑共用入口；R-dsg3）。

    白名单字段写入；未知字段拒绝（枚举校验：type 值域）。返回更新后全量。
    """
    unknown = set(fields) - set(_META_DEFAULTS)
    if unknown:
        raise MaterialError(
            f"problem=未知素材元数据字段；cause={sorted(unknown)}；"
            f"fix=仅允许 {sorted(_META_DEFAULTS)}")
    if "type" in fields and fields["type"] not in _VALID_MATERIAL_TYPES:
        raise MaterialError(
            f"problem=素材类型值域外；cause={fields['type']!r}；"
            f"fix=仅允许 {'/'.join(_VALID_MATERIAL_TYPES)}")
    with _META_LOCK:
        meta = _load_meta()
        entry = meta.get(segment) or {}
        entry.update({k: v for k, v in fields.items() if v is not None or k in ("background",)})
        meta[segment] = entry
        _save_meta(meta)
    return get_material_meta(segment)


def _meta_duration_s(total_frames, fps: float):
    """帧数→秒（X2：规则引擎播放时长来源；帧数缺失返回 None=时长未知）。"""
    try:
        if total_frames is None or fps <= 0:
            return None
        return round(float(total_frames) / float(fps), 3)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# 路径解析（与服务端 cwd 约定一致：character_dir 相对 edtalk 运行副本根）
# ----------------------------------------------------------------------

def edtalk_root() -> str:
    from frontend.config.paths import get_base_path
    return os.path.join(str(get_base_path()), "edtalk")


def resolve_character_dir(config: dict) -> str:
    ed_cfg = (config or {}).get("edtalk_realtime", {}) or {}
    return os.path.join(edtalk_root(), str(ed_cfg.get("character_dir", "wy")))


def staging_dir(segment: str) -> str:
    from frontend.config.paths import get_base_path
    return os.path.join(str(get_base_path()), "data", "edtalk_materials", segment)


def _edtalk_api(config) -> str:
    from utils.edtalk_realtime.runtime import resolve_api_base
    return resolve_api_base(config)


def _auth_headers(config: dict) -> dict:
    token = str(((config or {}).get("edtalk_realtime", {}) or {}).get("api_token", "") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def service_status(config, timeout: float = 2.0):
    """EDTalk /status 快照；服务未运行返回 None（不打日志刷屏）。"""
    try:
        import requests
        resp = requests.get(f"{_edtalk_api(config)}/status",
                            headers=_auth_headers(config), timeout=timeout)
        if resp.status_code == 200:
            return resp.json() or {}
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------
# 引用保护（删除前置校验）
# ----------------------------------------------------------------------

def collect_references(config: dict) -> dict:
    """收集 config.json 中对段名的全部引用（删除保护依据）。

    返回 {segment: [引用位置描述, ...]}——仅统计持久层（config.json），
    运行态引用（当前渲染段/调度序列）由调用方叠加 service_status。
    """
    refs: dict = {}
    try:
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    def _ref(seg, where):
        if seg:
            refs.setdefault(str(seg), []).append(where)

    ch = cfg.get("choreography") or {}
    for seg in (ch.get("default_segments") or []):
        _ref(seg, "编排默认画面池")
    for rule in (ch.get("rules") or []):
        rid = rule.get("rule_id") or "?"
        seq = rule.get("sequence") or {}
        for step_name in ("enter", "main", "exit"):
            _ref((seq.get(step_name) or {}).get("segment"),
                 f"编排规则 {rid}·{step_name}")
        for tier in (rule.get("emotion_tiers") or []):
            for s in ((tier.get("segment_overrides") or {}).values()):
                _ref(s, f"编排规则 {rid}·情绪分级覆盖")

    ed_segments = ((cfg.get("edtalk_realtime") or {}).get("segments")) or "auto"
    if ed_segments and ed_segments != "auto":
        for seg in str(ed_segments).split():
            _ref(seg, "画面设置·段播放调度表")
    return refs


def _deletion_blockers(config: dict, segment: str) -> list:
    """删除阻断原因清单（空=可删）。持久层引用 + 运行态（当前段/调度）。"""
    blockers = [f"被 {w} 引用" for w in collect_references(config).get(segment, [])]
    st = service_status(config)
    if st:
        if st.get("current_segment") == segment:
            blockers.append("正在渲染中（当前段）")
        seq = ((st.get("segment_sequence") or {}).get("sequence")) or []
        if segment in seq:
            blockers.append("在播放调度序列中（先更新段播放调度表/默认画面池）")
        if st.get("pending_switch") == segment:
            blockers.append("存在待执行的边界切换")
    return blockers


# ----------------------------------------------------------------------
# 素材清单（管理页列表）
# ----------------------------------------------------------------------

def migrate_material_types(config: dict, cfg_override: dict = None) -> dict:
    """X3 存量素材类型定型（R-ceo3/native F4：按既有规则步骤引用先行定型）。

    cfg_override：规则引用扫描源（生产=None→读 config.json，沿
    collect_references 单写者惯例；测试/调用方可注入）。幂等：sidecar 标记
    _migrated=true 后跳过。规则：
      - enter 步引用→enter、exit 步引用→exit、main 步引用→main；
      - 跨类引用（同段被多类步骤引用）→默认 main+conflict 标记（人工裁决入口=
        R-dsg3 改类型）；跨类引用同时计入 _deletion_blockers 提示；
      - 未被任何规则引用→main（R-eng7：default_segments 引用不改变类型，
        仅入删除保护）。
    返回迁移报告 {typed, conflicts, conflict_segments}（R-dsg5 横幅数据源）。
    """
    with _META_LOCK:
        meta = _load_meta()
        char_dir = resolve_character_dir(config)
        # 迁移标记按 character_dir 键控（隔离缺口教训：测试 tmp 角色目录与真实
        # 目录共用 sidecar 时，全局标记会跳过真实素材的迁移）
        migrate_key = f"_migrated:{char_dir}"
        if meta.get(migrate_key):
            return {"typed": 0, "conflicts": 0, "conflict_segments": [],
                    "skipped": True}
        orifra_base = os.path.join(char_dir, "orifra")
        on_disk = sorted(
            d for d in (os.listdir(orifra_base) if os.path.isdir(orifra_base) else [])
            if os.path.isdir(os.path.join(orifra_base, d)))

        # 段→引用类型集合（含 tiers 覆盖——native F4/R-eng7 全引用面）
        ref_types: dict = {}
        cfg = cfg_override
        if cfg is None:
            try:
                with open("config.json", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
        ch = cfg.get("choreography") or {}
        for seg in (ch.get("default_segments") or []):
            ref_types.setdefault(str(seg), set())  # 默认池引用不入类型（R-eng7）
        for rule in (ch.get("rules") or []):
            rid = rule.get("rule_id") or "?"
            seq = rule.get("sequence") or {}
            for step_name in ("enter", "main", "exit"):
                seg = (seq.get(step_name) or {}).get("segment")
                if seg:
                    ref_types.setdefault(str(seg), set()).add(step_name)
            for tier in (rule.get("emotion_tiers") or []):
                for s in ((tier.get("segment_overrides") or {}).values()):
                    if s:
                        ref_types.setdefault(str(s), set()).add(
                            f"tier:{rid}")  # 覆盖档引用=中性（不定型不冲突）

        typed = 0
        conflicts = []
        for seg in on_disk:
            if seg in meta:
                continue
            types = ref_types.get(seg) or set()
            step_types = types & set(_VALID_MATERIAL_TYPES)
            if len(step_types) == 1:
                mtype = step_types.pop()
            else:
                mtype = "main"
                if len(step_types) > 1:
                    conflicts.append(seg)
            meta[seg] = dict(_META_DEFAULTS)
            meta[seg]["type"] = mtype
            meta[seg]["conflict"] = seg in conflicts
            # F-2 存量特写回溯徽标（R-dsg2）：迁移时对完整链素材判定特写
            # （桩素材=全 NaN facemark 天然不标记——X1 口径同源）
            stats = _disk_stats(char_dir, seg)
            meta[seg]["duration_s"] = _meta_duration_s(stats.get("total_frames"),
                                                       meta[seg]["fps"])
            if stats.get("face_ratio") is not None and stats["face_ratio"] > 0:
                try:
                    meta[seg]["closeup"] = _detect_closeup(
                        os.path.join(char_dir, "orifra", seg),
                        os.path.join(char_dir, "metadata", f"{seg}.npy"))
                except Exception as e:
                    logger.warning(f"[materials] 迁移特写检测失败 seg={seg}: {e}")
            typed += 1
        meta[migrate_key] = True
        _save_meta(meta)
    logger.info(f"[materials] 素材类型迁移定型完成: typed={typed} "
                f"conflicts={len(conflicts)} {conflicts}")
    return {"typed": typed, "conflicts": len(conflicts),
            "conflict_segments": conflicts}


def list_materials(config: dict) -> dict:
    """character_dir 全段清单（磁盘扫描 + 服务在线信息合并 + X3 迁移幂等触发）。"""
    migration = migrate_material_types(config)
    char_dir = resolve_character_dir(config)
    orifra_base = os.path.join(char_dir, "orifra")
    on_disk = sorted(
        d for d in (os.listdir(orifra_base) if os.path.isdir(orifra_base) else [])
        if os.path.isdir(os.path.join(orifra_base, d)))

    refs = collect_references(config)
    st = service_status(config)
    details = (st or {}).get("segment_details") or {}
    current = (st or {}).get("current_segment")
    sched_seq = (((st or {}).get("segment_sequence") or {}).get("sequence")) or []

    items = []
    meta_all = _load_meta()
    for seg in on_disk:
        info = details.get(seg) or {}
        meta = meta_all.get(seg) or {}
        item = {
            "segment": seg,
            "total_frames": info.get("total_frames"),
            "face_ratio": info.get("face_ratio"),
            "resolution": info.get("resolution"),
            "is_current": seg == current,
            "in_scheduler": seg in sched_seq,
            "references": refs.get(seg, []),
            "complete": _piece_complete(char_dir, seg),
        }
        if info.get("total_frames") is None:
            # 服务未运行（或新段未注册）：直接读盘补齐帧数/人脸比例
            disk = _disk_stats(char_dir, seg)
            item.update(disk)
        if item.get("face_ratio") is not None and item["face_ratio"] <= 0.0:
            item["voiceover_only"] = True  # 全段无人脸=纯画外音素材
        # sidecar 元数据合并（T1/R-eng2：type/lip_flag/duration_s/背景/几何/标记）
        merged = dict(_META_DEFAULTS)
        for k in _META_DEFAULTS:
            if k in meta and meta[k] is not None:
                merged[k] = meta[k]
        if merged["duration_s"] is None and item.get("total_frames") is not None:
            merged["duration_s"] = _meta_duration_s(item["total_frames"], merged["fps"])
        item.update(merged)
        items.append(item)
    return {"character_dir": char_dir, "service_online": bool(st),
            "current_segment": current, "items": items,
            "migration": {k: v for k, v in migration.items() if k != "skipped"}}


def _piece_complete(char_dir: str, seg: str) -> bool:
    for d in _PIECE_DIRS:
        if not os.path.isdir(os.path.join(char_dir, d, seg)):
            return False
    for f in _PIECE_FILES:
        if not os.path.isfile(os.path.join(char_dir, f, f"{seg}.npy")):
            return False
    return True


def _disk_stats(char_dir: str, seg: str) -> dict:
    """读盘补齐帧数/人脸比例（numpy 直读 npy，不依赖 torch）。"""
    out = {"total_frames": None, "face_ratio": None, "resolution": None}
    try:
        import numpy as np
        fm_path = os.path.join(char_dir, "facemark", f"{seg}.npy")
        if os.path.isfile(fm_path):
            fm = np.load(fm_path, mmap_mode="r")
            if fm.ndim == 3 and fm.shape[0]:
                valid = ~np.isnan(np.asarray(fm)).all(axis=(1, 2))
                out["total_frames"] = int(fm.shape[0])
                out["face_ratio"] = round(float(valid.mean()), 4)
        elif os.path.isdir(os.path.join(char_dir, "orifra", seg)):
            out["total_frames"] = len([f for f in os.listdir(
                os.path.join(char_dir, "orifra", seg)) if f.endswith(".jpg")])
    except Exception as e:
        logger.warning(f"[materials] 读盘统计失败 seg={seg}: {e}")
    return out


# ----------------------------------------------------------------------
# 添加素材：暂存 → 预处理 → 落盘 → 热上线（后台单飞行任务）
# ----------------------------------------------------------------------

_task: dict = {"state": "idle", "stage": "", "segment": "", "message": "",
               "ok": None, "started_at": None, "finished_at": None}
_task_lock = threading.Lock()


def get_task_status() -> dict:
    with _task_lock:
        return dict(_task)


def _set_task(**kw) -> None:
    with _task_lock:
        _task.update(kw)


def publish_material(config: dict, video_path: str, segment: str,
                     device: str = "cuda", material_type: str = "main",
                     lip_flag: bool = True, background: str = None) -> dict:
    """发起素材添加（后台线程执行；单飞行）。返回任务快照（含排队失败原因）。

    R-ceo3/R-ceo2：material_type∈{enter,main,exit}（D1 三分类）；
    lip_flag=口型推理勾选（True→crop_head_video 完整人脸链；False→桩四件套，
    T2/R-eng4①）；background=可选背景画布图（uc3a/R-dsg11，None=默认背景）。
    """
    segment = str(segment or "").strip()
    if not _SEG_NAME_RE.match(segment):
        raise MaterialError(
            "problem=素材段名非法；cause=仅允许字母/数字/下划线/短横线（1-64 位）；"
            "fix=修改段名（将作为 orifra/ 目录名，编排里按此名引用）")
    if material_type not in _VALID_MATERIAL_TYPES:
        raise MaterialError(
            f"problem=素材类型值域外；cause={material_type!r}；"
            f"fix=仅允许 {'/'.join(_VALID_MATERIAL_TYPES)}")
    if not video_path or not os.path.isfile(str(video_path)):
        raise MaterialError(
            f"problem=视频文件不存在；cause={video_path!r}；fix=选择有效的本地视频文件")
    if str(video_path).lower().endswith(_VIDEO_EXTS) is False:
        raise MaterialError(
            "problem=不支持的文件类型；cause=仅支持 "
            f"{'/'.join(_VIDEO_EXTS)}；fix=转码后重试")
    if background is not None and (not str(background).strip()
                                   or not os.path.isfile(str(background))):
        raise MaterialError(
            f"problem=背景画布图不可读；cause={background!r}；"
            "fix=选择有效的图片文件或留空（默认背景）")

    char_dir = resolve_character_dir(config)
    if not os.path.isdir(char_dir):
        raise MaterialError(
            "problem=角色素材目录不存在；cause=" + char_dir +
            "；fix=先在训练主播页完成一次角色训练（生成角色数据包）")
    if _piece_complete(char_dir, segment):
        raise MaterialError(
            f"problem=已存在同名素材；cause=character_dir 已含段 '{segment}' 四件套；"
            "fix=换一个段名，或先删除旧素材")
    if _training_running():
        raise MaterialError(
            "problem=训练正在进行中；cause=预处理人脸检测与训练争抢 GPU 显存"
            "（16GB 卡不可并存）；fix=等训练完成后再添加素材")

    with _task_lock:
        if _task.get("state") == "running":
            raise MaterialError(
                f"problem=已有素材处理任务进行中；cause=段 '{_task.get('segment')}' "
                "尚未完成；fix=等待当前任务完成或失败后再试")
        _task.update({"state": "running", "stage": "排队", "segment": segment,
                      "message": "", "ok": None,
                      "started_at": time.time(), "finished_at": None})
    threading.Thread(target=_publish_worker,
                     args=(config, str(video_path), segment, device,
                           material_type, bool(lip_flag), background),
                     daemon=True).start()
    return get_task_status()


def _training_running() -> bool:
    try:
        from utils.train_streamer import pipeline as _tp
        return bool(getattr(_tp, "training_running", False))
    except Exception:
        return False


def _publish_worker(config: dict, video_path: str, segment: str,
                    device: str, material_type: str = "main",
                    lip_flag: bool = True, background: str = None) -> None:
    try:
        _publish_sync(config, video_path, segment, device,
                      material_type, lip_flag, background)
        _set_task(state="done", stage="完成", ok=True,
                  message=f"素材 '{segment}' 已上线",
                  finished_at=time.time())
        logger.info(f"[materials] 素材上线完成：{segment}")
    except Exception as e:
        msg = str(e)
        _set_task(state="error", stage="失败", ok=False, message=msg,
                  finished_at=time.time())
        logger.error(f"[materials] 素材上线失败：{segment} — {msg}")


def _publish_sync(config: dict, video_path: str, segment: str,
                  device: str, material_type: str = "main",
                  lip_flag: bool = True, background: str = None) -> None:
    ed_root = edtalk_root()
    char_dir = resolve_character_dir(config)
    stage = staging_dir(segment)

    # 1) 暂存：清残留 → 复制视频（文件名=段名，产出的 orifra 目录名=段名）
    _set_task(stage="暂存视频")
    if os.path.isdir(stage):
        shutil.rmtree(stage, ignore_errors=True)
    ov = os.path.join(stage, "original_videos")
    os.makedirs(ov, exist_ok=True)
    ext = os.path.splitext(video_path)[1].lower()
    staged_video = os.path.join(ov, segment + ext)
    shutil.copy2(video_path, staged_video)
    if background:
        shutil.copy2(background, os.path.join(stage, "background"
                                              + os.path.splitext(background)[1].lower()))

    # 2) 预处理（R-ceo2 按需）：勾选→crop_head_video 完整人脸链；不勾选→桩四件套
    from utils.edtalk_realtime.runtime import resolve_interpreter
    interpreter = resolve_interpreter(config)
    if not interpreter:
        raise MaterialError(
            "problem=EDTalk 解释器未找到；cause=仓库副本与 D:\\AI\\EDTalk\\runtime312 "
            "均无 python.exe；fix=运行 scripts/copy_edtalk.py 补齐运行副本")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    if lip_flag:
        _set_task(stage="预处理（拆帧+人脸检测，耗时与视频长度成正比）")
        script = os.path.join(ed_root, "data_preprocess", "data_preprocess_for_train",
                              "crop_head_video.py")
        if not os.path.isfile(script):
            raise MaterialError(
                "problem=预处理脚本缺失；cause=" + script +
                "；fix=运行 scripts/copy_edtalk.py 补齐 EDTalk 运行副本")
        cmd = [interpreter, "-u", script, stage,
               "--device", device, "--num_workers", "4",
               "--output_size", "256", "--output_format", "jpg",
               "--longest_edge", "1920", "--detect_interval", "3"]
        proc = subprocess.run(cmd, cwd=ed_root, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if proc.returncode != 0:
            tail = (proc.stdout or "")[-800:] + "\n" + (proc.stderr or "")[-800:]
            logger.error(f"[materials] 预处理失败 seg={segment}:\n{tail}")
            raise MaterialError(
                "problem=预处理失败（人脸检测/拆帧）；cause=返回码 "
                f"{proc.returncode}（详见 log/）；"
                "fix=确认视频含清晰人脸；持续失败改用 --device cpu 排查 GPU/WDDM 问题")
    else:
        # 桩四件套（R-eng4①：旁路脚本，runtime 解释器执行——主环境无 cv2）
        _set_task(stage="预处理（简单处理=桩四件套，跳过人脸链）")
        stub_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "stub_pieces.py")
        ffmpeg = os.path.join(ed_root, "runtime312", "ffmpeg", "bin", "ffmpeg.exe")
        cmd = [interpreter, "-u", stub_script, stage, "--segment", segment,
               "--ffmpeg", ffmpeg]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = (proc.stdout or "")[-800:] + "\n" + (proc.stderr or "")[-800:]
            logger.error(f"[materials] 桩生成失败 seg={segment}:\n{tail}")
            raise MaterialError(
                "problem=简单处理失败（拆帧/桩四件套）；cause=返回码 "
                f"{proc.returncode}（详见 log/）；fix=确认视频可解码后重试")

    # 3) 校验四件套齐全（加载器双校验：存在性 :929 + 帧数一致性 :962-967 由
    #    生成器保证；此处只要求"格式齐全"）
    _set_task(stage="校验产出")
    for d in _PIECE_DIRS:
        if not os.path.isdir(os.path.join(stage, d, segment)):
            raise MaterialError(
                f"problem=预处理产出缺 {d}/{segment}；cause=预处理输出不完整；"
                "fix=查看 log/ 中预处理日志定位")
    for f in _PIECE_FILES:
        if not os.path.isfile(os.path.join(stage, f, f"{segment}.npy")):
            raise MaterialError(
                f"problem=预处理产出缺 {f}/{segment}.npy；cause=预处理输出不完整；"
                "fix=查看 log/ 中预处理日志定位")

    # 4) X1 特写检测（R-eng8 覆盖判据；仅挂 on 路径——桩素材跳过，native F1 防误报）
    closeup = False
    closeup_warn = ""
    if lip_flag:
        closeup = _detect_closeup(os.path.join(stage, "orifra", segment),
                                  os.path.join(stage, "metadata", f"{segment}.npy"))
        if closeup:
            closeup_warn = "⚠ 源素材为头部特写：输出将只有头部区域。" \
                           "建议更换含完整画面的源视频重训/重传。"
            logger.warning(f"[materials] {segment}: {closeup_warn}")

    # 5) 落盘 character_dir（move 而非 copy：暂存区不留双份大文件）
    _set_task(stage="落盘角色目录")
    for d in _PIECE_DIRS:
        os.makedirs(os.path.join(char_dir, d), exist_ok=True)
        shutil.move(os.path.join(stage, d, segment),
                    os.path.join(char_dir, d, segment))
    for f in _PIECE_FILES:
        os.makedirs(os.path.join(char_dir, f), exist_ok=True)
        shutil.move(os.path.join(stage, f, f"{segment}.npy"),
                    os.path.join(char_dir, f, f"{segment}.npy"))
    gaze_src = os.path.join(stage, "gaze_data", f"{segment}.npy")
    if os.path.isfile(gaze_src):
        os.makedirs(os.path.join(char_dir, "gaze_data"), exist_ok=True)
        shutil.move(gaze_src, os.path.join(char_dir, "gaze_data", f"{segment}.npy"))
    bg_src = None
    for fn in os.listdir(stage):
        if fn.startswith("background"):
            bg_src = os.path.join(stage, fn)
    bg_char_path = None
    if bg_src:
        os.makedirs(staging_dir(segment), exist_ok=True)
        kept = os.path.join(staging_dir(segment), os.path.basename(bg_src))
        shutil.move(bg_src, kept)
        bg_saved = kept
        # A 方案服务端契约（R-eng2 F-H3）：character_dir/backgrounds/<seg>.jpg
        # = 合成 opt-in 信号；<seg>.json = 几何覆盖（head_position/head_scale）
        bg_char_path = _write_segment_background(char_dir, segment, bg_saved,
                                                 meta_fields)

    # 6) 元数据 sidecar 写入（T1/R-eng2：type/lip_flag/duration/几何/标记）
    stats = _disk_stats(char_dir, segment)
    fps = 25.0
    try:
        fps = float((config or {}).get("edtalk_realtime", {}).get("target_fps", 25))
    except (TypeError, ValueError):
        pass
    bg_saved = None
    if background:
        kept = os.path.join(staging_dir(segment),
                            "background" + os.path.splitext(background)[1].lower())
        bg_saved = kept if os.path.isfile(kept) else None
    meta_fields = dict(_META_DEFAULTS)
    try:
        meta_fields["canvas_w"] = int((config or {}).get("edtalk_realtime", {}).get("canvas_w", 1280))
        meta_fields["canvas_h"] = int((config or {}).get("edtalk_realtime", {}).get("canvas_h", 720))
    except (TypeError, ValueError):
        pass
    meta_fields.update({
        "type": material_type, "lip_flag": bool(lip_flag),
        "fps": fps, "duration_s": _meta_duration_s(stats.get("total_frames"), fps),
        "background": bg_saved, "closeup": bool(closeup),
        "composite": True,
        "server_background": bg_char_path,  # A 方案契约：character_dir/backgrounds/ 路径
    })
    set_material_meta(segment, **meta_fields)

    # 7) 预览缩略（R-dsg11 D2-6：首帧+背景合成，S 级；PIL 在主环境可用）
    try:
        _render_preview(segment, char_dir, meta_fields)
    except Exception as e:
        logger.warning(f"[materials] 预览生成失败 seg={segment}: {e}")

    # 8) 热上线：服务运行中→/segment/add；未运行→下次启动自动扫描发现
    _set_task(stage="热上线")
    st = service_status(config)
    if st:
        import requests
        resp = requests.post(f"{_edtalk_api(config)}/segment/add",
                             json={"segment": segment},
                             headers=_auth_headers(config), timeout=10)
        if resp.status_code != 200:
            raise MaterialError(
                f"problem=热上线失败；cause=EDTalk /segment/add 返回 "
                f"HTTP {resp.status_code}：{str(resp.json())[:200]}；"
                "fix=四件套已落盘，重启运行系统后自动生效")
        info = resp.json() or {}
        face_ratio = info.get("face_ratio")
        hint = "" if (face_ratio or 0) > 0 else "（全段无人脸：将作画外音素材使用）"
        _set_task(message=f"素材 '{segment}' 已热上线，共 {info.get('total_frames')} 帧{hint}"
                          + (f"；{closeup_warn}" if closeup_warn else ""))
    else:
        _set_task(message=f"素材 '{segment}' 已落盘；数字人服务未运行，下次启动自动生效"
                          + (f"；{closeup_warn}" if closeup_warn else ""))


def _write_segment_background(char_dir: str, segment: str, bg_path: str,
                              meta: dict) -> str:
    """段背景+几何落 character_dir/backgrounds/（A 方案交付契约，R-eng2 F-H3）。

    每项入补丁台账（edtalk/FORK_LEDGER.md——台账记录契约本身，文件随素材增删）。
    返回服务端背景文件路径（供 meta 记账）。
    """
    bg_dir = os.path.join(char_dir, "backgrounds")
    os.makedirs(bg_dir, exist_ok=True)
    ext = os.path.splitext(bg_path)[1].lower() or ".jpg"
    dst = os.path.join(bg_dir, segment + ext)
    shutil.copy2(bg_path, dst)
    geo = {
        "head_position": list(meta.get("head_position") or [0.5, 0.42]),
        "head_scale": float(meta.get("head_scale") or 0.5),
        "canvas": [int(meta.get("canvas_w") or 1280), int(meta.get("canvas_h") or 720)],
    }
    with open(os.path.join(bg_dir, segment + ".json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(geo, f, ensure_ascii=False, indent=2)
    return dst


def _remove_segment_background(char_dir: str, segment: str) -> None:
    """删除链同步清理段背景+几何文件（A 方案契约）。"""
    bg_dir = os.path.join(char_dir, "backgrounds")
    for suffix in (".jpg", ".png", ".json"):
        p = os.path.join(bg_dir, segment + suffix)
        if os.path.isfile(p):
            os.remove(p)


def _detect_closeup(orifra_dir: str, metadata_path: str) -> bool:
    """X1 特写检测（R-eng8 覆盖判据 F-L1：crop ⊇ 帧两轴；不扩召回，d3b 语义）。

    桩素材不调用（R-ceo2 native F1：桩按构造全帧 crop，全量误报）。
    metadata 结构化数组 11 字段；orifra 首帧尺寸为帧尺寸基准（PIL，主环境可用）。
    """
    try:
        import numpy as np
        from PIL import Image
        frames = sorted(f for f in os.listdir(orifra_dir) if f.endswith(".jpg"))
        if not frames or not os.path.isfile(metadata_path):
            return False
        w, h = Image.open(os.path.join(orifra_dir, frames[0])).size
        md = np.load(metadata_path)
        if len(md) == 0:
            return False
        # 首帧 crop 覆盖判据：crop 框两轴均覆盖帧（允许原点越界）
        c = md[0]
        covers_x = float(c['crop_x']) <= 0 and float(c['crop_x']) + float(c['crop_size']) >= w
        covers_y = float(c['crop_y']) <= 0 and float(c['crop_y']) + float(c['crop_size']) >= h
        return bool(covers_x and covers_y)
    except Exception as e:
        logger.warning(f"[materials] X1 特写检测失败（默认不标记）: {e}")
        return False


def _render_preview(segment: str, char_dir: str, meta: dict) -> None:
    """首帧+背景合成缩略（R-dsg11 D2-6；预览与出画同源读取 sidecar 几何——F-M3）。"""
    import numpy as np
    from PIL import Image
    orifra_dir = os.path.join(char_dir, "orifra", segment)
    frames = sorted(f for f in os.listdir(orifra_dir) if f.endswith(".jpg"))
    if not frames:
        return
    canvas_w, canvas_h = 1280, 720  # R-ceo9 默认画布（几何 sidecar 缺省）
    bg_path = meta.get("background")
    composite = bool(meta.get("composite", True))
    px, py = meta.get("head_position") or [0.5, 0.42]
    scale = float(meta.get("head_scale") or 0.5)

    canvas = None
    if composite:
        if bg_path and os.path.isfile(str(bg_path)):
            canvas = Image.open(str(bg_path)).convert("RGB")
        else:
            canvas = Image.new("RGB", (canvas_w, canvas_h), (24, 24, 32))  # 深色默认背景（非黑）
        canvas = canvas.resize((canvas_w, canvas_h))
        head = Image.open(os.path.join(orifra_dir, frames[0])).convert("RGB")
        side = int(min(canvas_w, canvas_h) * max(0.1, min(1.0, scale)))
        head = head.resize((side, side))
        cx, cy = int(canvas_w * float(px)), int(canvas_h * float(py))
        canvas.paste(head, (max(0, cx - side // 2), max(0, cy - side // 2)))
    else:
        canvas = Image.open(os.path.join(orifra_dir, frames[0])).convert("RGB")
        canvas = canvas.resize((canvas_w, canvas_h))
    out_dir = staging_dir(segment)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "preview.jpg")
    canvas.save(out, quality=85)
    set_material_meta(segment, preview=out)


def delete_material(config: dict, segment: str) -> dict:
    """删除素材：引用/运行态保护 → 热卸载 → 删盘上四件套。"""
    segment = str(segment or "").strip()
    if not segment:
        raise MaterialError("problem=段名为空；cause=未指定要删除的素材；fix=从列表选择")
    char_dir = resolve_character_dir(config)
    if not _piece_complete(char_dir, segment):
        raise MaterialError(
            f"problem=素材不存在或不完整；cause={segment} 四件套缺失于 {char_dir}；"
            "fix=刷新素材列表")

    blockers = _deletion_blockers(config, segment)
    if blockers:
        raise MaterialError(
            f"problem=素材 '{segment}' 被保护，拒绝删除；cause=" + "；".join(blockers) +
            "；fix=先在动作编排页移除相关引用（规则步骤段/情绪覆盖/默认池/段播放调度表），"
            "运行中的服务需先切走该段")

    # 服务运行中：先热卸载（失败不阻断盘上删除——下次启动即彻底消失）
    st = service_status(config)
    if st and segment in (st.get("available_segments") or []):
        try:
            import requests
            resp = requests.post(f"{_edtalk_api(config)}/segment/remove",
                                 json={"segment": segment},
                                 headers=_auth_headers(config), timeout=10)
            if resp.status_code != 200:
                logger.warning(
                    f"[materials] /segment/remove HTTP {resp.status_code}（继续删盘）")
        except Exception as e:
            logger.warning(f"[materials] /segment/remove 调用失败（继续删盘）：{e}")

    removed = []
    for d in _PIECE_DIRS:
        p = os.path.join(char_dir, d, segment)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            removed.append(f"{d}/{segment}")
    for f in _PIECE_FILES:
        p = os.path.join(char_dir, f, f"{segment}.npy")
        if os.path.isfile(p):
            os.remove(p)
            removed.append(f"{f}/{segment}.npy")
    g = os.path.join(char_dir, "gaze_data", f"{segment}.npy")
    if os.path.isfile(g):
        os.remove(g)
        removed.append(f"gaze_data/{segment}.npy")
    logger.info(f"[materials] 素材已删除：{segment}（{', '.join(removed)}）")
    _remove_segment_background(char_dir, segment)  # A 方案契约：背景/几何同步清理
    # sidecar 清理（T1/R-eng2：删除链同步；预览/背景随暂存目录一并清除）
    with _META_LOCK:
        meta = _load_meta()
        if segment in meta:
            meta.pop(segment, None)
            _save_meta(meta)
    stage_dir = staging_dir(segment)
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir, ignore_errors=True)
    return {"ok": True, "segment": segment, "removed": removed}


# ----------------------------------------------------------------------
# HTTP 路由（挂载 main 进程 FastAPI；POST 回环校验沿 orchestration.api）
# ----------------------------------------------------------------------

def register_material_routes(app, config) -> None:
    """把 /edtalk_materials/* 素材管理路由挂到 main 进程 FastAPI 应用。

    config 参数为 Config 实例（dict 形态，支持 .get；沿 danmaku/orchestration
    路由注册惯例传 WebServer.config）。
    """
    from fastapi import Depends, HTTPException
    from utils.orchestration.api import _require_loopback

    def _cfg():
        return config

    @app.get("/edtalk_materials")
    async def materials_list():
        """素材清单（管理页刷新/轮询；只读不校验来源——观察者可看）。"""
        try:
            return list_materials(_cfg())
        except Exception as e:
            raise HTTPException(status_code=500, detail={
                "problem": "素材清单读取失败", "cause": str(e),
                "fix": "查看 main 进程日志"})

    @app.get("/edtalk_materials/task")
    async def materials_task():
        """添加素材后台任务状态（管理页 2s 轮询）。"""
        return get_task_status()

    @app.post("/edtalk_materials/publish")
    async def materials_publish(payload: dict = None,
                                _guard: None = Depends(_require_loopback)):
        """添加素材（回环校验）：{"video_path": ..., "segment": ..., "device": "cuda",
        "material_type": "main", "lip_flag": true, "background": null}。

        4xx=同步校验失败（段名/文件/目录/训练冲突/任务占用）；202=已入后台。
        """
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象",
                "cause": "需提交 {video_path, segment}",
                "fix": "按素材管理页提交结构重试"})
        try:
            task = publish_material(
                _cfg(), str(payload.get("video_path", "")),
                str(payload.get("segment", "")),
                device=str(payload.get("device", "cuda")),
                material_type=str(payload.get("material_type", "main")),
                lip_flag=bool(payload.get("lip_flag", True)),
                background=payload.get("background") or None)
        except MaterialError as e:
            raise HTTPException(status_code=422, detail={"message": str(e)})
        return {"accepted": True, "task": task}

    @app.post("/edtalk_materials/set_meta")
    async def materials_set_meta(payload: dict = None,
                                 _guard: None = Depends(_require_loopback)):
        """素材元数据更新（回环校验；R-dsg3 改类型/R-dsg11 背景换绑共用）。

        载荷 {"segment": ..., <字段>: 值}；type 值域校验走 set_material_meta。
        """
        if not isinstance(payload, dict) or not str(payload.get("segment", "")).strip():
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象或缺 segment",
                "cause": "需提交 {segment, <字段>}",
                "fix": "按素材管理页改判/背景表单结构重试"})
        try:
            seg = str(payload.pop("segment")).strip()
            return {"ok": True, "segment": seg,
                    "meta": set_material_meta(seg, **payload)}
        except MaterialError as e:
            raise HTTPException(status_code=422, detail={"message": str(e)})

    @app.post("/edtalk_materials/reprocess")
    async def materials_reprocess(payload: dict = None,
                                  _guard: None = Depends(_require_loopback)):
        """按口型推理重处理（回环校验；R-dsg3）：{"segment": ..., "lip_flag": bool}。

        重跑对应预处理链（完整人脸链/桩）+/segment/add 热更新（add 幂等重载，
        T0b 实证）。当前渲染段拒绝（remove 同源保护；R-eng4② 前置保护）。
        """
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象", "cause": "需提交 {segment, lip_flag}",
                "fix": "按素材行重处理动作结构重试"})
        cfg = _cfg()
        seg = str(payload.get("segment", "")).strip()
        lip = bool(payload.get("lip_flag", True))
        try:
            meta = get_material_meta(seg)
            char_dir = resolve_character_dir(cfg)
            if not _piece_complete(char_dir, seg):
                raise MaterialError(
                    f"problem=素材不存在或不完整；cause={seg} 四件套缺失；"
                    "fix=刷新素材列表")
            # 非当前播放段保护（R-eng4②/T0b：add 对已缓存段不重载，
            # remove 拒当前渲染段——重处理必须不在播）
            st = service_status(cfg)
            if st and st.get("current_segment") == seg:
                raise MaterialError(
                    f"problem=素材 '{seg}' 正在渲染中，拒绝重处理；"
                    "cause=当前播放段重载会撕裂渲染缓存；"
                    "fix=先切换到其他段（编排切走或 /segment/switch）再重处理")
            # 原视频需在暂存区（original_videos）；缺失=无法重处理
            stage_ov = os.path.join(staging_dir(seg), "original_videos")
            src = None
            if os.path.isdir(stage_ov):
                for fn in os.listdir(stage_ov):
                    if fn.lower().endswith(_VIDEO_EXTS):
                        src = os.path.join(stage_ov, fn)
                        break
            if not src:
                raise MaterialError(
                    f"problem=素材 '{seg}' 缺原始视频（暂存区无 original_videos）；"
                    "cause=重处理需源视频；fix=删除后重新上传")
            # 移除旧注册（非当前段安全）→ 重跑预处理 → 重注册
            if st:
                import requests as rq
                rq.post(f"{_edtalk_api(cfg)}/segment/remove", json={"segment": seg},
                        headers=_auth_headers(cfg), timeout=10)
            task = publish_material(cfg, src, seg, device="cuda",
                                    material_type=meta.get("type", "main"),
                                    lip_flag=lip,
                                    background=meta.get("background"))
            return {"accepted": True, "task": task}
        except MaterialError as e:
            raise HTTPException(status_code=422, detail={"message": str(e)})

    @app.post("/edtalk_materials/delete")
    async def materials_delete(payload: dict = None,
                               _guard: None = Depends(_require_loopback)):
        """删除素材（回环校验）：{"segment": ...}。保护拒绝=422 附修复动作。"""
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象", "cause": "需提交 {segment}",
                "fix": "按素材管理页提交结构重试"})
        try:
            return delete_material(_cfg(), str(payload.get("segment", "")))
        except MaterialError as e:
            raise HTTPException(status_code=422, detail={"message": str(e)})
