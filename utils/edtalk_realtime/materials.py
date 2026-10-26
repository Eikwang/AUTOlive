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


class MaterialError(Exception):
    """素材操作语义失败（problem/cause/fix 三段式消息；API 层映射 4xx）。"""


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

def list_materials(config: dict) -> dict:
    """character_dir 全段清单（磁盘扫描 + 服务在线信息合并）。"""
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
    for seg in on_disk:
        info = details.get(seg) or {}
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
        items.append(item)
    return {"character_dir": char_dir, "service_online": bool(st),
            "current_segment": current, "items": items}


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
                     device: str = "cuda") -> dict:
    """发起素材添加（后台线程执行；单飞行）。返回任务快照（含排队失败原因）。"""
    segment = str(segment or "").strip()
    if not _SEG_NAME_RE.match(segment):
        raise MaterialError(
            "problem=素材段名非法；cause=仅允许字母/数字/下划线/短横线（1-64 位）；"
            "fix=修改段名（将作为 orifra/ 目录名，编排里按此名引用）")
    if not video_path or not os.path.isfile(str(video_path)):
        raise MaterialError(
            f"problem=视频文件不存在；cause={video_path!r}；fix=选择有效的本地视频文件")
    if str(video_path).lower().endswith(_VIDEO_EXTS) is False:
        raise MaterialError(
            "problem=不支持的文件类型；cause=仅支持 "
            f"{'/'.join(_VIDEO_EXTS)}；fix=转码后重试")

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
                     args=(config, str(video_path), segment, device),
                     daemon=True).start()
    return get_task_status()


def _training_running() -> bool:
    try:
        from utils.train_streamer import pipeline as _tp
        return bool(getattr(_tp, "training_running", False))
    except Exception:
        return False


def _publish_worker(config: dict, video_path: str, segment: str,
                    device: str) -> None:
    try:
        _publish_sync(config, video_path, segment, device)
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
                  device: str) -> None:
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

    # 2) 预处理：步骤10 crop_head_video（拆帧+人脸检测+关键点 → 四件套）
    _set_task(stage="预处理（拆帧+人脸检测，耗时与视频长度成正比）")
    from utils.edtalk_realtime.runtime import resolve_interpreter
    interpreter = resolve_interpreter(config)
    if not interpreter:
        raise MaterialError(
            "problem=EDTalk 解释器未找到；cause=仓库副本与 D:\\AI\\EDTalk\\runtime312 "
            "均无 python.exe；fix=运行 scripts/copy_edtalk.py 补齐运行副本")
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
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
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

    # 3) 校验四件套齐全（预处理对无人脸视频仍产出全 NaN facemark——合法素材，
    #    播放时走画外音直通；这里只要求"格式齐全"）
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

    # 4) 落盘 character_dir（move 而非 copy：暂存区不留双份大文件）
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
    shutil.rmtree(stage, ignore_errors=True)

    # 5) 热上线：服务运行中→/segment/add；未运行→下次启动自动扫描发现
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
        _set_task(message=f"素材 '{segment}' 已热上线，共 {info.get('total_frames')} 帧{hint}")
    else:
        _set_task(message=f"素材 '{segment}' 已落盘；数字人服务未运行，下次启动自动生效")


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
        """添加素材（回环校验）：{"video_path": ..., "segment": ..., "device": "cuda"}。

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
                device=str(payload.get("device", "cuda")))
        except MaterialError as e:
            raise HTTPException(status_code=422, detail={"message": str(e)})
        return {"accepted": True, "task": task}

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
