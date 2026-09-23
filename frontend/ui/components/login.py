"""
登录表单组件
提供用户登录功能
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable
from .config_helper import get_nested_value


class LoginForm:
    """登录表单组件"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化登录表单

        Args:
            config: 配置字典
        """
        self.config = config
        self.username_input = None
        self.password_input = None
        self.login_button = None
        self.forget_password_button = None
        self.login_column = None
        self.login_card = None
        self.label_login = None

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        theme_config = theme_manager.get_theme_config()
        self.login_card_css = theme_config.get("login_card", "")
    
    def create_login_form(
        self,
        on_login: Optional[Callable] = None,
        on_forget_password: Optional[Callable] = None
    ) -> ui.column:
        """
        创建登录表单
        
        Args:
            on_login: 登录按钮点击事件
            on_forget_password: 忘记密码按钮点击事件
            
        Returns:
            登录表单列
        """
        self.login_column = ui.column().style("width:100%;text-align: center;")
        
        with self.login_column:
            self.login_card = ui.card().style(self.login_card_css)
            
            with self.login_card:
                self.label_login = ui.label('AUTOlive').style(
                    "font-size: 30px;letter-spacing: 5px;color: #3b3838;font-weight: 800;text-transform: uppercase;"
                )
                
                self.username_input = ui.input(
                    label='用户名',
                    placeholder='您的账号，请找管理员申请',
                    value=""
                ).style("width:250px;")
                
                self.password_input = ui.input(
                    label='密码',
                    password=True,
                    placeholder='您的密码，请找管理员申请',
                    value=""
                ).style("width:250px;")
                
                self.login_button = ui.button(
                    '登录',
                    on_click=on_login
                ).style("width:250px;")
                
                self.forget_password_button = ui.button(
                    '忘记账号/密码怎么办？',
                    on_click=on_forget_password
                ).style("width:250px;")
        
        return self.login_column
    
    def get_username(self) -> str:
        """
        获取用户名
        
        Returns:
            用户名
        """
        if self.username_input:
            return self.username_input.value or ""
        return ""
    
    def get_password(self) -> str:
        """
        获取密码
        
        Returns:
            密码
        """
        if self.password_input:
            return self.password_input.value or ""
        return ""
    
    def validate_input(self) -> bool:
        """
        验证输入
        
        Returns:
            是否有效
        """
        username = self.get_username()
        password = self.get_password()
        
        if not username or not password:
            ui.notify(position="top", type="info", message="用户名或密码不能为空")
            return False
        
        return True
    
    def clear_form(self):
        """
        清空表单
        """
        if self.username_input:
            self.username_input.value = ""
        if self.password_input:
            self.password_input.value = ""
    
    def delete_form(self):
        """
        删除表单元素
        """
        if self.label_login:
            self.label_login.delete()
        if self.username_input:
            self.username_input.delete()
        if self.password_input:
            self.password_input.delete()
        if self.login_button:
            self.login_button.delete()
        if self.forget_password_button:
            self.forget_password_button.delete()
    
    def show_success(self, message: str):
        """
        显示登录成功
        
        Args:
            message: 成功消息
        """
        ui.notify(position="top", type="info", message=message)
        
        # 删除登录表单
        self.delete_form()
        
        # 调整样式
        if self.login_column:
            self.login_column.style("")
        if self.login_card:
            self.login_card.style("position: unset;")
    
    def show_error(self, message: str):
        """
        显示登录错误
        
        Args:
            message: 错误消息
        """
        ui.notify(position="top", type="negative", message=message)
    
    def create_forget_password_dialog(self):
        """
        创建忘记密码对话框
        """
        ui.notify(position="top", type="info", message="请联系管理员修改密码！")
