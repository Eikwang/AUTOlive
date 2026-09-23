# -*- coding: UTF-8 -*-
"""
设计令牌模块
统一定义前端所有视觉元素的设计变量：颜色、字体、间距、圆角、阴影。
通过 CSS 自定义属性（CSS Variables）注入页面，Python 端提供常量供组件引用。

设计规范：
- 主色调：专业蓝色（传达"工具"属性，避免装饰性渐变）
- 字号层级：页面标题 20px > 卡片标题 16px > 正文 14px > 辅助文字 12px
- 输入框宽度三档：sm=150px / md=250px / lg=400px
- 主题切换：通过 body--dark class 覆盖 CSS 变量实现
"""
from typing import Dict

# ============================================================
# 字体系统
# ============================================================
FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", Roboto, "Helvetica Neue", Arial, sans-serif'
)

FONT_SIZE_PAGE_TITLE = "16px"    # 三级页面标题（D-B3 v2：与二级标题统一 16px，页级靠标题带背景区分）
FONT_SIZE_CARD_TITLE = "16px"    # 卡片标题（配置分组）
FONT_SIZE_SECTION_TITLE = "16px"  # 二级列表头标题（D-B3 v2：与三级页标题统一 16px）
FONT_SIZE_BODY = "14px"          # 正文 / 列表项
FONT_SIZE_FIELD_LABEL = "14px"   # 参数标题（表单字段 label，与正文同大小保证可读性）
FONT_SIZE_SMALL = "12px"         # 辅助说明文字
FONT_SIZE_TINY = "11px"          # 徽章等极小文字
FONT_SIZE_BANNER = "20px"        # 导航栏品牌横幅（D-B2 v2：AUTOlive）

FONT_WEIGHT_TITLE = "600"
FONT_WEIGHT_HEAVY = "800"        # 品牌横幅重字重（D-B2 v2：工业感全大写+宽字距）
FONT_WEIGHT_EMPHASIS = "500"
FONT_WEIGHT_NORMAL = "400"

# ============================================================
# 输入框宽度规范（三档）
# ============================================================
INPUT_WIDTH_SM = "150px"   # 数字、短枚举
INPUT_WIDTH_MD = "250px"   # 常规文本
INPUT_WIDTH_LG = "400px"   # 路径、URL、长文本

# ============================================================
# 间距规范
# ============================================================
SPACING_XS = "4px"
SPACING_SM = "8px"
SPACING_MD = "16px"
SPACING_LG = "24px"
SPACING_XL = "32px"

# ============================================================
# 圆角规范
# ============================================================
RADIUS_SM = "4px"
RADIUS_MD = "8px"
RADIUS_LG = "12px"

# ============================================================
# 阴影规范
# ============================================================
SHADOW_SM = "0 1px 3px rgba(0, 0, 0, 0.08)"
SHADOW_MD = "0 2px 8px rgba(0, 0, 0, 0.10)"
SHADOW_LG = "0 4px 16px rgba(0, 0, 0, 0.15)"

# ============================================================
# 过渡
# ============================================================
TRANSITION_FAST = "0.15s ease"
TRANSITION_NORMAL = "0.25s ease"

# ============================================================
# 主题颜色令牌（浅色 / 深色）
# ============================================================

# 浅色主题
LIGHT_TOKENS: Dict[str, str] = {
    # 主色（专业蓝）
    "color-primary": "#2563eb",
    "color-primary-hover": "#1d4ed8",
    "color-primary-soft": "#eff6ff",

    # 语义色
    "color-success": "#16a34a",
    "color-danger": "#dc2626",
    "color-warning": "#d97706",
    "color-info": "#0284c7",

    # 灰阶
    "gray-50": "#f9fafb",
    "gray-100": "#f3f4f6",
    "gray-200": "#e5e7eb",
    "gray-300": "#d1d5db",
    "gray-400": "#9ca3af",
    "gray-500": "#6b7280",
    "gray-600": "#4b5563",
    "gray-700": "#374151",
    "gray-800": "#1f2937",
    "gray-900": "#111827",

    # 表面色
    "bg-page": "#f5f6f8",          # 页面背景
    "bg-card": "#ffffff",           # 卡片背景
    "bg-hover": "#f0f4ff",          # 悬停背景
    "bg-selected": "#e8effd",       # 选中背景
    "border-color": "#e5e7eb",      # 边框
    "divider-color": "#f0f1f3",     # 分割线

    # 文字色
    "text-title": "#111827",        # 标题
    "text-body": "#374151",         # 正文
    "text-secondary": "#6b7280",    # 次要文字
    "text-hint": "#9ca3af",         # 提示文字
    "text-on-primary": "#ffffff",   # 主色按钮上的文字
}

