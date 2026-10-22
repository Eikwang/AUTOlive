# -*- coding: UTF-8 -*-
"""
fastrag：fastrag 知识库 HTTP 通道（基础功能版块 2026-10-22 批次）

契约锚定（native CEO F7）：POST {base}/query {"question": ...} → {"answer": ...}
与 edtalk/fastrag/api_client.py 当前版本形态一致（2026-10-22 快照；该文件为
上游副本区源码，本模块不 import 之——契约漂移时人工核对此处注释）。

失败语义（spec R2-2 方案 a）：连接拒绝/超时/非 200/解析失败均为失败→上层托底；
不设独立 health 探测（连接拒绝即时失败，启动警告仅看 fastrag.enable 配置开关）。
失败 log 按 agent-ready 契约（DX CAP6-1/2/3）：单行含失败类别+实际 host:port+
超时阈值+fix 指引，稳定前缀 LLM_FALLBACK 便于 grep；非预期异常附紧凑 traceback。
"""
import asyncio
import json
import traceback

from utils.base import logger

DEFAULT_FASTRAG_HOST = "localhost"   # DX medium：读 config fastrag.host，跨机部署可改
_QUERY_TIMEOUT = 60                  # 秒，对齐 custom_llm


def _get_aiohttp():
    """惰性加载 aiohttp（对齐 custom_llm 模式，配置迁移路径不拉 HTTP 栈）。"""
    import aiohttp
    return aiohttp


class Fastrag_LLM:
    """fastrag 知识库 LLM 通道（接口对齐 Custom_LLM.get_resp_sync(data_json)）。"""

    def __init__(self, data):
        cfg = data if isinstance(data, dict) else {}
        self.host = str(cfg.get("host") or DEFAULT_FASTRAG_HOST).strip() or DEFAULT_FASTRAG_HOST
        self.port = cfg.get("port") or 11420
        base = f"http://{self.host}:{self.port}"
        # ENG E-8：host 限定 http(s) 形态（默认 http://localhost:{port}）
        self.query_url = base + "/query"

    def _build_prompt(self, data):
        """对齐 Custom_LLM：兼容 {"prompt": ...} 与运行时弹幕数据 {"content": ...}。"""
        if isinstance(data, dict):
            return str(data.get("prompt") or data.get("content") or "")
        return str(data or "")

    async def get_resp(self, data):
        """异步查询 fastrag /query 并返回文本回答。

        Args:
            data (dict): {"prompt": ...} 或运行时弹幕数据 {"content": ...}

        Returns:
            str | None: 知识库回答文本；失败返回 None（上层按失败→托底处理）
        """
        aiohttp = _get_aiohttp()
        question = self._build_prompt(data)
        try:
            client_timeout = aiohttp.ClientTimeout(total=_QUERY_TIMEOUT)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(self.query_url, json={"question": question}) as response:
                    status = response.status
                    text = await response.text()
                    if status != 200:
                        logger.error(
                            f"LLM_FALLBACK fastrag /query 失败(非200, HTTP {status}, "
                            f"{self.host}:{self.port}, 超时{_QUERY_TIMEOUT}s)；"
                            f"请确认 fastrag.enable=true 且服务已启动、核对 fastrag.port，"
                            f"或将 chat_type 改为 custom_llm/local_qa")
                        return None
                    try:
                        resp = json.loads(text)
                        answer = resp.get("answer")
                        if answer is None:
                            logger.error(
                                f"LLM_FALLBACK fastrag /query 失败(解析失败: 无 answer 字段, "
                                f"{self.host}:{self.port})；请核对 fastrag 服务版本与契约"
                                f"（edtalk/fastrag/api_client.py）")
                            return None
                        return str(answer)
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.error(
                            f"LLM_FALLBACK fastrag /query 失败(解析失败: {type(e).__name__}, "
                            f"{self.host}:{self.port})；请核对 fastrag 服务版本与契约"
                            f"（edtalk/fastrag/api_client.py）")
                        return None
        except asyncio.TimeoutError:
            logger.error(
                f"LLM_FALLBACK fastrag /query 失败(超时 {_QUERY_TIMEOUT}s, {self.host}:{self.port})；"
                f"请确认 fastrag 服务已启动且未挂起，或将 chat_type 改为 custom_llm/local_qa")
            return None
        except aiohttp.ClientError as e:
            logger.error(
                f"LLM_FALLBACK fastrag /query 失败(连接拒绝: {type(e).__name__}, "
                f"{self.host}:{self.port})；请确认 fastrag.enable=true 且服务已启动、"
                f"核对 fastrag.port（WebUI 知识库页），或将 chat_type 改为 custom_llm/local_qa")
            return None
        except Exception as e:
            # 非预期异常（代码缺陷而非服务故障）附紧凑 traceback 再降级——防缺陷伪装成服务故障
            logger.error(
                f"LLM_FALLBACK fastrag /query 非预期异常({type(e).__name__}: {e})——"
                f"附 traceback 降级托底；fix=按 traceback 定位代码缺陷")
            logger.error(traceback.format_exc(limit=8))
            return None

    def get_resp_sync(self, data):
        """同步版本（对齐 Custom_LLM.get_resp_sync，向后兼容）。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.get_resp(data))
                    return future.result()
            else:
                return loop.run_until_complete(self.get_resp(data))
        except RuntimeError:
            return asyncio.run(self.get_resp(data))
