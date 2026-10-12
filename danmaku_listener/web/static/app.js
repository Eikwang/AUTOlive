/**
 * DanmakuListener 测试控制台 v2 — 契约 v1 前端
 *
 * 消息处理：category 分流（business 八类徽章 / system 八种告警与指示）
 * 统计：按类型计数、msg/s（10s 滚动窗口）、按房间分组、引擎标识
 * 观测：TTHW、消息 timestamp 与本地时钟差
 */

// 九类徽章色板（与 style.css 变量对应）
const TYPE_COLORS = {
  DANMU: "var(--type-danmu)",
  GIFT: "var(--type-gift)",
  SUPER_CHAT: "var(--type-super-chat)",
  ENTER_ROOM: "var(--type-enter)",
  LIKE: "var(--type-like)",
  LIVE_STATUS_CHANGE: "var(--type-live)",
  ROOM_STATS: "var(--type-stats)",
  SOCIAL: "var(--type-social)",
};

const BUSINESS_TYPES = Object.keys(TYPE_COLORS);
const SYSTEM_TYPES = [
  "HEARTBEAT", "ROOM_STATUS", "GAP", "NEEDS_LOGIN",
  "ENGINE_STATUS", "BACKPRESSURE", "ROUTE_FAILED", "RECOVERED",
];

// 平台中文名（2026-10-05 R4："[平台]内容"显示格式）
const PLATFORM_NAMES = {
  douyin: "抖音", douyu: "斗鱼", bilibili: "B站", huya: "虎牙",
  kuaishou: "快手", taobao: "淘宝", "1688": "1688", meituan: "美团",
  xiaohongshu: "小红书", pdd: "拼多多", jd: "京东", wechat_channels: "视频号",
};

// SOCIAL 动作中文名
const ACTION_NAMES = { follow: "关注", share: "分享", favorite: "关注" };

// 房间状态中文名
const STATUS_NAMES = {
  running: "监听中", stopped: "已停止", login_required: "待登录",
  login_failed: "登录失败", error: "异常",
};

// 管线拒绝码中文映射（contract/models.py 词表；映射表未覆盖的码直接展示原码——spec 评审）
const SEND_REASON_NAMES = {
  AUTH_UNCONFIGURED: "鉴权未配置",
  SENDER_DISABLED: "平台未启用",
  CIRCUIT_OPEN: "熔断开启",
  ROOM_NOT_LISTENED: "房间未在监听",
  RATE_LIMITED: "限速冷却中",
  RATE_COOLDOWN: "冷却等待",
  DUPLICATE: "重复内容",
  TOO_LONG: "内容超长",
  KEYWORD_BLOCKED: "命中关键词拦截",
  PLATFORM_REJECTED: "平台拒绝",
  SEND_TIMEOUT: "发送超时",
  AUDIT_UNAVAILABLE: "审计不可用",
  ROUTE_UNVERIFIED: "路线未验证",
  SENDER_UNAVAILABLE: "发送器不可用",
  IDEMPOTENT_REPLAY: "幂等重放（回执上次结果）",
};

// 前端发送中止上限（T4 提取，原两处 60000 字面量；CEO-1 预算契约：后端四阶段 ≤55s，留 5s 余量）
const SEND_TIMEOUT_MS = 60000;
// 中止后对账宽限（T4/评审 maint#5 提取——与 runbook QA 第 9 项及 tests/js 断言联动）
const RECONCILE_GRACE_MS = 2000;

// 直播间链接识别（与后端 platform_parser.LINK_PATTERNS 对齐；仅做平台预选，
// 后端为解析权威；未收录平台链接由用户手动选平台后直接粘贴）
const LINK_PATTERNS = [
  ["douyin", /live\.douyin\.com\/(\d+)/],
  ["douyu", /douyu\.com\/(\d+)/],
  ["bilibili", /live\.bilibili\.com\/(\d+)/],
  ["huya", /huya\.com\/(\d+)/],
  ["jd", /zhibo\.jd\.com\/liveroom\?[^\s]*liveId=(\d+)/],
  ["1688", /live\.1688\.com\/zb\/play\.html\?[^\s]*feedId=(\d+)/],
];

// 三区路由（2026-10-05 R4：左=弹幕 / 右上=礼物点赞关注 / 右下=入场房间信息）
const ROUTING = {
  DANMU: "danmaku-container",
  GIFT: "interactive-container",
  SUPER_CHAT: "interactive-container",
  LIKE: "interactive-container",
  SOCIAL: "interactive-container",
  ENTER_ROOM: "info-container",
  ROOM_STATS: "info-container",
  LIVE_STATUS_CHANGE: "info-container",
};

// 各容器消息上限
const MAX_ITEMS = {
  "danmaku-container": 400,
  "interactive-container": 200,
  "info-container": 200,
};