# 深色主题（覆盖值）
DARK_TOKENS: Dict[str, str] = {
    "color-primary": "#3b82f6",
    "color-primary-hover": "#60a5fa",
    "color-primary-soft": "#1e293b",

    "color-success": "#22c55e",
    "color-danger": "#ef4444",
    "color-warning": "#f59e0b",
    "color-info": "#38bdf8",

    "gray-50": "#1a202c",
    "gray-100": "#1e242f",
    "gray-200": "#2a3140",
    "gray-300": "#3a4252",
    "gray-400": "#6b7280",
    "gray-500": "#9ca3af",
    "gray-600": "#b8bfc9",
    "gray-700": "#d1d5db",
    "gray-800": "#e5e7eb",
    "gray-900": "#f3f4f6",

    "bg-page": "#111318",
    "bg-card": "#1c2028",
    "bg-hover": "#242a36",
    "bg-selected": "#1e293b",
    "border-color": "#2a3140",
    "divider-color": "#232936",

    "text-title": "#f3f4f6",
    "text-body": "#d1d5db",
    "text-secondary": "#9ca3af",
    "text-hint": "#6b7280",
    "text-on-primary": "#ffffff",
}


def build_css_variables(tokens: Dict[str, str]) -> str:
    """
    将令牌字典转换为 CSS 变量声明块。

    Args:
        tokens: 令牌字典

    Returns:
        形如 "--name: value;\\n" 的 CSS 片段
    """
    return "\n".join(f"    --{name}: {value};" for name, value in tokens.items())


def inject_design_tokens() -> str:
    """
    生成设计令牌的完整 <style> 内容。
    包含浅色变量（:root）和深色覆盖（body--dark）。

    Returns:
        可注入 <style> 标签的 CSS 字符串
    """
    return f"""
    :root {{
{build_css_variables(LIGHT_TOKENS)}

        /* 字体 */
        --font-family: {FONT_FAMILY};
        --font-size-page-title: {FONT_SIZE_PAGE_TITLE};
        --font-size-card-title: {FONT_SIZE_CARD_TITLE};
        --font-size-section-title: {FONT_SIZE_SECTION_TITLE};
        --font-size-body: {FONT_SIZE_BODY};
        --font-size-field-label: {FONT_SIZE_FIELD_LABEL};
        --font-size-small: {FONT_SIZE_SMALL};
        --font-size-tiny: {FONT_SIZE_TINY};
        --font-size-banner: {FONT_SIZE_BANNER};

        /* 字重 */
        --font-weight-title: {FONT_WEIGHT_TITLE};
        --font-weight-heavy: {FONT_WEIGHT_HEAVY};
        --font-weight-emphasis: {FONT_WEIGHT_EMPHASIS};
        --font-weight-normal: {FONT_WEIGHT_NORMAL};

        /* 间距 */
        --spacing-xs: {SPACING_XS};
        --spacing-sm: {SPACING_SM};
        --spacing-md: {SPACING_MD};
        --spacing-lg: {SPACING_LG};
        --spacing-xl: {SPACING_XL};

        /* 圆角 */
        --radius-sm: {RADIUS_SM};
        --radius-md: {RADIUS_MD};
        --radius-lg: {RADIUS_LG};

        /* 阴影 */
        --shadow-sm: {SHADOW_SM};
        --shadow-md: {SHADOW_MD};
        --shadow-lg: {SHADOW_LG};

        /* 过渡 */
        --transition-fast: {TRANSITION_FAST};
        --transition-normal: {TRANSITION_NORMAL};

        /* 输入框宽度档位 */
        --input-width-sm: {INPUT_WIDTH_SM};
        --input-width-md: {INPUT_WIDTH_MD};
        --input-width-lg: {INPUT_WIDTH_LG};
    }}

    /* 深色主题覆盖 */
    body.body--dark {{
{build_css_variables(DARK_TOKENS)}
    }}
"""


# 统一标题样式（供 Python 端 style() 引用，与 CSS 变量保持同步）
def title_style(level: str = "card") -> str:
    """
    生成统一标题样式的内联 style 字符串。

    Args:
        level: 标题级别
            - "page": 三级页面标题
            - "card": 卡片标题
            - "section": 分组标题
            - "item": 列表项标题

    Returns:
        CSS 样式字符串
    """
    styles = {
        "page": (
            f"font-size: var(--font-size-page-title); "
            f"font-weight: var(--font-weight-title); "
            f"color: var(--text-title);"
        ),
        "card": (
            f"font-size: var(--font-size-card-title); "
            f"font-weight: var(--font-weight-title); "
            f"color: var(--text-title); "
            f"margin-bottom: var(--spacing-sm);"
        ),
        "section": (
            f"font-size: var(--font-size-section-title); "
            f"font-weight: var(--font-weight-title); "
            f"color: var(--text-title);"
        ),
        "item": (
            f"font-size: var(--font-size-body); "
            f"font-weight: var(--font-weight-emphasis); "
            f"color: var(--text-body);"
        ),
    }
    return styles.get(level, styles["card"])
