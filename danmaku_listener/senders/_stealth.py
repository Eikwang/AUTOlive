"""headless 反检测指纹包——STEALTH_JS 7 信号伪装（单源）

虎牙 2026-10-09 对抗实验实证方案：风控识别 headless=new 指纹并静默吞弹幕——
headed 对照可过，注入此 7 信号伪装（webdriver 抹除、outerWidth/outerHeight、
screen 可视区+色深、window.chrome 对象、plugins/languages、permissions.query
覆写）后 headless 亦过（即虎牙 2026-10-10 纯后台终态方案）；快手 2026-10-13
用户 headed 可见窗诊断实证同根因。

2026-10-14 ship D5 用户批准：原 dom_transient.py 与 senders/huya.py 双副本
（「互不耦合」注释）统一为单源——dom_transient re-export 保持 kuaishou_douyu
import 链不变；huya 直接 import 本模块。
"""

STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(window, 'outerWidth', {get: () => 1280});
    Object.defineProperty(window, 'outerHeight', {get: () => 800});
    Object.defineProperty(screen, 'availWidth', {get: () => 1280});
    Object.defineProperty(screen, 'availHeight', {get: () => 760});
    Object.defineProperty(screen, 'colorDepth', {get: () => 24});
    Object.defineProperty(screen, 'pixelDepth', {get: () => 24});
    if (!window.chrome) { window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}}; }
    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) => p.name === 'notifications'
        ? Promise.resolve({state: Notification.permission}) : origQuery(p);
}
"""
