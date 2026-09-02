import json
import os

# 读取config.json
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

# 创建基础配置
base_config = {
    'platform': config.get('platform', 'talk'),
    'room_display_id': config.get('room_display_id', ''),
    'chat_type': config.get('chat_type', 'custom_llm'),
    'need_lang': config.get('need_lang', 'none'),
    'before_prompt': config.get('before_prompt', '请简要回复:'),
    'after_prompt': config.get('after_prompt', ''),
    'api_ip': config.get('api_ip', '127.0.0.1'),
    'api_port': config.get('api_port', 8082)
}

# 创建平台配置
platforms = {
    'bilibili': config.get('bilibili', {}),
    'twitch': config.get('twitch', {}),
    'talk': config.get('talk', {})
}

# 创建LLM配置
llm_config = {
    'custom_llm': config.get('custom_llm', {}),
    'gemini': config.get('gemini', {}),
    'search_online': config.get('search_online', {})
}

# 创建TTS配置
tts_config = {
    'play_audio': config.get('play_audio', {}),
    'audio_player': config.get('audio_player', {}),
    'audio_synthesis_type': config.get('audio_synthesis_type', ''),
    'audio_random_speed': config.get('audio_random_speed', False),
    'so_vits_svc': config.get('so_vits_svc', {}),
    'gpt_sovits': config.get('gpt_sovits', {})
}

# 创建features配置
features_config = {
    'read_comment': config.get('read_comment', {}),
    'filter': config.get('filter', {}),
    'thanks': config.get('thanks', {}),
    'local_qa': config.get('local_qa', {}),
    'choose_song': config.get('choose_song', {}),
    'sd': config.get('sd', {}),
    'copywriting': config.get('copywriting', {}),
    'header': config.get('header', {}),
    'image_recognition': config.get('image_recognition', {}),
    'captions': config.get('captions', {}),
    'schedule': config.get('schedule', {}),
    'idle_time_task': config.get('idle_time_task', {}),
    'database': config.get('database', {}),
    'game': config.get('game', {}),
    'trends_copywriting': config.get('trends_copywriting', {}),
    'web_captions_printer': config.get('web_captions_printer', {}),
    'integral': config.get('integral', {}),
    'key_mapping': config.get('key_mapping', {}),
    'custom_cmd': config.get('custom_cmd', {}),
    'translate': config.get('translate', {}),
    'abnormal_alarm': config.get('abnormal_alarm', {}),
    'trends_config': config.get('trends_config', {}),
    'coordination_program': config.get('coordination_program', {}),
    'assistant_anchor': config.get('assistant_anchor', {}),
    'serial': config.get('serial', {}),
    'data_analysis': config.get('data_analysis', {}),
    'webui': config.get('webui', {}),
    'login': config.get('login', {}),
    'comment_template': config.get('comment_template', {}),
    'reply_template': config.get('reply_template', {}),
    'comment_log_type': config.get('comment_log_type', '回答'),
    'visual_body': config.get('visual_body', 'metahuman_stream'),
    'luoxi_project': config.get('luoxi_project', {}),
    'metahuman_stream': config.get('metahuman_stream', {}),
    'ordinaryroad_barrage_fly': config.get('ordinaryroad_barrage_fly', {})
}

# 创建config目录
os.makedirs('config', exist_ok=True)
os.makedirs('config/platforms', exist_ok=True)
os.makedirs('config/llm', exist_ok=True)
os.makedirs('config/tts', exist_ok=True)
os.makedirs('config/features', exist_ok=True)

# 写入配置文件
with open('config/base.json', 'w', encoding='utf-8') as f:
    json.dump(base_config, f, ensure_ascii=False, indent=2)

for platform_name, platform_config in platforms.items():
    with open(f'config/platforms/{platform_name}.json', 'w', encoding='utf-8') as f:
        json.dump(platform_config, f, ensure_ascii=False, indent=2)

for llm_name, llm_config_item in llm_config.items():
    with open(f'config/llm/{llm_name}.json', 'w', encoding='utf-8') as f:
        json.dump(llm_config_item, f, ensure_ascii=False, indent=2)

with open('config/tts/tts.json', 'w', encoding='utf-8') as f:
    json.dump(tts_config, f, ensure_ascii=False, indent=2)

with open('config/features/features.json', 'w', encoding='utf-8') as f:
    json.dump(features_config, f, ensure_ascii=False, indent=2)

print("配置文件拆分完成！")
print("创建的文件：")
print("- config/base.json")
print("- config/platforms/bilibili.json")
print("- config/platforms/twitch.json")
print("- config/platforms/talk.json")
print("- config/llm/custom_llm.json")
print("- config/llm/gemini.json")
print("- config/llm/search_online.json")
print("- config/tts/tts.json")
print("- config/features/features.json")
