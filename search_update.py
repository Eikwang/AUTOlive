# Search functionality update for webui.py

# This file contains the updated search functions

# Updated search_configs function
def search_configs(keyword):
    """实时搜索配置项"""
    if not keyword:
        search_results_container.clear()
        search_results_container.style('display: none')
        return
    
    results = []
    for group in config_groups:
        for func in group["functions"]:
            # 搜索功能名称
            if keyword.lower() in func["name"].lower():
                results.append({"type": "function", "name": func["name"], "group": group["name"], "group_name": group["name"], "func_name": func["name"]})
            
            # 搜索配置项名称
            for config_item in func["configs"]:
                if keyword.lower() in config_item["label"].lower():
                    results.append({"type": "config", "name": config_item["label"], "function": func["name"], "group": group["name"], "group_name": group["name"], "func_name": func["name"]})
    
    display_search_results(results, keyword)

# Updated display_search_results function
def display_search_results(results, keyword):
    """显示搜索结果，高亮匹配关键字"""
    search_results_container.clear()
    
    with search_results_container:
        if not results:
            ui.label("未找到匹配项").style("color: var(--text-secondary); padding: 16px;")
            search_results_container.style('display: none')
            return
        
        # 显示搜索结果数量
        ui.label(f"找到 {len(results)} 个结果").style("font-size: 12px; color: var(--text-secondary); padding: 8px 16px 4px 16px;")
        
        for result in results:
            # 高亮匹配关键字
            highlighted_name = result["name"].replace(keyword, f'<span class="search-highlight">{keyword}</span>')
            
            # 创建可点击的搜索结果卡片
            with ui.card().style("margin: 4px; padding: 8px; cursor: pointer; transition: background-color 0.2s;") as card:
                ui.html(highlighted_name)
                ui.label(f"{result['group']} > {result.get('function', '')}").style("font-size: 12px; color: var(--text-secondary);")
                
                # 添加点击事件 - 跳转到对应配置页面
                card.on('click', lambda e, r=result: navigate_to_config(r))

# New navigate_to_config function
def navigate_to_config(result):
    """点击搜索结果跳转到对应配置页面"""
    global current_group, current_function
    
    # 隐藏搜索结果
    search_results_container.clear()
    search_results_container.style('display: none')
    
    # 清空搜索框
    search_input.value = ''
    
    # 获取目标分组和功能
    target_group_name = result["group_name"]
    target_func_name = result["func_name"]
    
    # 更新当前组
    current_group = target_group_name
    
    # 查找目标分组
    target_group = next((g for g in config_groups if g["name"] == target_group_name), None)
    if target_group is None:
        return
    
    # 查找目标功能
    target_func = next((f for f in target_group["functions"] if f["name"] == target_func_name), None)
    if target_func is None:
        return
    
    # 更新功能列表显示
    function_list_container.clear()
    with function_list_container:
        for func in target_group["functions"]:
            with ui.card().style("margin: 8px; padding: 12px; cursor: pointer;" + 
                               (" background-color: var(--primary-color); color: white;" if func["name"] == target_func_name else "")) as func_card:
                with ui.row().style("align-items: center; justify-content: space-between;"):
                    ui.label(func["name"]).style("font-size: 16px; font-weight: 500;")
                    ui.switch(
                        value=func["enabled"],
                        on_change=lambda e, f=func: toggle_function(f, e.value)
                    )
                
                # 添加点击事件显示配置
                func_card.on('click', lambda e, f=func: show_config_page(f))
    
    # 显示目标功能的配置页面
    show_config_page(target_func)
    
    # 显示成功提示
    ui.notify(f"已跳转到: {target_group_name} > {target_func_name}", type="positive")
