# -*- coding: UTF-8 -*-
"""
custom_llm：OpenAI-compatible 请求通道（C4 收敛，2026-10）

S2 裁决：响应解析固定 OpenAI schema（choices[0].message.content），
data_analysis/resp_template/method/proxies/body_type/headers/body/url 全部退役，
不留求值（eval）后门。各平台差异仅在 base_url/model/api_key 三项配置，
请求模板示例见 docs/llm-request-template.md。
"""
import asyncio
import json
import queue
import re
import threading
import traceback

from utils.my_log import logger

# 旧形态键（C4 退役清单）——迁移时全部移除
LEGACY_KEYS = ("url", "headers", "method", "proxies", "body_type", "body",
               "resp_data_type", "data_analysis", "resp_template")

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"   # 存量 Ollama 用户无缝
DEFAULT_MODEL = "qwen:latest"
_CHAT_COMPLETIONS = "/chat/completions"


def _get_aiohttp():
    """惰性加载 aiohttp：迁移函数可被前端配置层（frontend/config/settings.py）
    轻量复用，配置迁移路径不应拉起 HTTP 栈。"""
    import aiohttp
    return aiohttp


def parse_headers(headers_text):
    """解析换行分隔的请求头文本为 dict（兼容保留：迁移函数从中提取 Authorization）。

    Args:
        headers_text (str): 每行一个键值对，如 "Content-Type:application/json"

    Returns:
        dict: 请求头字典
    """
    headers = {}
    for line in str(headers_text or "").split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            headers[key.strip()] = value.strip()
    return headers


def replace_variables(text, variables):
    """{{key}} 变量替换（兼容保留：C4 后请求体模板退役，新代码不得依赖）。"""
    for key, value in variables.items():
        text = re.sub(f'{{{{{key}}}}}', value, text)
    return text


def _extract_bearer_key(headers_text):
    """从旧 headers 文本提取 Authorization: Bearer <key> 的 key 部分。"""
    headers = parse_headers(headers_text)
    for key, value in headers.items():
        if key.strip().lower() == "authorization":
            parts = str(value).split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
            return str(value).strip()
    return ""


def _extract_model_from_body(body_text):
    """从旧请求体 JSON 提取 model 字段（不可解析返回空串）。"""
    if isinstance(body_text, dict):
        body = body_text
    elif isinstance(body_text, str):
        try:
            body = json.loads(body_text)
        except (json.JSONDecodeError, ValueError):
            return ""
    else:
        return ""
    if isinstance(body, dict):
        return str(body.get("model") or "")
    return ""


def _default_config():
    return {
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "api_key": "",
        "max_tokens": "",
        "temperature": "",
    }


def migrate_legacy_config(data):
    """旧形态 custom_llm 配置 → OpenAI-compatible 字段（C4 收敛，幂等）。

    迁移规则：
    - url → base_url：去 /chat/completions 尾作服务前缀；
    - headers 中 Authorization:Bearer <key> → api_key；
    - body JSON 的 "model" 字段 → model（不可解析 → 默认 qwen:latest 并 log 提示）；
    - 可选 max_tokens/temperature 缺省补空串；旧键全部删除。
    新形态配置仅补缺失键，不覆盖既有值。

    Args:
        data (dict|None): 旧/新形态 custom_llm 配置节

    Returns:
        (dict, bool): (收敛后的配置, 是否发生变更)
    """
    new_cfg = {}
    if isinstance(data, dict):
        for key in ("base_url", "model", "api_key", "max_tokens", "temperature"):
            if key in data:
                new_cfg[key] = data[key]
    has_legacy = isinstance(data, dict) and any(k in data for k in LEGACY_KEYS)
    if has_legacy:
        url = str(data.get("url") or "").strip()
        if url.endswith(_CHAT_COMPLETIONS):
            url = url[: -len(_CHAT_COMPLETIONS)]
        new_cfg["base_url"] = url.rstrip("/") or DEFAULT_BASE_URL
        api_key = _extract_bearer_key(data.get("headers"))
        if api_key:
            new_cfg["api_key"] = api_key
        model = _extract_model_from_body(data.get("body"))
        if model:
            new_cfg["model"] = model
        else:
            logger.info(
                "custom_llm 迁移：旧请求体不可解析或无 model 字段，"
                f"使用默认模型 {DEFAULT_MODEL}（请在 WebUI『自定义LLM』确认）"
            )
    # 幂等补全：缺省键补默认（新形态亦走此分支）
    for key, value in _default_config().items():
        if key not in new_cfg:
            new_cfg[key] = value
    changed = (new_cfg != data) if isinstance(data, dict) else True
    return new_cfg, changed


