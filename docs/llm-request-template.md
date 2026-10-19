# LLM 请求模板（OpenAI-compatible 标准协议）

> C4 收敛（2026-10）后，AUTOlive 的 custom_llm 通道固定为 OpenAI-compatible 协议。
> 各平台差异**仅在三项目配置**：`base_url` / `model` / `api_key`，请求体与响应解析不再可配置。
> 素材来源：仓库根 `各LLM平台的API接口示例/` 目录（各平台 curl 原始示例）。

## 标准请求

`POST {base_url}/chat/completions`

```bash
curl "${BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
        "model": "${MODEL}",
        "messages": [
          {"role": "user", "content": "弹幕内容"}
        ]
      }'
```

- `api_key` 为空时不发送 `Authorization` 头（本地 Ollama 场景）。
- 可选参数 `max_tokens` / `temperature`：配置留空则不发送。

## 标准响应（固定解析路径）

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "回复文本"
      }
    }
  ]
}
```

AUTOlive 固定取 `choices[0].message.content` 作为回复，交给 TTS 合成。
响应不含 `choices` 或结构不符时记为失败（日志含 problem/cause/fix）。

## 平台差异对照

| 平台 | base_url | model 示例 | api_key |
|------|----------|-----------|---------|
| Ollama（本地） | `http://127.0.0.1:11434/v1` | `qwen:latest` | 留空 |
| DeepSeek | `https://api.deepseek.com`（或 `/v1`） | `deepseek-chat` | 必填 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3` | 必填 |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | 必填 |
| MiniMax | `https://api.minimax.chat/v1` | `abab6.5s-chat` | 必填 |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 方舟推理接入点 ID | 必填 |
| 讯飞星火 | `https://spark-api-open.xf-yun.com/v1` | `generalv3.5` | 必填 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | 必填 |

> DeepSeek 官方 curl 同时支持 `https://api.deepseek.com/chat/completions`
> 与 `https://api.deepseek.com/v1/chat/completions`（`/v1` 与协议版本无关）。

## AUTOlive 三字段映射

`config.json` 的 `custom_llm` 节（WebUI『LLM』页『自定义LLM』卡片同源）：

```json
{
  "custom_llm": {
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen:latest",
    "api_key": "",
    "max_tokens": "",
    "temperature": ""
  }
}
```

## 旧配置迁移（C4）

存量旧形态配置（url/headers/body/data_analysis 等 9 键）已退役。迁移规则：

| 旧键 | 迁移去向 |
|------|----------|
| `url`（去 `/chat/completions` 尾） | `base_url` |
| `headers` 中 `Authorization: Bearer <key>` | `api_key` |
| `body` JSON 的 `"model"` 字段 | `model`（不可解析 → 默认 `qwen:latest` 并日志提示） |
| `method` / `proxies` / `body_type` / `resp_data_type` / `data_analysis` / `resp_template` | 删除（固定 POST + JSON + OpenAI schema 解析，不留求值后门） |

迁移入口：前端启动时 `frontend/config/settings.py`（磁盘落盘）；运行时 `Custom_LLM.__init__`（内存兜底）。函数：`utils/gpt_model/custom_llm.py::migrate_legacy_config`（幂等）。

## 已退役通道说明

- **fastrag `/query` 模板**：`/query` 为私有 schema（`{"question"}` → `{"answer"}`），
  固定 OpenAI 解析后无法承载，『应用 fastrag 请求模板』按钮已随 C4 退役。
  fastrag 服务开关、知识库构建、上传与生成参数页不受影响。
