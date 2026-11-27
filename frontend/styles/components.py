# -*- coding: UTF-8 -*-
"""
全局组件级样式层（发行改造阶段 B，2026-11-22）

职责：Quasar 基础组件（按钮/输入框/下拉/开关/滑块/表格/卡片/横幅/徽章）
的统一质感规范——令牌驱动（design_tokens.py CSS 变量），浅/深主题经
body--dark 自动切换，注入一次全 tab 生效，**不改任何 tab 业务代码**。

兼容性护栏：
- 现有 tab 内联 style 优先级高于本层（style attr > class 选择器），
  不破坏既有精确布局指令（一行四列等已验收布局）；
- 基础形态规则（.q-btn/.q-field 等既有全局规则）在 DrawerLayout.
  inject_global_styles 中先行注入，本层追加其后——同特异性后者生效，
  仅做增量补强（聚焦态/禁用态/弹层/表格等此前未覆盖面）。
- 变体类（.btn-secondary/.btn-ghost/.btn-danger）为 opt-in 工具类，
  存量 tab 未引用时零影响。
"""


def inject_component_styles() -> str:
    """生成全局组件层的 <style> 内容（追加于设计令牌之后注入）。"""
    return """
    /* ============ 按钮（阶段 B 组件层） ============ */
    /* 禁用态统一（对齐 DG-4 规范：降透明 + not-allowed；sys-btn 已有
       0.45 !important 特例，此处为全局兜底） */
    .q-btn.disabled, .q-btn.q-btn--disabled {
        opacity: 0.5;
        cursor: not-allowed;
        box-shadow: none !important;
    }
    /* 危险动作按钮（color="negative"）：悬停加深，语义一致 */
    .q-btn.bg-negative:not(.disabled):not(.q-btn--disabled):hover {
        filter: brightness(0.9);
    }
    /* 变体工具类（opt-in，存量 tab 未引用时零影响） */
    .btn-secondary {
        background: var(--color-primary-soft) !important;
        color: var(--color-primary) !important;
    }
    .btn-secondary:hover { filter: brightness(0.97); }
    .btn-ghost {
        background: transparent !important;
        color: var(--text-secondary) !important;
    }
    .btn-ghost:hover {
        background: var(--bg-hover) !important;
        color: var(--color-primary) !important;
    }
    .btn-danger {
        background: var(--color-danger) !important;
        color: var(--text-on-primary) !important;
    }
    .btn-danger:hover { filter: brightness(0.9); }

    /* ============ 输入框 ============ */
    /* 悬停边框加深 → 聚焦主色描边 + soft 光晕（聚焦态可见性护栏） */
    .q-field--outlined .q-field__control:before {
        border-color: var(--border-color);
        transition: border-color var(--transition-fast);
    }
    .q-field--outlined:not(.q-field--focused):not(.q-field--disabled):hover .q-field__control:before {
        border-color: var(--gray-400);
    }
    .q-field--outlined.q-field--focused .q-field__control:after {
        border-color: var(--color-primary);
        box-shadow: 0 0 0 3px var(--focus-ring);
    }
    /* filled 形态底色统一（bg-card 语义，深色自动切换） */
    .q-field--filled .q-field__control {
        background: var(--bg-hover);
    }
    /* 禁用态 */
    .q-field--disabled { opacity: 0.6; cursor: not-allowed; }
    /* 错误态提示文字统一危险色 */
    .q-field--error .q-field__messages { color: var(--color-danger); }

    /* ============ 下拉弹层 / 菜单 ============ */
    .q-menu {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-md);
        box-shadow: var(--shadow-md);
    }
    .q-menu .q-item {
        border-radius: var(--radius-sm);
        min-height: 34px;
    }
    .q-menu .q-item:hover:not(.q-item--active) { background: var(--bg-hover); }
    .q-menu .q-item--active {
        background: var(--color-primary-soft);
        color: var(--color-primary);
        font-weight: var(--font-weight-emphasis);
    }

    /* ============ 开关 / 复选 / 单选 ============ */
    /* 选中色统一主色（Quasar 默认即 primary，此处钉死防止个别未传 color 的
       组件回落 keep-color 灰调） */
    .q-checkbox__inner--truthy,
    .q-radio__inner--truthy,
    .q-toggle__inner--truthy .q-toggle__track {
        color: var(--color-primary);
    }
    .q-toggle__inner--truthy .q-toggle__track { opacity: 1; }
    .q-toggle__inner--falsy .q-toggle__track { opacity: 0.35; }

    /* ============ 滑块 / 数值输入 ============ */
    .q-slider__handle { transition: transform var(--transition-fast); }
    .q-slider__active { color: var(--color-primary); }

    /* ============ 表格 ============ */
    .q-table thead th {
        background: var(--gray-50);
        color: var(--text-title);
        font-weight: var(--font-weight-title);
        position: sticky;
        top: 0;
        z-index: 1;
    }
    .q-table tbody tr:hover td { background: var(--bg-hover); }
    .q-table__bottom { color: var(--text-secondary); }

    /* ============ 卡片 / 分隔线 ============ */
    .q-separator { background: var(--divider-color); }
    .q-banner {
        border-radius: var(--radius-md);
        background: var(--bg-card);
    }
    .q-badge {
        border-radius: var(--radius-sm);
        font-weight: var(--font-weight-emphasis);
    }

    /* ============ 横幅三态（warning/negative/info 统一质感） ============ */
    .q-banner--warning { background: var(--color-primary-soft); }
    .q-banner.negative, .q-banner--negative { background: var(--bg-card); }

    /* ============ 过渡统一 ============ */
    .q-item, .q-banner, .q-menu, .q-toggle__track {
        transition: background-color var(--transition-fast),
                    color var(--transition-fast);
    }
"""