class Custom_LLM:
    """OpenAI-compatible 自定义 LLM 通道（C4 收敛后唯一形态）。"""

    def __init__(self, data):
        migrated, changed = migrate_legacy_config(data)
        if changed:
            logger.info(
                "custom_llm 配置已收敛为 OpenAI-compatible 字段"
                "（base_url/model/api_key/max_tokens/temperature）；旧键已移除（C4）"
            )
        self.config_data = migrated

    def _build_prompt(self, data):
        """构建 user prompt：兼容 {"prompt": ...}（脚本直调）与运行时弹幕数据 {"content": ...}。"""
        if isinstance(data, dict):
            return str(data.get("prompt") or data.get("content") or "")
        return str(data or "")

    def _build_headers(self):
        """固定请求头；api_key 空则不带 Authorization（本地 Ollama 场景）。"""
        headers = {"Content-Type": "application/json"}
        api_key = str(self.config_data.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _chat_completions_url(self):
        """{base_url}/chat/completions（base_url 已含尾路径则不重复拼接）。"""
        base = str(self.config_data.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        if base.endswith(_CHAT_COMPLETIONS):
            return base
        return base + _CHAT_COMPLETIONS

    def _build_body(self, prompt):
        """固定请求体：model + messages；max_tokens/temperature 可选（留空不发）。"""
        body = {
            "model": str(self.config_data.get("model") or DEFAULT_MODEL),
            "messages": [{"role": "user", "content": prompt}],
        }
        max_tokens = self.config_data.get("max_tokens")
        if max_tokens not in (None, ""):
            body["max_tokens"] = int(max_tokens)
        temperature = self.config_data.get("temperature")
        if temperature not in (None, ""):
            body["temperature"] = float(temperature)
        return body

    async def send_request(self, url, headers=None, body=None, timeout=60):
        """固定 POST OpenAI-compatible 端点。

        Args:
            url (str): {base_url}/chat/completions
            headers (dict): 请求头
            body (dict): JSON 请求体（_build_body 产物）
            timeout (int): 超时秒数

        Returns:
            tuple[int, str]: (HTTP 状态码, 响应原文)——解析留给 get_resp 以区分错误分支
        """
        aiohttp = _get_aiohttp()
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(url, headers=headers, json=body) as response:
                text = await response.text()
                logger.debug(f"custom_llm response.status={response.status}")
                return response.status, text

    async def get_resp(self, data):
        """异步请求 LLM 并返回文本回答（固定解析 choices[0].message.content）。

        错误分支均为 agent-ready 日志（problem/cause/fix）：
        超时 / 非 200 / JSON 解析失败 / choices 空。

        Args:
            data (dict): {"prompt": ...} 或运行时弹幕数据 {"content": ...}

        Returns:
            str | None: 文本回答；失败返回 None（上层按"无返回"处理）
        """
        aiohttp = _get_aiohttp()
        try:
            prompt = self._build_prompt(data)
            url = self._chat_completions_url()
            headers = self._build_headers()
            body = self._build_body(prompt)
            logger.debug(f"custom_llm 请求: url={url} model={body['model']}")

            status, text = await self.send_request(url=url, headers=headers, body=body, timeout=60)
            snippet = str(text or "")[:200]

            if status == 401:
                logger.error(
                    "problem=LLM 认证失败（HTTP 401）；cause=api_key 缺失或无效；"
                    "fix=在 WebUI『自定义LLM』填入正确 api_key（本地 Ollama 留空即可）"
                )
                return None
            if status == 404:
                logger.error(
                    f"problem=LLM 端点不存在（HTTP 404）；cause=base_url 缺少 /v1 前缀或路径有误（当前：{url}）；"
                    "fix=base_url 填服务前缀（如 https://api.deepseek.com/v1），系统自动拼接 /chat/completions"
                )
                return None
            if status != 200:
                logger.error(
                    f"problem=LLM 返回非 200（HTTP {status}）；cause=服务端拒绝；"
                    f"fix=检查 base_url/model 配置；响应片段：{snippet}"
                )
                return None
            try:
                resp = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                logger.error(
                    "problem=LLM 响应不是合法 JSON；cause=端点非 OpenAI-compatible"
                    "（可能误指 /query 等私有端点）；"
                    f"fix=确认 base_url 指向 OpenAI-compatible 服务；响应片段：{snippet}"
                )
                return None
            choices = resp.get("choices") or []
            if not choices:
                logger.error(
                    f"problem=LLM 响应无 choices；cause=服务端返回异常结构；"
                    f"fix=确认服务为 OpenAI-compatible chat/completions；响应片段：{snippet}"
                )
                return None
            content = (choices[0].get("message") or {}).get("content")
            if content is None:
                logger.error(
                    f"problem=LLM 响应缺少 message.content；cause=响应不符合 OpenAI schema；"
                    f"fix=确认服务为 OpenAI-compatible；响应片段：{snippet}"
                )
                return None
            return str(content)
        except asyncio.TimeoutError:
            logger.error(
                "problem=LLM 请求超时（60s）；cause=服务未响应；"
                "fix=确认 LLM 服务已启动（如 Ollama：ollama serve）且 base_url 可达"
            )
            return None
        except ValueError:
            logger.error(
                "problem=LLM 参数非法；cause=max_tokens/temperature 不是数字；"
                "fix=在 WebUI『自定义LLM』检查 max_tokens/temperature（留空=不发送该参数）"
            )
            return None
        except aiohttp.ClientError as e:
            logger.error(traceback.format_exc())
            logger.error(
                f"problem=LLM 请求网络错误；cause={e}；"
                "fix=确认 base_url 可达（本地服务先启动，远程服务检查网络/代理）"
            )
            return None
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"problem=LLM 请求失败；cause={e}；fix=查看日志定位")
            return None

    async def stream_resp(self, data, timeout=60):
        """流式请求 LLM，逐段产出文本增量（① 2026-10-27 主链延迟优化）。

        OpenAI-compatible SSE（"stream": true）——data: 行解析 choices[0].
        delta.content；服务端不支持流式时多数兼容服务自动降级为单块响应，
        解析层不感知（delta.content 空时回读 message.content 一次）。

        Args:
            data (dict): {"prompt": ...} 或运行时弹幕数据 {"content": ...}
            timeout (int): 超时秒数

        Yields:
            str: 文本增量（可能为空串，调用方跳过）
        """
        aiohttp = _get_aiohttp()
        try:
            prompt = self._build_prompt(data)
            url = self._chat_completions_url()
            headers = self._build_headers()
            body = self._build_body(prompt)
            body["stream"] = True
            logger.debug(f"custom_llm 流式请求: url={url} model={body['model']}")

            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, headers=headers, json=body) as response:
                    if response.status != 200:
                        logger.error(
                            f"problem=LLM 流式返回非 200（HTTP {response.status}）；"
                            "cause=服务端拒绝；fix=检查 base_url/model 配置；"
                            "流式不可用将回退全文链路"
                        )
                        return
                    buffer = ""
                    async for raw in response.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            buffer += delta
                            yield delta
                    # 非流式降级响应（部分兼容服务忽略 stream 标志整块返回）：
                    # delta.content 恒空时回读一次 message.content
                    if not buffer:
                        try:
                            resp = json.loads(await response.text())
                            content = (resp.get("choices") or [{}])[0].get(
                                "message", {}).get("content")
                            if content:
                                yield str(content)
                        except Exception:
                            pass
        except asyncio.TimeoutError:
            logger.error(
                "problem=LLM 流式请求超时；cause=服务未响应；"
                "fix=确认 LLM 服务已启动且 base_url 可达；回退全文链路"
            )
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"problem=LLM 流式请求失败；cause={e}；回退全文链路")

    def stream_resp_sync(self, data):
        """stream_resp 的同步生成器桥（线程内独立事件循环 + 队列转交）。

        独立线程跑 asyncio.run（绕开主线程事件循环占用——get_resp_sync 的
        executor 方案无法分块转交增量），生成器耗尽/异常时终止。

        Args:
            data (dict): 请求参数

        Yields:
            str: 文本增量；上游异常时原样抛出（调用方按流式失败处理）
        """
        q = queue.Queue()
        _DONE = object()

        def _run():
            async def _pump():
                try:
                    async for delta in self.stream_resp(data):
                        q.put(delta)
                except Exception as e:
                    q.put(e)
                finally:
                    q.put(_DONE)
            asyncio.run(_pump())

        threading.Thread(target=_run, name="llm-stream-pump", daemon=True).start()
        while True:
            item = q.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def get_resp_sync(self, data):
        """同步版本的 get_resp（向后兼容）

        Args:
            data (dict): 请求参数

        Returns:
            str: 返回的文本回答
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果事件循环正在运行，使用 run_in_executor
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.get_resp(data))
                    return future.result()
            else:
                return loop.run_until_complete(self.get_resp(data))
        except RuntimeError:
            return asyncio.run(self.get_resp(data))


# 测试用
if __name__ == '__main__':
    import logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    custom_llm = Custom_LLM({
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen:latest",
        "api_key": "",
        "max_tokens": "",
        "temperature": "",
    })

    async def main():
        result = await custom_llm.get_resp({"prompt": "早上好"})
        logger.info(result)

    asyncio.run(main())