class DanmakuApp {
  constructor() {
    this.ws = null;
    this.reconnectTimer = null;
    this.reconnectDelay = 3000;
    this.autoScroll = true;

    // 契约统计
    this.typeCounts = {};        // type -> count（business）
    this.roomCounts = {};        // room_id -> count
    this.roomSeq = {};           // room_id -> last seq（连续性观察）
    this.rateWindow = [];        // 最近 10s 时间戳
    this.currentEngine = "—";
    this.typeFilter = new Set(); // 空集合=全部显示
    this._lastStatsSig = {};     // 房间统计同值去重（在线统一口径，2026-10-05）
    this.sendMaxLength = 0;      // D-G：/api/config send_max_length（0=未读取不设 maxlength）

    this.init();
  }

  init() {
    this.connectWebSocket();
    this.bindEvents();
    this.loadInitialState();
    this.renderTypeFilter();
    this.renderPlatformSelect();
    // msg/s 滚动窗口刷新（10s 窗口，每秒重算）
    setInterval(() => this.updateRate(), 1000);
  }

  // 选项式添加（2026-10-05 R2）：平台下拉 + 房间号/链接输入
  renderPlatformSelect() {
    const select = document.getElementById("platform-select");
    if (!select) return;
    for (const [key, name] of Object.entries(PLATFORM_NAMES)) {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = name;
      select.appendChild(opt);
    }
  }

  // 粘贴链接时自动选中平台（仅预选，后端为解析权威）
  detectPlatformFromLink() {
    const input = document.getElementById("room-input");
    const select = document.getElementById("platform-select");
    const text = (input?.value || "").trim();
    if (!text || !text.includes("://")) return;
    for (const [platform, pattern] of LINK_PATTERNS) {
      if (pattern.test(text)) {
        select.value = platform;
        return;
      }
    }
  }

  connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${location.host}/ws`;
    try {
      this.ws = new WebSocket(wsUrl);
      this.ws.onopen = () => this.setStatus("connected");
      this.ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
      this.ws.onclose = () => {
        this.setStatus("disconnected");
        this.reconnectTimer = setTimeout(() => this.connectWebSocket(), this.reconnectDelay);
      };
      this.ws.onerror = () => this.setStatus("error");
    } catch (e) {
      this.setStatus("error");
    }
  }

  setStatus(state) {
    const dot = document.getElementById("status-indicator");
    const text = document.getElementById("status-text");
    dot.className = "status-dot " + (state === "connected" ? "connected" : state === "error" ? "error" : "disconnected");
    text.textContent = state === "connected" ? "已连接" : state === "error" ? "连接错误" : "未连接（重连中…）";
  }

  // ===== 契约消息处理核心 =====

  handleMessage(msg) {
    if (!msg || !msg.category) return; // additive-only：无 category 的未知消息忽略

    if (msg.category === "business") {
      this.handleBusiness(msg);
    } else if (msg.category === "system") {
      this.handleSystem(msg);
    } else {
      // 未知 category：忽略并计数（additive-only 消费语义）
      console.warn("unknown category", msg.category);
    }
    this.updateStats(msg);
  }

  handleBusiness(msg) {
    const type = msg.type;
    this.typeCounts[type] = (this.typeCounts[type] || 0) + 1;
    if (this.typeFilter.size && !this.typeFilter.has(type)) return; // 过滤 chips

    const p = msg.payload || {};
    let content = "";
    let extra = "";
    switch (type) {
      case "DANMU":
        content = `${p.user_name}：${p.content}`;
        break;
      case "GIFT":
        content = `${p.user_name} 送出 ${p.gift_name} x${p.gift_count}`;
        extra = p.gift_value ? ` (价值 ${p.gift_value})` : "";
        break;
      case "SUPER_CHAT":
        content = `SC ¥${p.price} ${p.user_name}：${p.content}`;
        break;
      case "ENTER_ROOM":
        content = `${p.user_name} 进入直播间`;
        break;
      case "LIKE":
        content = `${p.user_name || "有人"} 点赞 x${p.count}`;
        break;
      case "LIVE_STATUS_CHANGE":
        content = p.live ? "直播开始" : "直播结束";
        break;
      case "ROOM_STATS": {
        // 在线统一口径（2026-10-05 用户裁定：在线/观看不分成两类——
        // online_count 优先（抖音 RoomUserSeq），viewer_count 作为无在线
        // 口径平台的回退（淘宝/1688 观看数）；同房间同值去重，抖音双源
        // （RoomUserSeq 在线 + RoomStats 累计）合并为一条流）
        const parts = [];
        const online = p.online_count ?? p.viewer_count;
        const like = p.like_count ?? p.total_likes;
        if (online != null) parts.push(`在线 ${online}`);
        if (like != null) parts.push(`点赞 ${like}`);
        if (!parts.length) break;
        const statsKey = `${msg.platform}:${msg.room_id}`;
        const statsSig = parts.join(" · ");
        if (this._lastStatsSig[statsKey] === statsSig) break; // 同值不重复显示
        this._lastStatsSig[statsKey] = statsSig;
        content = statsSig;
        break;
      }
      case "SOCIAL":
        content = `${p.user_name} ${ACTION_NAMES[p.action] || p.action || ""}`;
        break;
      default:
        content = JSON.stringify(p); // 未知类型透传（additive-only）
    }

    // "[平台]内容" 显示格式（2026-10-05 R4）；空内容不渲染（v10 兜底——
    // 统计帧解析失败等场景不再出现空行，2026-10-05 快手实测）
    if (!content && !extra) return;
    const pname = PLATFORM_NAMES[msg.platform] || msg.platform;
    const clockSkew = Math.abs(Math.floor(Date.now() / 1000) - msg.timestamp);
    const skewNote = clockSkew > 5 ? ` (时钟差${clockSkew}s)` : "";
    this.appendMessage(msg, `[${pname}]${content}${extra}`, skewNote);
  }

  handleSystem(msg) {
    const type = msg.type;
    const p = msg.payload || {};

    switch (type) {
      case "HEARTBEAT":
        // 心跳只在统计面板体现（不打进弹幕流）
        this.refreshEngineDisplay();
        return;
      case "GAP":
        this.showAlert(
          "warn",
          `数据缺口 ${new Date(p.window_start * 1000).toLocaleTimeString()} ~ ` +
          `${new Date(p.window_end * 1000).toLocaleTimeString()}（${p.reason}${p.approx ? ", 近似" : ""}` +
          `${p.dropped_estimate != null ? `, 疑似丢失 ${p.dropped_estimate} 条` : ""}）`
        );
        this.appendGapMarker(p);
        break;
      case "NEEDS_LOGIN": {
        const f = p.failure || {};
        this.showAlert(
          "login",
          `需要登录：${f.fix_hint || ""}（${f.reason_code}）`,
          f.docs_anchor
        );
        const il = p.interactive_login;
        if (il && il.qr_image_b64) {
          this.showQr(il.qr_image_b64);
        } else if (il && il.login_url) {
          this.showAlert("login", `登录链接：<a href="${il.login_url}" target="_blank">${il.login_url}</a>`);
        }
        break;
      }
      case "ENGINE_STATUS":
        if (p.engine) this.currentEngine = p.engine;
        if (p.detail) this.showAlert("info", `引擎状态：${p.detail}`);
        this.refreshEngineDisplay();
        break;
      case "BACKPRESSURE": {
        const dropped = Object.entries(p.dropped_by_type || {})
          .map(([t, n]) => `${t}:${n}`).join(", ");
        this.showAlert("warn", `背压丢弃（${new Date(p.window_start * 1000).toLocaleTimeString()} 窗口）：${dropped}`);
        break;
      }
      case "ROUTE_FAILED": {
        const f = p.failure || {};
        const link = f.docs_anchor
          ? ` <a href="/static/${f.docs_anchor}" target="_blank">手册</a>`
          : "";
        this.showAlert("route", `路线失效 ${f.reason_code}：${f.fix_hint}.${link}`);
        break;
      }
      case "RECOVERED":
        this.showAlert("ok", `引擎已恢复（故障持续 ${p.after_seconds}s）`);
        break;
      case "LIVE_STATUS_CHANGE":
        // 直播状态变化（开播/下播）——观看/点赞数据由 ROOM_STATS 承载
        this.showAlert(p.live ? "ok" : "info",
                       `直播状态：${p.live ? "直播中" : "未直播"}（raw=${p.raw_status}）`);
        break;
      default:
        this.showAlert("info", `未知系统消息 ${type}（additive-only，已忽略载荷）`);
    }
    this.refreshEngineDisplay();
  }

  // ===== 渲染 =====

  appendMessage(msg, text, skewNote = "") {
    // 三区路由（2026-10-05 R4）：弹幕/互动/信息各自独立滚动与上限
    const containerId = ROUTING[msg.type] || "info-container";
    const container = document.getElementById(containerId);
    // 首条消息移除该区空态（v7 C1：三区各有空态）
    const empty = document.getElementById(containerId === "danmaku-container" ? "empty-state"
      : containerId === "interactive-container" ? "empty-interactive" : "empty-info");
    empty?.remove();

    const item = document.createElement("div");
    item.className = "danmaku-item";
    if (msg.type !== "DANMU") {
      // 互动/信息区行首色点（弹幕区整区皆 DANMU，不加点）
      const dot = document.createElement("span");
      dot.className = "type-dot";
      dot.style.background = TYPE_COLORS[msg.type] || "var(--text-secondary)";
      dot.title = msg.type;
      item.appendChild(dot);
    }
    const body = document.createElement("span");
    body.className = "danmaku-text";
    body.textContent = text + skewNote;
    item.appendChild(body);

    // HH:MM 时间戳（v7 C3：区分消息批次）
    const time = document.createElement("span");
    time.className = "msg-time";
    const d = new Date((msg.timestamp || Math.floor(Date.now() / 1000)) * 1000);
    time.textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    item.appendChild(time);

    container.appendChild(item);
    const maxItems = MAX_ITEMS[containerId] || 200;
    while (container.children.length > maxItems) {
      container.removeChild(container.firstChild);
    }
    if (this.autoScroll) container.scrollTop = container.scrollHeight;
  }

  appendGapMarker(gapPayload) {
    const container = document.getElementById("danmaku-container");
    const marker = document.createElement("div");
    marker.className = "gap-marker";
    marker.textContent = `── 缺口 ${gapPayload.reason}（${gapPayload.window_start} ~ ${gapPayload.window_end}）──`;
    container.appendChild(marker);
    if (this.autoScroll) container.scrollTop = container.scrollHeight;
  }

  showAlert(level, html, link = null) {
    const bar = document.getElementById("alert-bar");
    const item = document.createElement("div");
    item.className = `alert alert-${level}`;
    item.innerHTML = `<span>${html}${link ? ` <a href="/static/${link}" target="_blank">手册</a>` : ""}</span>`;
    const close = document.createElement("button");
    close.className = "alert-close";
    close.textContent = "×";
    close.setAttribute("aria-label", "关闭告警");
    close.onclick = () => item.remove();
    item.appendChild(close);
    bar.prepend(item);
    while (bar.children.length > 5) bar.removeChild(bar.lastChild);
  }

  showQr(b64) {
    const bar = document.getElementById("alert-bar");
    const item = document.createElement("div");
    item.className = "alert alert-login qr";
    const img = document.createElement("img");
    img.src = `data:image/png;base64,${b64}`;
    img.alt = "登录二维码";
    img.width = 160;
    item.appendChild(img);
    const note = document.createElement("span");
    note.textContent = "用微信扫码重新登录管理后台";
    item.appendChild(note);
    bar.prepend(item);
  }

  // ===== 统计 =====

  updateStats(msg) {
    this.roomCounts[`${msg.platform}:${msg.room_id}`] =
      (this.roomCounts[`${msg.platform}:${msg.room_id}`] || 0) + 1;
    if (msg.category === "business") {
      this.rateWindow.push(Date.now());
    }
    // seq 连续性观察（按房间）
    if (msg.category === "business" && typeof msg.seq === "number") {
      const key = msg.room_id;
      const last = this.roomSeq[key];
      if (last != null && msg.seq > last + 1) {
        console.warn(`seq gap on ${key}: ${last} -> ${msg.seq}（等待 GAP 确认）`);
      }
      this.roomSeq[key] = msg.seq;
    }
    this.renderStats();
  }

  updateRate() {
    const now = Date.now();
    this.rateWindow = this.rateWindow.filter((t) => now - t < 10000);
    const rate = (this.rateWindow.length / 10).toFixed(1);
    document.getElementById("stats-rate").textContent = `${rate} msg/s`;
    this.refreshEngineDisplay();
  }

  refreshEngineDisplay() {
    document.getElementById("stats-engine").textContent = this.currentEngine;
  }

  renderStats() {
    const typesEl = document.getElementById("stats-types");
    typesEl.innerHTML = "";
    for (const [type, count] of Object.entries(this.typeCounts)) {
      const row = document.createElement("div");
      row.className = "stats-row";
      const dot = document.createElement("span");
      dot.className = "type-badge";
      dot.textContent = type;
      dot.style.background = TYPE_COLORS[type] || "var(--text-secondary)";
      const num = document.createElement("span");
      num.className = "stats-value";
      num.textContent = count;
      row.append(dot, num);
      typesEl.appendChild(row);
    }

    const roomsEl = document.getElementById("stats-rooms");
    roomsEl.innerHTML = "";
    for (const [room, count] of Object.entries(this.roomCounts)) {
      const row = document.createElement("div");
      row.className = "stats-row";
      row.innerHTML = `<span class="stats-label">${room}</span><span class="stats-value">${count}</span>`;
      roomsEl.appendChild(row);
    }
  }

  renderTypeFilter() {
    const filter = document.getElementById("type-filter");
    for (const type of BUSINESS_TYPES) {
      const chip = document.createElement("button");
      chip.className = "filter-chip active";
      chip.textContent = type;
      chip.style.borderColor = TYPE_COLORS[type];
      chip.onclick = () => {
        if (this.typeFilter.has(type)) {
          this.typeFilter.delete(type);
          chip.classList.add("active");
        } else {
          this.typeFilter.add(type);
          chip.classList.remove("active");
        }
      };
      filter.appendChild(chip);
    }
  }

  // ===== 既有交互（房间/代理/关键词，保留） =====

  bindEvents() {
    document.getElementById("add-room-btn")?.addEventListener("click", () => this.addRoom());
    document.getElementById("room-input")?.addEventListener("keypress", (e) => {
      if (e.key === "Enter") this.addRoom();
    });
    document.getElementById("room-input")?.addEventListener("input", () => this.detectPlatformFromLink());
    document.getElementById("stop-all-btn")?.addEventListener("click", () => this.stopAll());
    document.getElementById("scroll-toggle-btn")?.addEventListener("click", (e) => {
      this.autoScroll = !this.autoScroll;
      e.target.textContent = this.autoScroll ? "暂停滚动" : "恢复滚动";
    });
    document.getElementById("add-keyword-btn")?.addEventListener("click", () => this.addKeyword());
    document.getElementById("settings-toggle-btn")?.addEventListener("click", (e) => {
      const body = document.getElementById("settings-body");
      const hidden = body.classList.toggle("hidden");
      e.target.textContent = hidden ? "展开" : "收起";
      e.target.setAttribute("aria-expanded", String(!hidden));
    });
    document.getElementById("save-config-btn")?.addEventListener("click", () => this.saveConfig());
    document.getElementById("keyword-toggle")?.addEventListener("change", (e) => {
      this.api("/api/keywords/toggle", "PUT", { enabled: e.target.checked }).then(() => this.loadKeywords());
    });
  }

  async api(path, method = "GET", body = null) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(path, opts);
    return resp.json();
  }

  async addRoom() {
    const platform = document.getElementById("platform-select")?.value || "";
    const input = document.getElementById("room-input");
    const errEl = document.getElementById("room-error");
    errEl.classList.add("hidden");
    try {
      // 选项模式（2026-10-05 R2）：{platform, room}；room 可为纯房号或链接
      const data = await this.api("/api/rooms", "POST", { platform, room: input.value.trim() });
      if (data.status === "login_required") {
        errEl.textContent = data.message || "需要登录：请在弹出的浏览器窗口中登录账号";
        errEl.classList.remove("hidden");
        errEl.classList.add("login-hint");
        this.loadRooms(); // 待登录房间已入列表（保存），刷新显示状态
        return; // 登录完成后引擎自动开始监听
      }
      input.value = "";
      this.loadRooms();
    } catch (e) {
      errEl.textContent = "添加失败";
      errEl.classList.remove("hidden");
    }
  }

  // 单房间启动/停止（2026-10-05 R3：action = "start" | "stop"）
  // room_id 编码进路径（防御：链接型 room_id 含 : / 不编码则路由被斜杠打散）
  async toggleRoom(platform, roomId, action) {
    await this.api(`/api/rooms/${platform}/${encodeURIComponent(roomId)}/${action}`, "POST");
    this.loadRooms();
  }

  async stopAll() {
    await this.api("/api/rooms/stop-all", "POST");
    this.loadRooms();
  }

  async addKeyword() {
    const input = document.getElementById("keyword-input");
    if (!input.value) return;
    await this.api("/api/keywords", "POST", { keyword: input.value });
    input.value = "";
    this.loadKeywords();
  }

  async loadConfig() {
    try {
      const data = await this.api("/api/config");
      for (const [key, value] of Object.entries(data.config || {})) {
        const input = document.getElementById(`cfg-${key}`);
        if (input) input.value = value;
      }
      // D-G：内容长度上限（读不到/非法不设，由守卫 TOO_LONG 回执兜底）
      const maxLen = Number((data.config || {}).send_max_length);
      this.sendMaxLength = Number.isFinite(maxLen) && maxLen > 0 ? maxLen : 0;
      const src = document.getElementById("data-source");
      if (src && data.source === "config.local.toml") src.textContent = "配置: config.local.toml";
    } catch (e) { /* config 可选 */ }
  }

  async saveConfig() {
    const msg = document.getElementById("settings-msg");
    const fields = ["ws_port", "web_port", "max_rooms", "fast_retry_max",
                    "slow_retry_cap_seconds", "bus_ring_capacity",
                    "bus_dedup_window_seconds", "session_lifetime_seconds"];
    const config = {};
    for (const f of fields) {
      const el = document.getElementById(`cfg-${f}`);
      if (el && el.value !== "") config[f] = Number(el.value);
    }
    try {
      const resp = await this.api("/api/config", "PUT", { config });
      if (resp.success) {
        msg.textContent = "已保存（需重启服务生效）";
        msg.className = "settings-msg ok";
      } else {
        msg.textContent = resp.error || "保存失败";
        msg.className = "settings-msg err";
      }
    } catch (e) {
      msg.textContent = `保存失败: ${e}`;
      msg.className = "settings-msg err";
    }
    msg.classList.remove("hidden");
  }

  async loadRooms() {
    const data = await this.api("/api/rooms");
    const list = document.getElementById("room-list");
    list.innerHTML = "";
    // 2026-10-05 F1.1 修复：rooms 为数组（api_get_rooms 返回 values() 列表），
    // 旧实现按对象遍历致列表显示索引、删除按钮解析错误
    const rooms = data.rooms || [];
    for (const r of rooms) {
      const li = document.createElement("li");
      li.className = "room-item";

      // 平台徽章 + 房号 + 状态
      const name = document.createElement("span");
      name.className = "room-name";
      const pname = PLATFORM_NAMES[r.platform] || r.platform;
      const tag = document.createElement("span");
      tag.className = "platform-tag";
      tag.textContent = pname;
      const rid = document.createElement("span");
      rid.textContent = r.room_id;
      const badge = document.createElement("span");
      badge.className = `status-badge ${r.status || "stopped"}`;
      badge.textContent = STATUS_NAMES[r.status] || r.status || "";
      name.append(tag, rid, badge);

      // 启停/删除/发送/登录 四宫格（2026-10-10 用户裁定：四按钮 2×2 对齐）
      const running = r.status === "running";
      const toggle = document.createElement("button");
      toggle.textContent = running ? "停止" : "启动";
      toggle.className = running ? "room-stop-btn" : "room-toggle-btn";
      toggle.setAttribute("aria-label", `${running ? "停止" : "启动"} ${r.platform}:${r.room_id}`);
      toggle.onclick = () => this.toggleRoom(r.platform, r.room_id, running ? "stop" : "start");
      const del = document.createElement("button");
      del.textContent = "删除";
      del.className = "room-del-btn";
      del.setAttribute("aria-label", `移除 ${r.platform}:${r.room_id}`);
      del.onclick = async () => {
        await this.api(`/api/rooms/${r.platform}/${encodeURIComponent(r.room_id)}`, "DELETE");
        this.loadRooms();
      };

      // 上行=身份/状态（操作按钮全部移入下方宫格）
      const mainRow = document.createElement("div");
      mainRow.className = "room-main-row";
      mainRow.append(name);

      const grid = document.createElement("div");
      grid.className = "room-grid";
      const sendInput = document.createElement("input");
      sendInput.className = "room-send-input";
      sendInput.type = "text";
      sendInput.placeholder = "发送弹幕到该房间";
      sendInput.setAttribute("aria-label", `发送弹幕到 ${pname}:${r.room_id}`);
      if (this.sendMaxLength > 0) sendInput.maxLength = this.sendMaxLength; // D-G
      const sendBtn = document.createElement("button");
      sendBtn.textContent = "发送";
      sendBtn.className = "room-send-btn";
      sendBtn.setAttribute("aria-label", `发送弹幕到 ${pname}:${r.room_id}`);
      const sendResult = document.createElement("div");
      sendResult.className = "room-send-result";
      sendResult.setAttribute("role", "status");
      sendResult.setAttribute("aria-live", "polite");

      // D-B 按钮禁用三态：非 running（baseDisabled）/ 空输入 / 发送中（sendFromRoom 置位）
      sendBtn.dataset.baseDisabled = String(r.status !== "running");
      if (sendBtn.dataset.baseDisabled === "true") {
        sendBtn.disabled = true;
        sendBtn.title = "房间未在监听中";
      }
      sendInput.addEventListener("input", () => {
        sendBtn.disabled = sendBtn.dataset.baseDisabled === "true" || sendInput.value.trim() === "";
      });
      sendInput.addEventListener("keydown", (e) => {
        if (e.isComposing) return; // IME 组合输入中 Enter=确认候选词（评审 P1：中文输入误触发发送）
        if (e.key === "Enter" && !sendBtn.disabled) {
          this.sendFromRoom(r.platform, r.room_id, sendInput, sendBtn, sendResult);
        } else if (e.key === "Escape") {
          sendInput.value = ""; // Escape 清空（设计评审 P6）
          // 编程赋值不触发 input 事件——同步按钮态（评审 P2-1）
          sendBtn.disabled = sendBtn.dataset.baseDisabled === "true";
        }
      });
      sendBtn.addEventListener("click", () =>
        this.sendFromRoom(r.platform, r.room_id, sendInput, sendBtn, sendResult));

      // 手动重新登录（2026-10-10 用户需求）：清平台登录载体→既有登录门接管弹窗
      const loginBtn = document.createElement("button");
      loginBtn.textContent = "登录";
      loginBtn.className = "room-login-btn";
      loginBtn.title = "清除该平台登录状态并弹出登录窗口重新登录";
      loginBtn.setAttribute("aria-label", `重新登录 ${pname}:${r.room_id}`);
      loginBtn.addEventListener("click", () =>
        this.reloginRoom(r.platform, r.room_id, loginBtn, sendResult));

      grid.append(sendInput, toggle, del, sendBtn, loginBtn);
      li.append(mainRow, grid, sendResult);
      list.appendChild(li);
    }
    document.getElementById("stop-all-btn").disabled = rooms.length === 0;
  }

  async loadKeywords() {
    const data = await this.api("/api/keywords");
    const list = document.getElementById("keyword-list");
    list.innerHTML = "";
    for (const kw of data.keywords || []) {
      const li = document.createElement("li");
      li.textContent = kw;
      const del = document.createElement("button");
      del.textContent = "×";
      del.className = "room-del";
      del.onclick = async () => {
        await this.api(`/api/keywords/${encodeURIComponent(kw)}`, "DELETE");
        this.loadKeywords();
      };
      li.appendChild(del);
      list.appendChild(li);
    }
    const toggle = document.getElementById("keyword-toggle");
    if (toggle) toggle.checked = data.enabled !== false;
  }

  async loadInitialState() {
    // 先 config 后 rooms：send_max_length 就位后首渲染即有 maxlength（D-G）
    await this.loadConfig();
    await this.loadRooms();
    await this.loadKeywords();
    try {
      const status = await this.api("/api/status");
      if (status.message_count != null) {
        document.getElementById("stats-rate").textContent = `累计 ${status.message_count} 条`;
      }
      this.renderSendStatus(status.send);
    } catch (e) { /* status 可选 */ }
  }

  renderSendStatus(send) {
    // T7 day-1 状态行：开关/dry-run/熔断三态（S8-1）
    const el = document.getElementById("send-status-line");
    if (!el) return;
    if (!send) { el.textContent = "发送：未配置（[send] send_enabled_platforms 为空）"; return; }
    const parts = [];
    for (const [plat, st] of Object.entries(send.platforms || {})) {
      parts.push(plat + (st.enabled ? (st.circuit_open ? "⚠熔断" : "✓") : "✗"));
    }
    el.textContent = "发送：" + (parts.length ? parts.join(" / ") : "全部关闭")
      + " · " + (send.dry_run ? "dry-run 开（只记录不实发）" : "实发模式");
  }

  // 公共 token 流（D-D：抽取自 sendTestDanmu 既有语义；prompt 取消/空 → null 不发请求）
  getSendToken() {
    let token = localStorage.getItem("send_token");
    if (token) return token;
    token = window.prompt("输入发送 token（persistence_data/send_token.txt 内容）:") || "";
    if (!token.trim()) return null;
    localStorage.setItem("send_token", token.trim());
    return token.trim();
  }

  // 行内结果反馈（D-C）：一律 textContent（XSS 卫生，评审 S5）；三态语义色
  setRoomSendResult(resultEl, text, kind, title = "") {
    if (!resultEl.isConnected) {
      // 列表重绘后行节点已游离（对抗评审 F1）：降级写入发送状态行，回执不静默丢失
      const line = document.getElementById("send-status-line");
      if (line) line.textContent = "发送：最近一次回执 → " + text;
      return;
    }
    resultEl.textContent = text;
    resultEl.className = "room-send-result" + (kind ? " " + kind : "");
    resultEl.title = title;
  }

  // 房间行内发送（T3）：AbortController 60s（D-E）+ request_id 随机后缀（spec 1.3b）
  // + 全分支反馈（D-C）+ 完成仅更新状态行不重绘列表（spec 2.2）+ 焦点回位（连续发送）
  // 手动重新登录（2026-10-10）：清载体→既有登录门弹窗；鉴权/反馈复用行内发送模式
  async reloginRoom(platform, roomId, btnEl, resultEl) {
    const token = this.getSendToken();
    if (!token) {
      this.setRoomSendResult(resultEl, "✗ 未提供 token，未执行", "error");
      return;
    }
    if (!window.confirm(`将清除 ${platform} 的登录状态（profile/cookie）并弹出登录窗口，确认继续？`)) {
      return;
    }
    const prevLabel = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = "登录中…";
    try {
      const resp = await fetch("/api/relogin", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
        body: JSON.stringify({ platform, room_id: roomId }),
      });
      if (resp.status === 401) {
        localStorage.removeItem("send_token");
        this.setRoomSendResult(resultEl, "✗ token 无效——已清除，请重试", "error");
        return;
      }
      const data = await resp.json();
      if (data.success) {
        this.setRoomSendResult(resultEl, "↻ " + (data.msg || "登录窗口已弹出——请完成登录，登录后自动恢复监听"), "success");
      } else {
        this.setRoomSendResult(resultEl, `✗ ${data.error || "重新登录失败"}`, "error");
      }
    } catch (e) {
      this.setRoomSendResult(resultEl, `✗ 重新登录请求失败: ${e}`, "error");
    } finally {
      btnEl.disabled = false;
      btnEl.textContent = prevLabel;
    }
  }

  async sendFromRoom(platform, roomId, inputEl, btnEl, resultEl) {
    const content = inputEl.value.trim();
    if (!content || btnEl.disabled) return;
    const token = this.getSendToken();
    if (!token) {
      this.setRoomSendResult(resultEl, "✗ 未提供 token，未发送", "error");
      return;
    }
    const prevLabel = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = "发送中…";
    inputEl.disabled = true;
    const restore = () => {
      btnEl.disabled = btnEl.dataset.baseDisabled === "true" || inputEl.value.trim() === "";
      btnEl.textContent = prevLabel;
      inputEl.disabled = false;
    };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SEND_TIMEOUT_MS);
    const requestId = "ui-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
    try {
      const resp = await fetch("/api/send-danmu", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
        body: JSON.stringify({
          platform, room_id: roomId, content,
          request_id: requestId,
        }),
        signal: controller.signal,
      });
      if (resp.status === 401) {
        localStorage.removeItem("send_token");
        this.setRoomSendResult(resultEl, "✗ token 无效——已清除，请重试", "error");
        return;
      }
      let p = {};
      let errBody = null;
      try {
        const respData = await resp.json();
        p = (respData.result || {}).payload || {};
        if (!resp.ok) errBody = respData; // 503/400 直返 {error, fix_hint}（非包裹路径，评审 P2-2）
      } catch (e) {
        if (e.name === "AbortError" || controller.signal.aborted) {
          // 中止后对账（T4/CEO-5）：后端可能在中止后才出结果——单次查询兜底回显
          await this.reconcileAfterAbort(resultEl, inputEl, requestId);
          return;
        }
        this.setRoomSendResult(resultEl, `✗ 服务错误（HTTP ${resp.status}）`, "error");
        return;
      }
      if (!resp.ok) {
        const name = SEND_REASON_NAMES[errBody?.error] || errBody?.error || "服务错误";
        this.setRoomSendResult(resultEl, `✗ ${name}（HTTP ${resp.status}）`, "error",
          [errBody?.error, errBody?.fix_hint].filter(Boolean).join(" · "));
        return;
      }
      if (p.status === "sent") {
        const ts = typeof p.sent_at === "number" ? p.sent_at * 1000
          : (p.sent_at ? Date.parse(p.sent_at) : Date.now());
        const d = Number.isFinite(ts) ? new Date(ts) : new Date();
        const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        this.setRoomSendResult(resultEl, `✓ 已发送 ${hhmm}`, "success");
        inputEl.value = "";
        inputEl.focus(); // 发送成功后焦点回位（连续发送）
      } else if (p.status === "dry_run") {
        this.setRoomSendResult(resultEl, "○ dry-run 已记录（未实发）", "dryrun");
      } else if (p.status === "unknown") {
        // ？第三态（评审 ASK-C/redteam#4）：结果未知≠确定失败——弹幕可能已落地，
        // 盲重会制造平台侧真重复（DUPLICATE 影子源）
        this.setRoomSendResult(resultEl, "？结果未知（可能已送达）——请对账/回环确认后再重试", "unknown",
          [p.reason_code, p.fix_hint, "重试前先按 request_id 对账或查看弹幕流回环"].filter(Boolean).join(" · "));
      } else {
        const name = SEND_REASON_NAMES[p.reason_code] || p.reason_code || "未知错误";
        const code = SEND_REASON_NAMES[p.reason_code] ? `（${p.reason_code}）` : "";
        const title = [p.reason_code, p.fix_hint, p.docs_anchor].filter(Boolean).join(" · ");
        this.setRoomSendResult(resultEl, `✗ ${name}${code}`, "error", title);
      }
    } catch (e) {
      if (e.name === "AbortError") {
        // 中止后对账（T4/CEO-5）：同上——fetch 层中止的单次兜底查询
        await this.reconcileAfterAbort(resultEl, inputEl, requestId);
      } else {
        this.setRoomSendResult(resultEl, "✗ 网络错误（无法连接服务）", "error");
      }
    } finally {
      clearTimeout(timer);
      restore();
      // 完成后仅更新发送状态行，不重绘房间列表（spec 评审 2.2——保护行内结果）
      try {
        const status = await this.api("/api/status");
        this.renderSendStatus(status.send);
      } catch (e) { /* status 可选 */ }
    }
  }

  // 中止后对账（T4/CEO-5）：后端可能在中止后才出结果（慢页/网络抖动）——
  // 宽限 2s 后按 request_id 单次查询 /api/send-results，把真实结果回显到行内；
  // 查不到则回落「发送超时 · 结果待对账」（后端 T2 围栏保证结果终会落审计）
  async reconcileAfterAbort(resultEl, inputEl, requestId) {
    await new Promise((resolve) => setTimeout(resolve, RECONCILE_GRACE_MS)); // 宽限：给后端收尾窗口
    const token = localStorage.getItem("send_token");
    if (!token) {
      this.setRoomSendResult(resultEl,
        `✗ 发送超时（超过 ${SEND_TIMEOUT_MS / 1000}s）· 结果待对账`, "error",
        "结果未即时可得：稍后按 request_id 查询 GET /api/send-results 对账");
      return;
    }
    try {
      // 对账 GET 也必须有界（对抗评审 F1）：无超时的 fetch 会在服务停滞时把
      // 房间行锁死在「发送中…」——正是本分支要消灭的挂点形态在前端的复活
      const resp = await fetch("/api/send-results?request_id=" + encodeURIComponent(requestId),
        { headers: { "Authorization": "Bearer " + token }, signal: AbortSignal.timeout(5000) });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const body = await resp.json();
      const row = body && body.found ? body.result : null;
      if (!row) throw new Error("not reconciled yet");
      if (row.status === "sent") {
        const ts = typeof row.sent_at === "number" ? row.sent_at * 1000 : Date.now();
        const d = new Date(Number.isFinite(ts) ? ts : Date.now());
        const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        this.setRoomSendResult(resultEl, `✓ 已发送 ${hhmm}（对账回显）`, "success");
        if (inputEl) { inputEl.value = ""; inputEl.focus(); }
      } else if (row.status === "dry_run") {
        this.setRoomSendResult(resultEl, "○ dry-run 已记录（对账回显）", "dryrun");
      } else if (row.status === "unknown") {
        // ？第三态（评审 ASK-C）：同直发路径——UNKNOWN 不得渲染为确定失败
        this.setRoomSendResult(resultEl, "？结果未知（可能已送达，对账回显）——请确认弹幕流后再重试", "unknown",
          [row.reason_code, row.fix_hint, "发送中止后的对账回显"].filter(Boolean).join(" · "));
      } else {
        const name = SEND_REASON_NAMES[row.reason_code] || row.reason_code || "未知错误";
        this.setRoomSendResult(resultEl, `✗ ${name}（对账回显）`, "error",
          [row.reason_code, row.fix_hint, "发送中止后的对账回显"].filter(Boolean).join(" · "));
      }
    } catch (e) {
      this.setRoomSendResult(resultEl,
        `✗ 发送超时（超过 ${SEND_TIMEOUT_MS / 1000}s）· 结果待对账`, "error",
        "结果未即时可得：稍后按 request_id 查询 GET /api/send-results 对账");
    }
  }
}

window.addEventListener("DOMContentLoaded", () => new DanmakuApp());
