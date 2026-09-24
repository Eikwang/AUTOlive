/* 字幕显示页逻辑（移植自 captions_printer js/index.js 渲染子集，按整合计划改造：
   - S3 渲染安全：文本 HTML 转义 + 仅换行转 <br>（源 innerHTML 直插是 XSS 向量）
   - show_mode 由 config 决定（源 bug：radio DOM 决定不随配置同步）
   - D1 初始同步：connect 时服务端 emit 全量配置（含 enable）——OBS 刷新即恢复样式
   - dx1 断线自检：disconnect 页内显示"字幕服务未连接"
   - E-6：enable=false 显示"字幕功能未开启"
   - D10 透明语义：body_bg_color 空/none/transparent → background: transparent
   - dx8 节流参数读 config（单一事实源） */

let currentAnimation = null;
let isHiding = false;
let currentTimeout = null;
let hideSubtitle_Timeout = null;

// 配置驱动的渲染参数（config_update / connect 初始同步刷新）
let config = {
    show_mode: '1',
    hide_time: 1000,
    show_over_hide_time: 2000,
    single_char_show_time: 80,
    gradient_show_time: 500,
    subtitle_font_family: 'Microsoft YaHei',
    subtitle_font_size: 24,
    subtitle_font_weight: 'bold',
    subtitle_webkit_text_stroke: 0,
    subtitle_bg_width: 400,
    subtitle_bg_height: 60,
    bg_color: 'rgba(0,0,0,0.6)',
    font_color: '#FFFFFF',
    body_bg_color: 'none',
    enable: false
};

function applyTransparent(el, colorValue) {
    // D10：空值/none/transparent → 透明（OBS 叠加场景必须可表达透明）
    const v = String(colorValue || '').trim().toLowerCase();
    if (v === '' || v === 'none' || v === 'transparent') {
        el.style.background = 'transparent';
        el.style.backgroundColor = 'transparent';
    } else {
        el.style.background = colorValue;
    }
}

function applyConfig(cfg) {
    if (!cfg) return;
    config = Object.assign(config, cfg);

    const subtitleDiv = document.getElementById('subtitle');
    const bgDiv = document.getElementById('subtitle_bg');
    subtitleDiv.style.fontFamily = config.subtitle_font_family;
    subtitleDiv.style.fontSize = config.subtitle_font_size;
    subtitleDiv.style.fontWeight = config.subtitle_font_weight;
    subtitleDiv.style.color = config.font_color;
    subtitleDiv.style.webkitTextStroke = String(config.subtitle_webkit_text_stroke) + 'px black';
    bgDiv.style.width = config.subtitle_bg_width;
    bgDiv.style.minHeight = config.subtitle_bg_height;
    applyTransparent(bgDiv, config.bg_color);
    applyTransparent(document.body, config.body_bg_color);

    // E-6：enable=false 明示（页面已连接但不会有字幕推送）
    const status = document.getElementById('conn_status');
    if (!config.enable && socket && socket.connected) {
        status.innerText = '字幕功能未开启（请在 AUTOlive 基础功能-web字幕打印机 中启用）';
        status.style.display = 'block';
    } else if (socket && socket.connected) {
        status.style.display = 'none';
    }
}

// S3：文本转义 + 仅换行转 <br>
function renderContent(raw) {
    const escaped = String(raw)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    return escaped.replace(/\r?\n/g, '<br>');
}

function clearSubtitle() {
    document.getElementById('subtitle').innerText = '';
}

function hideSubtitle(fadeOutDuration) {
    if (isHiding) return;
    isHiding = true;
    const subtitleDiv = document.getElementById('subtitle');
    subtitleDiv.style.transition = 'opacity ' + fadeOutDuration + 'ms';
    subtitleDiv.style.opacity = 0;
    setTimeout(() => {
        clearSubtitle();
        isHiding = false;
    }, fadeOutDuration);
}

// 渐显模式（config.show_mode === '1'）
function show1(text, keep_time) {
    const subtitleDiv = document.getElementById('subtitle');
    subtitleDiv.innerHTML = text;
    subtitleDiv.style.opacity = 0;
    subtitleDiv.style.transition = 'opacity ' + (config.gradient_show_time / 1000) + 's ease-in';
    setTimeout(() => { subtitleDiv.style.opacity = 1; }, 100);

    const delay = keep_time === 0
        ? config.show_over_hide_time + config.gradient_show_time
        : keep_time;
    hideSubtitle_Timeout = setTimeout(() => hideSubtitle(config.hide_time), delay);
}

// 打字机模式（config.show_mode === '2'）
function show2(text, keep_time) {
    const subtitleDiv = document.getElementById('subtitle');
    subtitleDiv.innerHTML = '';
    subtitleDiv.style.opacity = 1;

    const parts = text.split(/(<br>)/);
    let partIndex = 0;

    function showNextPart() {
        if (partIndex >= parts.length) {
            const totalTime = keep_time === 0
                ? config.show_over_hide_time + config.single_char_show_time * text.length
                : keep_time;
            hideSubtitle_Timeout = setTimeout(() => hideSubtitle(config.hide_time), totalTime);
            return;
        }
        const part = parts[partIndex];
        if (part === '<br>') {
            subtitleDiv.innerHTML += part;
            partIndex++;
            showNextPart();
            return;
        }
        const chars = part.split('');
        let charIndex = 0;
        function showNextChar() {
            if (charIndex < chars.length) {
                subtitleDiv.innerHTML += chars[charIndex];
                charIndex++;
                currentTimeout = setTimeout(showNextChar, config.single_char_show_time);
            } else {
                partIndex++;
                showNextPart();
            }
        }
        showNextChar();
    }
    showNextPart();
}

function showSubtitle(data) {
    const text = renderContent(data.content);
    clearSubtitle();
    if (currentTimeout) { clearTimeout(currentTimeout); currentTimeout = null; }
    if (currentAnimation) { cancelAnimationFrame(currentAnimation); }
    if (hideSubtitle_Timeout) { clearTimeout(hideSubtitle_Timeout); }

    let keep_time = 0;
    if ('keep_time' in data) keep_time = parseInt(data.keep_time) || 0;

    if (String(config.show_mode) === '2') show2(text, keep_time);
    else show1(text, keep_time);
}

function showMessage(data) {
    showSubtitle(data);
}

// 同源连接（页面与 API 同服务，无需 host:port；path 对应 /captions_ws 挂载）
const socket = io({ path: '/captions_ws/socket.io' });

socket.on('connect', () => {
    document.getElementById('conn_status').style.display = 'none';
    if (!config.enable) {
        const status = document.getElementById('conn_status');
        status.innerText = '字幕功能未开启（请在 AUTOlive 基础功能-web字幕打印机 中启用）';
        status.style.display = 'block';
    }
});

socket.on('disconnect', () => {
    // dx 发现1：断线自检——OBS 源里直接可见
    const status = document.getElementById('conn_status');
    status.innerText = '字幕服务未连接';
    status.style.display = 'block';
});

socket.on('message', (data) => showMessage(data));

// D1：connect 时服务端 emit 全量配置（含 enable）；此后增量广播
socket.on('config_update', (data) => applyConfig(data));
