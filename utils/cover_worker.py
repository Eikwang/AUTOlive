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
        """翻唱子进程解释器（S-2 显式钉住）：默认=外部树 py3.9 嵌入式 runtime
        （AICoverGen 依赖全齐的唯一 runtime；不得从 rvc_root 派生——S-1 根翻转
        apps/rvc 后派生值指向不存在的 apps/rvc/runtime，/autoplan outside 发现 3）。"""
        v = self.config.get("rvc_vc", "runtime_python")
        if v and str(v).strip():
            return str(v).strip()
        return r"D:\AI\RVC20240604Nvidia\runtime\python.exe"

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
            proc.kill() if hasattr(self, "proc") and self.proc else None
            self._fail(job, policies, timeout=True)
            return
        except FileNotFoundError as _sp_err:
            logger.error(f"[cover] 主跑解释器/命令不可达：{_sp_err}；cmd[0]={cmd[0]}")
            self._fail(job, policies)
            return
        logger.info(f"[cover] 主跑结束 rc={rc}：{song}（输出: {run_log}）")
        if rc != 0:
            try:
                tail = open(run_log, encoding="utf-8", errors="replace").read()[-800:]
                logger.error("[cover] 主跑失败尾部输出:\n" + tail)
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
        if rc != 0 or not produced:
            # 后处理补刀（/autoplan 2026-10-14 翻唱实跑发现）：py39 runtime 下
            # torch 加载后 pedalboard C++ 同进程崩溃（rc=139，三次复现）——主跑
            # 死在效果链时 RVC 中产物已在，用干净进程（零 torch）补 effects+mix。
            self._postprocess_fallback(job)
            produced = self._find_produced_wav(job)
        if rc == 0 and produced:
            tmp = cover + ".tmp.wav"
            shutil.copy2(produced, tmp)
            os.replace(tmp, cover)
            logger.info(f"[cover] 完成: {song} → {cover}")
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

    def _retain_split_tracks(self, job: Dict, cover: str):
        """R6 分轨保留：从 song_output 定位本次任务的 ai_vocals（RVC 人声）与
        *_Instrumental.wav（伴奏），复制为 covers/<成品base>_vocal.wav / _inst.wav。
        定位失败（上游产物名变化）仅告警不失败——播放侧自动降级整轨成品。"""
        try:
            out_root = os.path.join(self._aicovergen_src(), "..", "song_output")
            if not os.path.isdir(out_root):
                return
            started = job.get("started_at", 0)
            vocal = inst = None
            for dirpath, _, filenames in os.walk(out_root):
                for fn in filenames:
                    if not fn.endswith(".wav"):
                        continue
                    full = os.path.join(dirpath, fn)
                    if os.path.getmtime(full) <= started:
                        continue
                    low = fn.lower()
                    if "mixed" in low or " ver)." in low or "_inst.wav" in low or "_vocal.wav" in low:
                        continue
                    if low.endswith("_instrumental.wav") and inst is None:
                        inst = full
                    elif "_p" in fn and "_i" in fn and vocal is None:
                        m = os.path.getmtime(full)
                        if vocal is None or m > os.path.getmtime(vocal):
                            vocal = full
            base = os.path.splitext(cover)[0]
            if vocal and os.path.isfile(vocal):
                shutil.copy2(vocal, base + "_vocal.wav")
            if inst and os.path.isfile(inst):
                shutil.copy2(inst, base + "_inst.wav")
            if vocal and inst:
                logger.info(f"[cover] 分轨保留: {base}_vocal.wav + _inst.wav")
            else:
                logger.warning(f"[cover] 分轨定位不全（vocal={vocal is not None}, inst={inst is not None}），播放将走整轨降级")
        except Exception:
            logger.error("[cover] 分轨保留异常（不影响成品）", exc_info=True)

    def _postprocess_fallback(self, job: Dict):
        """效果链+混音补刀（零 torch 干净进程）：主跑崩在 effects 段时，
        ai_vocals 中产物已落盘 song_output——本方法用 pedalboard+pydub 直接
        补齐 add_audio_effects+combine_audio（逻辑对齐 main.py:213-240，
        参数同默认值）。成功=成品原子落位 covers/（finder 可命中）。

        T3/R-1（2026-11-02 r4 批次，spec R1 修正口径+CEO C1/C4+Eng A-1/E-1/E-2/E-3）：
        定位单源 _locate_cover_sources；CEO C1 半成品防御；Eng E-2 argv 传参；
        Eng E-3 原子落位；R-2 补刀对称挂 faulthandler。"""
        located = self._locate_cover_sources(job)
        if located is None:
            return
        vocal, backup, inst, work_dir = located
        final = f"{os.path.splitext(os.path.basename(job['song_path']))[0]} (autolive_{job['profile_id']} Ver).wav"
        final_path = os.path.join(work_dir, final)
        final_tmp = final_path + ".tmp.wav"
        code = self._postprocess_code()
        env = os.environ.copy()
        env.update(FAULTHANDLER_ENV)  # R-2/CEO C2：补刀对称挂
        try:
            r = subprocess.run(
                [self._rt_python(), "-c", code, "--", vocal, backup, inst, final_tmp],
                capture_output=True, text=True, timeout=600, encoding="utf-8",
                errors="replace", env=env, cwd=self._aicovergen_src(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            tail = ((r.stdout or "") + (r.stderr or ""))[-800:]  # Eng E-4：对齐主跑
            if r.returncode == 0 and os.path.isfile(final_tmp) \
                    and os.path.getsize(final_tmp) > 1024:  # Eng E-3：防空壳
                os.replace(final_tmp, final_path)
                logger.info(f"[cover] 补刀完成: {final_path}")
            else:
                logger.error(f"[cover] 补刀失败（rc={r.returncode}）尾部: {tail}")
                if os.path.isfile(final_tmp):
                    os.remove(final_tmp)  # 半成品 final 不入 covers（Eng E-3）
        except Exception:
            logger.error("[cover] 补刀异常", exc_info=True)
            if os.path.isfile(final_tmp):
                try:
                    os.remove(final_tmp)
                except OSError:
                    pass

    def _locate_cover_sources(self, job):
        """补刀三件套定位单源（T3/R-1，Eng A-1：命名约定单点；纯后缀匹配）。

        spec R1 修正口径：旧 rsplit("_p",1) 被 protect 段 _pro0.33 截断（_pro 的
        _p 比变调 _p0 更靠右）→base 反推必错；且 <rvc_stem>_Instrumental.wav
        AICoverGen 从不生成（combine 的伴奏=song_dir 的 MDX 原始伴奏）。
        纯后缀匹配天然绕开 stem 反推+覆盖 mono _stereo 变体；walk 全 song_output
        +started_at 守卫+参数链正则（Eng E-1：不受歌名污染/mtime 影响）定位 vocal。

        CEO C1：定位**前**先删主跑残留 {stem}_mixed.wav 半成品（崩在 effects
        写中的部分文件被补刀当已完成混入=坏成品事故）。

        Returns: (vocal, backup, inst, work_dir) 或 None（缺任一=明确日志）。"""
        song_root = os.path.join(self._aicovergen_root(), "song_output")
        if not os.path.isdir(song_root):
            logger.warning("[cover] 补刀中止：song_output 不存在")
            return None
        # CEO C1：删主跑残留半成品 mixed——**全量删**（R-5：AICoverGen 缓存复用
        # 语义下 started_at 守卫会漏删旧半成品；mixed 全部为可重生产物，删安全）
        for dirpath, _, fns in os.walk(song_root):
            for fn in fns:
                if fn.endswith("_mixed.wav"):
                    full = os.path.join(dirpath, fn)
                    try:
                        os.remove(full)
                        logger.warning(f"[cover] 已删除残留半成品: {full}（CEO C1）")
                    except OSError:
                        pass
        # vocal：参数链正则（Eng E-1 首选）+**无 started_at 守卫**（R-5 真根因修：
        # AICoverGen 同曲重翻时 song_dir 缓存命中→跳过 MDX/RVC→主跑直接崩在
        # effects→本轮"新增产物 0 个"——旧 vocal 是缓存语义下正确应复用的产物，
        # mtime 守卫会把它排除=补刀恒中止。参数链正则已足够精准：文件名含
        # 模型名+全参数链，同歌同参=同一 vocal 文件，跨参数/跨歌不冲突）。
        # 最新者胜：缓存场景多轮同参产物相同，取最新保持确定性。
        vocal, vocal_m = None, 0
        for dirpath, _, fns in os.walk(song_root):
            for fn in fns:
                if not fn.endswith(".wav") or "mixed" in fn.lower() \
                        or fn.endswith("_Instrumental.wav") or fn.endswith("_inst.wav") \
                        or fn.endswith("_vocal.wav") or fn.endswith("_Vocals_Backup.wav"):
                    continue
                if not _VOCAL_PARAM_RE.search(fn):
                    continue
                full = os.path.join(dirpath, fn)
                m = os.path.getmtime(full)
                if m > vocal_m:
                    vocal, vocal_m = full, m
        if not vocal:
            logger.warning("[cover] 补刀中止：未找到 RVC 中产物（参数链签名无命中）")
            return None
        work_dir = os.path.dirname(vocal)
        # 伴奏/备份：纯后缀匹配（vocal 同目录=hash 隔离防串歌；词序=Vocals_Backup）
        inst = backup = None
        for fn in os.listdir(work_dir):
            full = os.path.join(work_dir, fn)
            if fn.endswith("_Instrumental.wav") and inst is None:
                inst = full
            elif fn.endswith("_Vocals_Backup.wav") and backup is None:
                backup = full
        if not inst or not backup or not os.path.isfile(inst) or not os.path.isfile(backup):
            logger.warning(f"[cover] 补刀中止：缺伴奏/备份轨（inst={inst is not None}, "
                           f"backup={backup is not None}）——song_dir={work_dir}")
            return None
        return vocal, backup, inst, work_dir

    def _postprocess_code(self) -> str:
        """零 torch 后处理代码串（Eng E-2：路径经 sys.argv 传入——r''' 内插在
        歌名含连续单引号/尾随反斜杠时静默死）：效果链（高通+压缩+混响，
        main.py:217-223 同参）+三轨混音（main -4dB/backup -6dB/inst -7dB，
        main.py:236-240 同语义）。final 由父进程 .tmp 落位+os.replace 原子化
        （Eng E-3）；effects 强制重写（CEO C1：父侧已删半成品 mixed）。"""
        return """
import os, sys
ai_vocals, backup, inst, final = sys.argv[1:5]
from pedalboard import Pedalboard, HighpassFilter, Compressor, Reverb
from pedalboard.io import AudioFile
from pydub import AudioSegment
mixed = os.path.splitext(ai_vocals)[0] + '_mixed.wav'
if os.path.isfile(mixed):
    os.remove(mixed)  # CEO C1：主跑残留半成品强制重写
board = Pedalboard([HighpassFilter(), Compressor(ratio=4, threshold_db=-15),
                    Reverb(room_size=0.15, dry_level=0.8, wet_level=0.2, damping=0.7)])
with AudioFile(ai_vocals) as f, AudioFile(mixed, 'w', f.samplerate, f.num_channels) as o:
    while f.tell() < f.frames:
        chunk = f.read(int(f.samplerate))
        o.write(board(chunk, f.samplerate, reset=False))
main_v = AudioSegment.from_wav(mixed) - 4
back_v = AudioSegment.from_wav(backup) - 6
inst_v = AudioSegment.from_wav(inst) - 7
main_v.overlay(back_v).overlay(inst_v).export(final, format='wav')
print('POSTPROCESS_OK')
"""

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
