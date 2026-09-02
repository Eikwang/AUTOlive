import json
import re
import aiohttp
import asyncio
import traceback

from utils.common import Common
from utils.my_log import logger


class Custom_LLM:
    def __init__(self, data):
        self.config_data = data
        self.common = Common()

    def parse_headers(self, headers_text):
        headers = {}
        for line in headers_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip()] = value.strip()
        return headers

    def replace_variables(self, text, variables):
        for key, value in variables.items():
            text = re.sub(f'{{{{{key}}}}}', value, text)
        return text

    async def send_request(self, url="", method='GET', headers=None, body_type="json", body=None, resp_data_type="json", proxies=None, timeout=60):
        """
        发送异步 HTTP 请求并返回结果

        Parameters:
            url (str): 请求的 URL
            method (str): 请求方法，'GET' 或 'POST'
            headers (str): 请求头（每行一个键值对，如：Content-Type: application/json）
            body_type (str): 请求体类型（json | raw）
            body (str): 请求体
            resp_data_type (str): 返回数据的类型（json | content）
            proxies (dict): 代理配置（aiohttp 使用 proxy 参数）
            timeout (int): 请求超时时间

        Returns:
            dict|str: 包含响应的 JSON数据 | 字符串数据
        """

        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                request_method = method.upper()
                
                if body_type == "json":
                    body_data = json.loads(body)
                    async with session.request(
                        method=request_method, url=url, headers=headers,
                        json=body_data, proxy=proxies
                    ) as response:
                        logger.debug(f'response.status={response.status}')

                        if resp_data_type == "json":
                            result = await response.json()
                        else:
                            content = await response.read()
                            result = content.decode('utf-8')

                        return result
                else:
                    body_data = body.encode('utf-8')
                    async with session.request(
                        method=request_method, url=url, headers=headers,
                        data=body_data, proxy=proxies
                    ) as response:
                        logger.debug(f'response.status={response.status}')

                        if resp_data_type == "json":
                            result = await response.json()
                        else:
                            content = await response.read()
                            result = content.decode('utf-8')

                        return result

        except aiohttp.ClientError as e:
            logger.error(traceback.format_exc())
            logger.error(f"请求出错: {e}")
            return None
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"请求出错: {e}")
            return None


    async def get_resp(self, data):
        """异步请求对应接口，获取返回值

        Args:
            data (dict): 请求参数

        Returns:
            str: 返回的文本回答
        """
        try:
            variables = {
                "cur_time": self.common.get_bj_time(0),
                "prompt": data['prompt'],
            }

            url = self.replace_variables(self.config_data['url'], variables)
            method = self.config_data['method']
            body_type = self.config_data['body_type']
            body = self.replace_variables(self.config_data['body'], variables)
            resp_data_type = self.config_data['resp_data_type']
            headers = self.parse_headers(self.replace_variables(self.config_data['headers'], variables))
            data_analysis = self.config_data['data_analysis']
            resp_template = self.config_data['resp_template']
            if self.config_data['proxies'] == '':
                proxies = None
            else:
                proxies = self.config_data['proxies'] if isinstance(self.config_data['proxies'], str) else json.dumps(self.config_data['proxies'])

            logger.debug(f"url={url}\nheaders={headers}\nbody={body}")

            resp = await self.send_request(url=url, method=method, headers=headers, body_type=body_type, body=body, resp_data_type=resp_data_type, proxies=proxies, timeout=60)
            if resp is None:
                return None
                
            # 使用 eval() 执行字符串表达式并获取结果
            resp_content = eval(data_analysis)

            variables = {
                'cur_time': self.common.get_bj_time(5),
                'data': resp_content
            }

            # 使用字典进行字符串替换
            if any(var in resp_template for var in variables):
                resp_content = resp_template.format(**{var: value for var, value in variables.items() if var in resp_template})

            return resp_content
        except Exception as e:
            logger.error(traceback.format_exc())
            return None

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

    data = {
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "headers": "Content-Type:application/json\nAuthorization:Bearer sk",
        "method": "POST",
        "proxies": "{}",
        "body_type": "json",
        "body": "{\"model\":\"qwen:latest\",\"messages\":[{\"role\":\"user\",\"content\":\"{{prompt}}\"}]}",
        "resp_data_type": "json",
        "data_analysis": "resp[\"choices\"][0][\"message\"][\"content\"]",
        "resp_template": "{data}"
    }

    custom_llm = Custom_LLM(data)

    async def main():
        result = await custom_llm.get_resp({"prompt": "早上好"})
        logger.info(result)

    asyncio.run(main())
