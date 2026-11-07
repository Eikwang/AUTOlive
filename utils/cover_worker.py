# -*- coding: UTF-8 -*-
"""
翻唱队列与 AICoverGen worker 编排（P3-2/P3-3）

架构：
- 队列状态机在 AUTOlive 主进程（本模块）：入队（上限 5，B-3 同观众在队上限 1，
  冷却 60s）→ ffprobe 预检（S-1）→ 逐个派发子进程（AICoverGen src/main.py）
- 子进程 = RVC runtime python -c bootstrap（嵌入式 ._pth 需显式 sys.path 注入）
- 三阶段计时（B-2）：下载/加载/推理分离；本地曲源无下载阶段
- 成品原子写入：临时名 + os.replace（S-4）
- GPU 准入：派发前 gpu_policy（E-2），拒绝→排队等待
- 生命周期（H-3）：worker 子进程随批任务启停，队列空自动退出
- 曲源（S-3）：仅本地 song_path 原始歌曲文件，拒绝弹幕 URL（防 SSRF）
- 歌名归一化（B-6）：去空格/括注/大小写后匹配

状态机：pending → ffprobe → running → done/failed(retry×1)/timeout/queued_full
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Callable, Dict, List, Optional

from utils.my_log import logger

# 直播文案五态（D6）——文案模板由调用方（choose_song）渲染
NOTIFY_TEMPLATES = {
    "success": "🎤 {viewer} 点的《{song}》翻唱完成（声音：{profile}），来了！",
    "failed": "抱歉，《{song}》翻唱失败，换首试试？",
    "queue_full": "点歌排队已满，稍后再点吧！",
    "cooldown": None,  # 冷却=静默（有意设计 D6）
    "timeout": "《{song}》翻唱超时了，再点一次试试？",
}


# ---------- 补刀定位单源（T3/R-1，Eng A-1/E-1：命名约定单点） ----------
# 参数链签名（Eng E-1 首选定位）：唯一不受歌名污染/mtime 时序影响的标记——
# 派发参数只由 cover_worker 生成，半成品 mixed 不含它。
_VOCAL_PARAM_RE = re.compile(
    r"_p-?\d+_i[\d.]+_fr\d+_rms[\d.]+_pro[\d.]+_(rmvpe|mangio-crepe)", re.I)
FAULTHANDLER_ENV = {"PYTHONFAULTHANDLER": "1"}  # R-2：原生崩溃 Python 栈 dump（主跑+补刀对称）


class CoverQueue:
    """翻唱任务队列（单例；choose_song 写入，worker 线程消费）。"""

    def __init__(self, config, paths):
        self.config = config
        self.paths = paths
        self.queue: List[Dict] = []          # 待处理
        self.current: Optional[Dict] = None  # 进行中
        self.results: Dict[str, str] = {}    # song_key → 成品路径（缓存命中）
        self.lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_dispatch_ts = 0.0

    # ---------- 配置读取 ----------

    def _policies(self) -> dict:
        base = {
            "queue_max": 5, "cooldown_s": 60, "job_timeout_min": 10,
            "retries": 1, "vram_reserve_gb": 4.0, "same_viewer_in_queue_max": 1,
            "worker_idle_exit_s": 300,
            "cover_sweep_log_only": True,  # DX-7 浸泡：C7 扫描首周 log-only（期满配置切 False）
        }
        custom = self.config.get("audio_integration", "policies") or {}
        base.update({k: v for k, v in custom.items() if isinstance(v, (int, float))})
        return base

    def _song_path(self) -> str:
        """歌曲音频根目录（/autoplan 2026-10-23 批次 R1：song_path 用户配置键退役，
        改内部约定常量经 autolive_paths.song_audio_root 锚定，R13 遗留键迁移告警内聚于此）。"""
        from utils.autolive_paths import song_audio_root
        return song_audio_root(self.config)

    def _covers_dir(self) -> str:
        return os.path.join(self._song_path(), "covers")

    def _rvc_models_root(self) -> str:
        """RVC 模型共享目录基根（C-2 修复，/autoplan 2026-10-14 outside 发现 2）：
        AICoverGen main.py:31-34 读 `AICoverGen根/rvc_models`，旧实现写
        `rvc_vc.rvc_root/rvc_models`（目录不存在）——写读两根永不相交=共享模型
        翻唱必然失败。基根切换到 aicovergen 根（apps/aicovergen/rvc_models，
        S-1 junction 对齐）。config paths.aicovergen_root 可覆盖。"""
        return os.path.join(self._aicovergen_root(), "rvc_models")

    def _aicovergen_root(self) -> str:
        """AICoverGen 根（C-4 收敛：config paths.aicovergen_root → apps 副本 →
        旧 aicovergen.root → 外部树兜底）。"""
        for section, key in (("paths", "aicovergen_root"), ("aicovergen", "root")):
            v = self.config.get(section, key)
            if v and str(v).strip():
                return os.path.abspath(str(v).strip())
        return os.path.join(
            self.config.get("paths", "autolive_home")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "apps", "aicovergen")

    def _aicovergen_src(self) -> str:
        return os.path.join(self._aicovergen_root(), "src")

    def _rt_python(self) -> str:
        """翻唱子进程解释器（2026-11-07 迁移收尾修复）：统一项目内环境——

        取值序：rvc_vc.runtime_python 显式覆盖（指向真实文件才采纳）→
        候选链 paths.rvc_python（config 覆盖→记忆→仓库根 runtime312→外部
        兜底）。**历史**：原实现回退硬编码 py39 外部嵌入式环境
        （py39 外部嵌入式环境，S-2 时代钉子），统一项目内候选链。
        **2026-11-07 二次修复**：paths 构造参数两个调用点均传 None
        （cover_endpoints.py:92 / feature_handler.py:691）——上一版
        getattr(self.paths,...) 返回空 cmd[0] → Popen WinError 87（用户
        实测）。修复=paths 为 None 时内部自建 AutolivePaths（进程级缓存）。"""
        v = self.config.get("rvc_vc", "runtime_python")
        if v and str(v).strip() and os.path.isfile(str(v).strip()):
            return str(v).strip()
        try:
            paths = self.paths
            if paths is None:
                from utils.autolive_paths import AutolivePaths
                paths = CoverQueue._cached_paths()
            return getattr(paths, "rvc_python", None) or ""
        except Exception as e:
            logger.error(f"[cover] 解释器候选链解析失败: {e}")
            return ""

    @staticmethod
    def _gpu_compute_snapshot() -> str:
        """GPU compute 进程快照（2026-11-07 诊断增强）：翻唱前/后各落一条——
        ①证明主跑在 GPU（运行中快照含主跑 python 进程+显存）；②主跑退出后
        的残留显存直接现形（PID+进程名+MB）。异常不阻断主流程。"""
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name",
                 "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
            out = (r.stdout or "").strip()
            return out if out else "（无 compute 进程）"
        except Exception as e:
            return f"(nvidia-smi 不可用: {e})"

    @staticmethod
    def _cached_paths():
        """AutolivePaths 进程级缓存（构造有文件探测 IO——每任务一次不划算）。"""
        inst = getattr(CoverQueue, "_paths_cache", None)
        if inst is None:
            from utils.autolive_paths import AutolivePaths
            inst = AutolivePaths(CoverQueue._config_for_paths())
            CoverQueue._paths_cache = inst
        return inst

    @staticmethod
    def _config_for_paths():
        from utils.config import Config
        return Config(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"))

    # ---------- 归一化匹配（B-6）----------

    @staticmethod
    def normalize_song(name: str) -> str:
        """歌名归一化：去空格/括注/大小写（B-6）。"""
        import re
        n = name.strip().lower()
        n = re.sub(r"[（(].*?[)）]", "", n)   # 圆括注（中英）
        n = re.sub(r"【.*?】", "", n)         # 方头括注（标签前缀）
        n = re.sub(r"\[.*?\]", "", n)         # 方括注
        n = re.sub(r"[\s]+", "", n)
        return n

    def find_local_song(self, song_name: str) -> Optional[str]:
        """本地曲源查找（S-3：仅本地文件）。返回原始歌曲文件路径。"""
        root = self._song_path()
        if not os.path.isdir(root):
            return None
        target = self.normalize_song(song_name)
        for fn in os.listdir(root):
            base, ext = os.path.splitext(fn)
            if ext.lower() not in (".mp3", ".wav", ".flac"):
                continue
            if self.normalize_song(base) == target:
                return os.path.join(root, fn)
        return None

    def cover_path(self, song_name: str, profile_id: str) -> str:
        """成品路径（DX-6 covers 定义：song_path 下 covers/ 子目录）。"""
        safe = "".join(c for c in song_name if c not in '\\/:*?"<>|')
        return os.path.join(self._covers_dir(), f"{safe}__{profile_id}.wav")

    # ---------- 入队（D6/B-3/S-1/S-4）----------

    def enqueue(self, viewer: str, song_name: str, profile_id: str,
                manual: bool = False) -> Dict:
        """弹幕点歌入口。返回 {action, message, cover_path?}。

        action: cached / queued / queue_full / cooldown / duplicated_in_queue
        manual=True（webui 一键翻唱）：跳过同观众在队上限与冷却治理——冷却/在队
        上限防的是弹幕刷屏，手动点按是有意的用户动作，连翻多首不应被静默拒绝；
        队列上限与成品缓存命中两道治理对 manual 仍然生效。
        """
        policies = self._policies()
        cover = self.cover_path(song_name, profile_id)
        if os.path.isfile(cover):
            return {"action": "cached", "cover_path": cover}
        with self.lock:
            if len(self.queue) >= policies["queue_max"]:
                return {"action": "queue_full"}
            if not manual:
                in_queue = [j for j in self.queue if j["viewer"] == viewer]
                in_queue += [self.current] if (self.current and self.current["viewer"] == viewer) else []
                if len(in_queue) >= policies.get("same_viewer_in_queue_max", 1):
                    return {"action": "cooldown"}  # B-3：同观众在队上限 1，静默
                now = time.time()
                last = getattr(self, "_last_enqueue_by_viewer", {}).get(viewer, 0)
                if now - last < policies["cooldown_s"]:
                    return {"action": "cooldown"}  # 静默（D6 有意设计）
                getattr(self, "_last_enqueue_by_viewer", {})[viewer] = now
            job = {
                "viewer": viewer, "song": song_name,
                "profile_id": profile_id,
                "song_path": self.find_local_song(song_name),
                "cover_path": cover, "retries_left": policies["retries"],
                "started_at": None,
            }
            self.queue.append(job)
        return {"action": "queued", "position": len(self.queue)}

    # ---------- worker 线程（H-3：随批启停）----------

    def ensure_worker(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="cover-worker",
                                            daemon=True)
            self._thread.start()

    def shutdown(self):
        self._stop.set()

    def _loop(self):
        """worker 主循环：空队列空闲退出（H-3）。"""
        policies = self._policies()
        idle_since = time.time()
        while not self._stop.is_set():
            job = None
            with self.lock:
                if self.queue:
                    job = self.queue.pop(0)
                    self.current = job
                    idle_since = time.time()
            if job is None:
                if time.time() - idle_since > policies["worker_idle_exit_s"]:
                    logger.info("[cover-worker] 空闲超时退出（H-3）")
                    return
                self._stop.wait(2.0)
                continue
            self._process(job, policies)
            with self.lock:
                self.current = None
            idle_since = time.time()

    def _process(self, job: Dict, policies: dict):
        """单任务处理：ffprobe 预检 → GPU 准入 → 派发 AICoverGen → 原子改名。"""
        from utils.gpu_policy import background_task_gate
        song, cover = job["song"], job["cover_path"]
        if job["song_path"] is None:
            logger.warning(f"[cover] 本地无此歌曲（S-3 拒绝外部 URL）: {song}")
            self._fail(job, policies)
            return
        if not os.path.isfile(job["song_path"]):
            self._fail(job, policies)
            return
        # GPU 准入（E-2）：不满足→等待重试（排队语义，B-3 对立面）。
        # T3（2026-11-02 批次）：等待原因/稳定码写入 job 供 /cover/status 透出
        # （webui 轮询侧做转换通知——worker 无 NiceGUI 客户端，Eng F1）；准入
        # 通过置空两字段（C-2 防推理期残留误导）；日志首次 info 后续 debug（F9）。
        waited_once = False
        while True:
            ok, reason, wait_code = background_task_gate(policies["vram_reserve_gb"])
            if ok or self._stop.is_set():
                break
            job["wait_reason"] = reason
            job["wait_code"] = wait_code
            if not waited_once:
                logger.info(f"[cover] GPU 忙（{reason}），任务 {song} 排队等待（空闲后自动开始）")
                waited_once = True
            else:
                logger.debug(f"[cover] GPU 忙（{reason}），任务 {song} 继续等待")
            self._stop.wait(30)
        job["wait_reason"] = ""
        job["wait_code"] = ""  # C-2：准入通过置空（webui 轮询侧按 非空→空 转换 notify）
        if waited_once:
            logger.info(f"[cover] 等待结束，开始翻唱: {song}")
        logger.info(f"[cover][GPU前] compute 进程快照: {self._gpu_compute_snapshot()}")
        if self._stop.is_set():
            return
        os.makedirs(self._covers_dir(), exist_ok=True)
        self._prepare_rvc_model_dir(job)
        # B-2：计时只覆盖推理阶段（本地曲源无下载段）
        started = time.time()
        job["started_at"] = started
        timeout_s = policies["job_timeout_min"] * 60
        profile = job["profile_id"]
        # AICoverGen：模型目录约定 rvc_models/<dirname>/（复制/硬链一份）
        # C-1 修复（/autoplan 2026-10-14）：派发补 -oformat wav——旧默认 mp3 与
        # _find_produced_wav 只匹配 .wav 矛盾，落库命中未混音中间干人声。
        # C-3 音高接线：前端八度值直接传 -p（CLI 单位=八度，main.py:330 help；
        # AICoverGen main.py:292 内部 ×12——前端不得预乘，outside 发现 1 更正）。
        pitch = self.config.get("choose_song", "cover_pitch")
        try:
            pitch = str(max(-12, min(12, int(pitch or 0))))
        except (TypeError, ValueError):
            pitch = "0"
        cmd = [
            self._rt_python(), "-c", self._bootstrap_code(),
            "--", "-i", job["song_path"], "-dir", f"autolive_{profile}",
            "-p", pitch, "-ir", "0.75", "-palgo", "rmvpe", "-oformat", "wav",
            # 分轨保留 v2（2026-11-03 补刀退役批次）：-k 保留中间产物——主跑
            # 原生清理（main.py:311-318）会删 _Instrumental.wav 等 5 件，是
            # 「分轨定位不全 inst=False」警告的根因（崩溃时代 pipeline 未走完
            # 才留得下）；分轨复制后由 _retain_split_tracks 自清理（保留
            # _Instrumental 作 MDX 缓存件）。
            "-k",
        ]
        env = os.environ.copy()
        env.setdefault("AUTOLIVE_COVER_OUT", self._covers_dir())
        env.update(FAULTHANDLER_ENV)  # R-2：原生崩溃 Python 栈 dump（主跑侧）
        # 主跑输出落盘（2026-11-02 实测批次：PIPE 不读=死锁风险+AICoverGen 真实
        # 报错全被丢弃——"未找到 RVC 中产物"却无一行原因）。写文件+尾部回灌日志。
        run_log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "logs", "cover_aicovergen_last.log")
        os.makedirs(os.path.dirname(run_log), exist_ok=True)
        try:
            with open(run_log, "w", encoding="utf-8", errors="replace") as logf:
                logf.write(f"# cmd: {cmd}\n# cwd: {self._aicovergen_src()}\n")
                logf.flush()
                proc = subprocess.Popen(
                    cmd, cwd=self._aicovergen_src(), env=env,
                    stdout=logf, stderr=subprocess.STDOUT,
                )
                self.proc = proc
                rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            logger.error(f"[cover] 超时（{timeout_s}s）：{song}；主跑输出: {run_log}")
            # C7 ①超时场景（主跑仍存活，spec F1）：树杀 taskkill /F /T——活进程
            # 场景与先例 train_rvc_pipeline.py:241-243 同型；ENG-5：taskkill 失败
            # 或执行前已自行退出（竞态）→回落 psutil 双条件扫描（崩溃分支同款）
            if hasattr(self, "proc") and self.proc:
                try:
                    self.proc.kill()
                except Exception:
                    pass
                self._reap_process_tree(self.proc)
            self._fail(job, policies, timeout=True)
            return
        except FileNotFoundError as _sp_err:
            logger.error(f"[cover] 主跑解释器/命令不可达：{_sp_err}；cmd[0]={cmd[0]}")
            self._fail(job, policies)
            return
        logger.info(f"[cover] 主跑结束 rc={rc}：{song}（输出: {run_log}）")
        logger.info(f"[cover][GPU后] compute 进程快照: {self._gpu_compute_snapshot()}")
        if rc != 0:
            # C7 ②崩溃场景（spec CL-2 挂点）：主跑已死——psutil 双条件扫描收尸
            # （收尸与产物成败解耦：崩溃≠失败，produced 判定在下方）
            if hasattr(self, "proc") and self.proc and self.proc.poll() is not None:
                self._reap_process_tree(self.proc)
            # 非零 rc 尾部落日志供诊断（C5 补刀退役 2026-11-03：runtime312 下
            # 主跑 rc 稳定为 0，崩溃路径已退役；此分支仅剩异常环境兜底诊断）
            try:
                tail = open(run_log, encoding="utf-8", errors="replace").read()[-800:]
                logger.info(f"[cover] 主跑非零 rc，尾部输出:\n{tail}")
            except Exception:
                pass
        # 原子落位（S-4）：AICoverGen 输出在 song_output/，找到成品移到 covers/
        produced = self._find_produced_wav(job)
        if not produced:
            # 诊断上下文：song_output 实际内容（产物命名漂移/空目录一目了然）
            out_root = os.path.join(self._aicovergen_root(), "song_output")
            listing = []
            for dirpath, _, fns in os.walk(out_root):
                for fn in fns:
                    full = os.path.join(dirpath, fn)
                    if os.path.getmtime(full) > job.get("started_at", 0):
                        listing.append(os.path.relpath(full, out_root))
            logger.info(f"[cover] song_output 本轮新增产物 {len(listing)} 个: {listing[:8]}")
        # C5 补刀退役（2026-11-03 用户确认：runtime312 主跑多次实测 rc=0 稳定，
        # pedalboard 崩溃路径随解释器切换根治——补刀兜底链
        # _postprocess_fallback/_locate_cover_sources/_postprocess_code 退役删除，
        # git revert 16767949 可回滚）。R-6 语义保留：成功条件=成品存在
        #（produced），rc 仅作诊断日志。
        if produced:
            tmp = cover + ".tmp.wav"
            shutil.copy2(produced, tmp)
            os.replace(tmp, cover)
            logger.info(f"[cover] 完成: {song} → {cover}"
                        f"{'（主跑非零 rc，产物有效）' if rc != 0 else ''}")
            # R6 分轨保留（/autoplan 2026-10-23 批次）：人声轨+伴奏轨复制入 covers/
            # 供 SongMode 双轨播放（人声驱动 EDTalk 对口型+伴奏外放）；缺失静默
            # （feature_handler._song_covers_hit 自动降级整轨）。
            self._retain_split_tracks(job, cover)
            # C-5 自动插播（/autoplan 2026-10-14 终门 uc1a=立即插播）：回调由
            # feature_handler 注入（复用点歌播放通道）；成品已落盘，回调失败不影响任务
            cb = getattr(self, "on_done", None)
            if cb:
                try:
                    cb(job)
                except Exception:
                    logger.error("[cover] 插播回调异常（成品已落盘不影响任务）", exc_info=True)
        else:
            self._fail(job, policies)

    def _reap_process_tree(self, proc):
        """C7 进程树收尸（spec F1 双场景+ENG-5 回落+ENG-6 分级+DX-7 浸泡）。

        ①活 PID 树杀：taskkill /F /T /PID（活进程场景，先例
          utils/train_rvc_pipeline.py:241-243 同型——进程树枚举有效）；
        ②死 PID/回落（ENG-5：taskkill 竞态失败或执行前自行退出）：psutil 双
          条件扫描——映像名 python*.exe 且 cwd 或 cmdline 完整匹配 AICoverGen
          源目录（spec CP-4 收窄：编辑器/终端非 Python 进程被映像条件排除，
          非 aicovergen 路径 Python 进程被路径条件排除）；
        ③DX-7 log-only 浸泡：audio_integration.policies.cover_sweep_log_only
          （默认 True）首周只记录不 terminate，期满无误杀再启用（配置键切换）；
        ④ENG-6 日志分级：扫描无残留=DEBUG / 处置（或 log-only 记录）=INFO /
          收尸全败=DX-3 WARNING 模板（崩溃为常态路径，防每任务刷控制台）。"""
        import psutil
        src = self._aicovergen_src()
        policies = self._policies()
        log_only = bool(policies.get("cover_sweep_log_only", True))
        pid = getattr(proc, "pid", None)
        alive = proc is not None and proc.poll() is None

        # ① 活 PID 树杀
        if alive and pid:
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode == 0:
                    logger.info(f"[cover] 主跑进程树已清理（taskkill /T pid={pid}）")
                else:
                    logger.debug(f"[cover] taskkill 未命中（pid={pid}，可能已退出）——回落扫描")
            except Exception as e:
                logger.warning(f"[cover] taskkill 异常（pid={pid}）：{e}——回落扫描")

        # ② psutil 双条件扫描（死 PID 场景/回落/兜底）
        victims = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                name = (p.info.get("name") or "").lower()
                if not name.startswith("python"):
                    continue  # 映像条件：编辑器/终端等非 Python 进程不触碰
                cwd = p.info.get("cwd") or ""
                cmdline = " ".join(p.info.get("cmdline") or [])
                if cwd == src or src in cmdline:
                    victims.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue  # cmdline 或语义兜底（spec F-1：cwd 读失败不排除）
        if not victims:
            logger.debug("[cover] 收尸扫描：无残留进程")  # ENG-6：常态路径 DEBUG
            return
        desc = "; ".join(f"pid={v.pid} name={v.info.get('name')} "
                         f"cwd={v.info.get('cwd')}" for v in victims)
        if log_only:
            # DX-7 浸泡：仅记录（INFO），附手动清理命令（可复核误杀面）
            logger.info(f"[cover] 收尸浸泡期（log-only）：发现疑似残留 "
                        f"{len(victims)} 个——{desc}；手动清理 taskkill /F /T /PID "
                        f"{victims[0].pid}；复核 nvidia-smi --query-compute-apps="
                        f"pid,process_name,used_memory --format=csv")
            return
        # ③ terminate（浸泡期满后启用）
        remaining = []
        for v in victims:
            try:
                v.terminate()
            except psutil.NoSuchProcess:
                continue
            except Exception as e:
                logger.warning(f"[cover] terminate pid={v.pid} 异常：{e}")
                remaining.append(v)
        gone = len(victims) - len(remaining)
        if gone:
            logger.info(f"[cover] 收尸完成：清理 {gone} 个 AICoverGen 残留进程")
        if remaining:
            # DX-3 WARNING 模板（收尸全败——含手动清理命令）
            logger.warning(f"[cover] 收尸未完成：残留 pid={','.join(str(v.pid) for v in remaining)}"
                           f"（image={remaining[0].info.get('name')}）——手动清理 "
                           f"taskkill /F /T /PID {remaining[0].pid}；复核 nvidia-smi "
                           f"--query-compute-apps=pid,process_name,used_memory --format=csv")

    def _generate_silences_file(self, base: str):
        """d15（2026-11-05 编排优化批次 T5）：vocal 分轨 RMS 扫描 → 成品旁挂
        .silences.json（消费端语义切片数据源）。fail-soft：任何异常仅告警——
        静音数据缺失=消费端回退 45s 固定切片，不影响成品与分轨播放。"""
        vocal = f"{base}_vocal.wav"
        out = base + ".silences.json"
        try:
            from utils.audio.vocal_silences import (
                compute_silences, write_silences_file,
            )
            silences = compute_silences(vocal)
            if write_silences_file(out, silences):
                logger.info(f"[cover] 静音段数据已生成：{out}（{len(silences)} 段间奏）")
        except Exception:
            logger.warning(f"[cover] 静音段数据生成失败（点歌回退固定切片）：{out}",
                           exc_info=True)

    def _retain_split_tracks(self, job: Dict, cover: str):
        """分轨保留 v2（2026-11-03 补刀退役批次重写）：从 song_output 定位本次
        任务的 RVC 人声（参数链产物）与 *_Instrumental.wav（MDX 伴奏），复制为
        covers/<成品base>_vocal.wav / _inst.wav，供 SongMode 双轨播放（人声驱动
        EDTalk 对口型+伴奏外放）；缺失告警不失败——播放侧自动降级整轨成品。

        v2 变更（修复「分轨定位不全 inst=False」警告）：
        - 派发加 -k（主跑原生清理 main.py:311-318 会删 _Instrumental 等 5 件，
          是 inst=False 的根因——崩溃时代 pipeline 未走完才留得下）；
        - 无 mtime 门槛（R-5 同源教训：同曲缓存重翻时产物 mtime 早于
          started_at，mtime 守卫会排除正确应复用的产物）；
        - stem 匹配隔离（跨歌防串台，替代 mtime 圈定）；
        - 复制后自清理：删本次 stem 的全部中间 wav（含主跑残留 mixed 半成品，
          CEO C1 语义迁移），**仅保留 *_Instrumental.wav**（MDX 最慢环节的
          缓存件——缓存重翻时跳过 MDX；vocal 源删后缓存重翻由 RVC 重产）。
        定位失败仅告警不失败。"""
        try:
            out_root = os.path.join(self._aicovergen_src(), "..", "song_output")
            if not os.path.isdir(out_root):
                return
            stem = os.path.splitext(os.path.basename(job.get("song_path") or ""))[0]
            if not stem:
                logger.warning("[cover] 分轨保留中止：job 无 song_path（stem 不可得）")
                return
            vocal = inst = None
            matched: List[str] = []  # 本次 stem 的全部产物（清理候选）
            for dirpath, _, filenames in os.walk(out_root):
                for fn in filenames:
                    if not fn.endswith(".wav") or not fn.startswith(stem):
                        continue
                    full = os.path.join(dirpath, fn)
                    matched.append(full)
                    if fn.endswith("_Instrumental.wav"):
                        if inst is None or os.path.getmtime(full) > os.path.getmtime(inst):
                            inst = full  # 最新者胜（多轮同曲取最新）
                        continue
                    if "mixed" in fn.lower():
                        continue  # mixed 半成品/成品（covers 已有），不作战 vocal
                    if _VOCAL_PARAM_RE.search(fn):
                        if vocal is None or os.path.getmtime(full) > os.path.getmtime(vocal):
                            vocal = full  # RVC 人声（参数链签名，Eng E-1 单源）
            base = os.path.splitext(cover)[0]
            if vocal and os.path.isfile(vocal):
                shutil.copy2(vocal, base + "_vocal.wav")
            if inst and os.path.isfile(inst):
                shutil.copy2(inst, base + "_inst.wav")
            # 自清理：本次 stem 的中间产物（inst 源除外——MDX 缓存件；mixed
            # 半成品一并清，CEO C1 语义迁移自退役的 _locate_cover_sources）
            cleaned = 0
            for full in matched:
                if full == inst:
                    continue
                try:
                    os.remove(full)
                    cleaned += 1
                except OSError:
                    pass
            if cleaned:
                logger.debug(f"[cover] 分轨自清理：本次任务中间产物 {cleaned} 个"
                             f"（_Instrumental 保留为 MDX 缓存件）")
            if vocal and inst:
                logger.info(f"[cover] 分轨保留: {base}_vocal.wav + _inst.wav")
            else:
                logger.warning(
                    f"[cover] 分轨定位不全（vocal={vocal is not None}, "
                    f"inst={inst is not None}），播放将走整轨降级"
                    f"——stem={stem!r}；核对派发 -k 是否生效与 AICoverGen 产物命名")
            # d15（2026-11-05 编排优化批次 T5）：人声静音段数据文件生成——
            # vocal 分轨 RMS 扫描（-40dB/≥3s，C3 实测校准义务：参数经
            # vocal_silences 常量起步）→ 成品旁挂 .silences.json（消费端
            # 语义切片数据源；失败 fail-soft 不影响成品与分轨播放）
            if vocal and os.path.isfile(f"{base}_vocal.wav"):
                self._generate_silences_file(base)
            else:
                logger.info(f"[cover] 跳过 silences 生成（无 vocal 分轨）：{base}")
        except Exception:
            logger.error("[cover] 分轨保留异常（不影响成品）", exc_info=True)


    def _fail(self, job: Dict, policies: dict, timeout: bool = False):
        if job["retries_left"] > 0:
            job["retries_left"] -= 1
            with self.lock:
                self.queue.insert(0, job)  # 重试插队头
            logger.info(f"[cover] 重试（余 {job['retries_left']}）: {job['song']}")
        else:
            logger.error(f"[cover] 最终失败: {job['song']}（超时={timeout}）")

    def _prepare_rvc_model_dir(self, job: Dict):
        """AICoverGen 约定 rvc_models/<dirname>/{pth,index}——硬链/复制共享目录文件。"""
        from utils.voice_profiles import get_profile
        prof = get_profile(job["profile_id"]) or {}
        pth = prof.get("rvc_model") or ""
        index = prof.get("rvc_index") or ""
        if not pth or not os.path.isfile(pth):
            # 回退：取 models/rvc/ 第一个 .pth（单模型场景）
            models_dir = os.path.join(
                self.config.get("paths", "autolive_home")
                or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "rvc")
            pths = sorted(f for f in os.listdir(models_dir) if f.endswith(".pth")) \
                if os.path.isdir(models_dir) else []
            if not pths:
                raise FileNotFoundError("models/rvc/ 无可用 RVC 模型（DX-5 预取路径）")
            pth = os.path.join(models_dir, pths[0])
            index = ""
        dest = os.path.join(self._rvc_models_root(), f"autolive_{job['profile_id']}")
        os.makedirs(dest, exist_ok=True)
        dst_pth = os.path.join(dest, f"{job['profile_id']}.pth")
        if not os.path.isfile(dst_pth):
            try:
                os.link(pth, dst_pth)  # 同卷硬链（零拷贝）
            except OSError:
                shutil.copy2(pth, dst_pth)
        if index and os.path.isfile(index):
            dst_idx = os.path.join(dest, os.path.basename(index))
            if not os.path.isfile(dst_idx):
                try:
                    os.link(index, dst_idx)
                except OSError:
                    shutil.copy2(index, dst_idx)

    def _bootstrap_code(self) -> str:
        """嵌入式 ._pth 绕过：显式 sys.path 注入 + runpy 执行 main.py。

        gr.Progress 桩（/autoplan 2026-10-14 翻唱实跑发现）：py39 runtime 的
        gradio 3.14.0 无 Progress（3.20+ 才有）——main.py:247 默认参数在 import
        时求值即炸。CLI 模式 progress 恒 no-op（gradio UI 进度对子进程无用），
        在 runpy 前注入桩：tqdm 透传迭代器、其余调用返回 None。不改上游 main.py
        （apps/aicovergen/src 与上游逐字节一致的承诺保持）。"""
        src = self._aicovergen_src().replace("\\", "/")
        return (
            "import sys, runpy\n"
            f"sys.path.insert(0, r'{src}')\n"
            "if '--' in sys.argv:\n"
            "    sys.argv = ['main.py'] + sys.argv[sys.argv.index('--') + 1:]\n"
            "else:\n"
            "    sys.argv = ['main.py'] + sys.argv[1:]\n"
            "try:\n"
            "    import gradio as _gr\n"
            "    if not hasattr(_gr, 'Progress'):\n"
            "        class _NoopProgress:\n"
            "            def __init__(self, *a, **k): pass\n"
            "            def __call__(self, *a, **k): return self\n"
            "            def tqdm(self, it=None, *a, **k): return it if it is not None else []\n"
            "            def __getattr__(self, n):\n"
            "                def _f(*a, **k): return None\n"
            "                return _f\n"
            "        _gr.Progress = _NoopProgress\n"
            "except ImportError:\n"
            "    pass\n"
            f"runpy.run_path(r'{src}/main.py', run_name='__main__')\n"
        )

    def _find_produced_wav(self, job: Dict) -> Optional[str]:
        """AICoverGen 产出定位：优先成品名（"{歌名} ({模型} Ver).wav"，C-1 兜底），
        回落 mtime 最新 wav。中间产物（*_p{pitch}_i{rate}_*.wav）不含 " Ver)."，
        成品名优先可避免命中未混音的 RVC 中间干人声（/autoplan 2026-10-14 勘察 bug）。"""
        out_root = os.path.join(self._aicovergen_src(), "..", "song_output")
        if not os.path.isdir(out_root):
            return None
        finals, fallback, best_mtime = [], None, 0
        for dirpath, _, filenames in os.walk(out_root):
            for fn in filenames:
                if not fn.endswith(".wav"):
                    continue
                full = os.path.join(dirpath, fn)
                m = os.path.getmtime(full)
                if m <= job.get("started_at", 0):
                    continue
                if " Ver)." in fn:
                    finals.append((m, full))
                elif m > best_mtime:
                    fallback, best_mtime = full, m
        if finals:
            finals.sort(reverse=True)
            return finals[0][1]
        return fallback
