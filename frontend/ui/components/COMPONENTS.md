# UI组件文档

## 组件列表

### 1. NavigationTabs (导航标签页)
**文件**: `navigation.py`

提供标签页导航和页面切换功能。

**主要方法**:
- `create_tabs()`: 创建标签页导航
- `create_tab_panels()`: 创建标签页面板
- `get_tab(name)`: 获取标签页
- `get_tab_panel(name)`: 获取标签页面板

**使用示例**:
```python
from frontend.ui.components import NavigationTabs

navigation = NavigationTabs(config)
tabs = navigation.create_tabs()
panels = navigation.create_tab_panels()
```

### 2. ThemeManager (主题管理器)
**文件**: `theme.py`

提供主题切换、暗黑模式等功能。只支持浅色和深色模式。

**主要方法**:
- `init_dark_mode()`: 初始化暗黑模式
- `toggle_dark_mode()`: 切换暗黑模式
- `set_theme(theme_name)`: 设置主题 ("light" 或 "dark")
- `get_css(element)`: 获取元素的CSS样式

**使用示例**:
```python
from frontend.ui.components import ThemeManager

theme_manager = ThemeManager(config)
dark_mode = theme_manager.init_dark_mode()
theme_manager.toggle_dark_mode()
```

### 3. SearchFilter (搜索过滤)
**文件**: `search.py`

提供搜索、过滤和筛选功能。

**主要方法**:
- `create_search_input(...)`: 创建搜索输入框
- `create_filter_switch(...)`: 创建过滤开关
- `create_filter_select(...)`: 创建过滤下拉框
- `get_search_value()`: 获取搜索值
- `get_filter_values()`: 获取所有过滤器的值
- `create_filter_group(title, filters)`: 创建过滤器组

**使用示例**:
```python
from frontend.ui.components import SearchFilter

search = SearchFilter(config)
search_input = search.create_search_input()
filter_switch = search.create_filter_switch('enable', '启用过滤')
```

### 4. ControlButtons (控制按钮)
**文件**: `control_buttons.py`

提供底部控制按钮组（保存、运行、停止等）。

**主要方法**:
- `create_button(name, text, ...)`: 创建控制按钮
- `create_save_button(on_click)`: 创建保存配置按钮
- `create_run_button(on_click)`: 创建运行按钮
- `create_stop_button(on_click)`: 创建停止按钮
- `create_light_button(on_click)`: 创建关灯按钮
- `create_restart_button(on_click)`: 创建重启按钮
- `create_scroll_top_button(on_click)`: 创建回到顶部按钮
- `create_bottom_bar(...)`: 创建底部控制栏

**使用示例**:
```python
from frontend.ui.components import ControlButtons

control = ControlButtons(config)
control.create_bottom_bar(
    on_save=save_config,
    on_run=run_program,
    on_stop=stop_program,
    on_light=toggle_light,
    on_restart=restart_app
)
```

### 5. LoginForm (登录表单)
**文件**: `login.py`

提供用户登录功能。

**主要方法**:
- `create_login_form(on_login, on_forget_password)`: 创建登录表单
- `get_username()`: 获取用户名
- `get_password()`: 获取密码
- `validate_input()`: 验证输入
- `clear_form()`: 清空表单
- `delete_form()`: 删除表单元素
- `show_success(message)`: 显示登录成功
- `show_error(message)`: 显示登录错误

**使用示例**:
```python
from frontend.ui.components import LoginForm

login_form = LoginForm(config)
login_form.create_login_form(
    on_login=handle_login,
    on_forget_password=handle_forget_password
)
```

### 6. ExpansionPanel (折叠面板)
**文件**: `expansion.py`

提供可折叠的内容区域。

**主要方法**:
- `create(name, title, ...)`: 创建折叠面板
- `create_with_card(name, title, ...)`: 创建带卡片的折叠面板
- `get_panel(name)`: 获取折叠面板
- `toggle_panel(name)`: 切换折叠面板状态
- `expand_all()`: 展开所有面板
- `collapse_all()`: 折叠所有面板

**使用示例**:
```python
from frontend.ui.components import ExpansionPanel

expansion = ExpansionPanel(config)
expansion.create('settings', '设置', content_func=create_settings_content)
```

## 组件依赖关系

所有组件都依赖于配置字典（config），用于获取主题和样式配置。

## 主题配置

组件使用以下主题配置项：
- `tab_panel`: 标签页面板样式
- `card`: 卡片样式
- `button_bottom`: 底部按钮样式
- `button_bottom_color`: 底部按钮颜色
- `button_internal`: 内部按钮样式
- `button_internal_color`: 内部按钮颜色
- `switch_internal`: 内部开关样式
- `echart`: 图表样式
- `login_card`: 登录卡片样式