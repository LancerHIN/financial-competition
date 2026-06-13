from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

from .config import settings


load_dotenv(settings.root_dir / ".env")


COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenClient:
    def __init__(self, model: str | None = None, temperature: float | None = None, retries: int = 5):
        self.model = model or settings.model_name
        self.temperature = settings.temperature if temperature is None else temperature
        self.retries = retries
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.enabled = bool(self.api_key)
        # 后端模式：compatible（OpenAI 兼容接口，适配 qwen3.6 等新模型，默认）
        #          generation（legacy DashScope Generation.call）| multimodal
        self.api_mode = os.getenv("QWEN_API_MODE", "compatible").lower()
        self._dashscope = None
        if self.enabled and self.api_mode in {"generation", "multimodal"}:
            import dashscope

            dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
            dashscope.api_key = self.api_key
            self._dashscope = dashscope

    def chat(self, messages: list[dict[str, str]], purpose: str = "chat", temperature: float | None = None) -> tuple[str, dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("DASHSCOPE_API_KEY is not set; cannot call Qwen API.")
        # 临时覆盖本次调用温度（自一致性投票用），不污染 client 的默认温度。
        prev_temp = self.temperature
        if temperature is not None:
            self.temperature = temperature
        try:
            last_error: Exception | None = None
            for attempt in range(self.retries):
                try:
                    if self.api_mode == "multimodal":
                        return self._chat_multimodal(messages)
                    if self.api_mode == "generation":
                        return self._chat_generation(messages)
                    return self._chat_compatible(messages)
                except urllib.error.HTTPError as exc:
                    # 4xx 客户端错误（无效模型名/请求体等）重试无意义，立即失败，避免空耗退避时间
                    if 400 <= exc.code < 500:
                        detail = ""
                        try:
                            detail = exc.read().decode("utf-8", "replace")[:300]
                        except Exception:  # noqa: BLE001
                            pass
                        raise RuntimeError(f"Qwen API client error {exc.code} for {purpose}: {detail}") from exc
                    last_error = exc
                    backoff = min(8.0, 1.5 * (2 ** attempt)) + random.uniform(0, 1.0)
                    time.sleep(backoff)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    # 指数退避 + 抖动：高并发下错开重试，降低瞬时 url error / 限流导致的回退
                    backoff = min(8.0, 1.5 * (2 ** attempt)) + random.uniform(0, 1.0)
                    time.sleep(backoff)
            raise RuntimeError(f"Qwen API failed for {purpose}: {last_error}")
        finally:
            self.temperature = prev_temp

    def _chat_compatible(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        """OpenAI 兼容接口（适配 qwen3.6-plus 等新模型）。

        仅用标准库 urllib，不引入 openai 依赖。返回 (content, usage)，usage 为标准
        prompt_tokens/completion_tokens/total_tokens，与 token_counter 兼容。
        """
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                # 关闭 hidden reasoning：JSON 工具调用不需要长 thinking，默认 False 省 completion/reasoning token。
                # 仅作用于 compatible 模式，不影响 generation / multimodal legacy 路径。
                "enable_thinking": settings.qwen_enable_thinking,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{COMPATIBLE_BASE_URL}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return content, dict(usage)

    def _chat_generation(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        response = self._dashscope.Generation.call(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            result_format="message",
        )
        self._raise_for_error(response)
        output = response.output
        content = output["choices"][0]["message"]["content"]
        return content, self._usage_dict(response)

    def _chat_multimodal(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        response = self._dashscope.MultiModalConversation.call(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model=self.model,
            messages=to_multimodal_messages(messages),
        )
        self._raise_for_error(response)
        return extract_multimodal_text(response), self._usage_dict(response)

    @staticmethod
    def _raise_for_error(response: Any) -> None:
        status_code = getattr(response, "status_code", None)
        if status_code is None and hasattr(response, "get"):
            status_code = response.get("status_code")
        if status_code not in (None, 200):
            raise RuntimeError(f"Qwen API error: {getattr(response, 'message', response)}")

    @staticmethod
    def _usage_dict(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None and hasattr(response, "get"):
            usage = response.get("usage", {})
        if usage is None:
            return {}
        return dict(usage) if not isinstance(usage, dict) else usage

    def chat_json(self, messages: list[dict[str, str]], purpose: str = "json", temperature: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        content, usage = self.chat(messages, purpose=purpose, temperature=temperature)
        # API 调用已成功并产生 token，即使 JSON 解析失败也必须把 usage 返回给调用方记账，
        # 不能让解析异常吞掉已消耗的 token（合规：所有 Qwen 调用 token 必须计入）。
        # 返回空 dict 时，各调用方均已对空结果做 fallback/空判断处理。
        try:
            return parse_json_object(content), usage
        except Exception as exc:  # noqa: BLE001
            logging.warning("chat_json parse failed for %s: %s", purpose, exc)
            return {}, usage


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # 模型常见输出：合法 JSON 对象后跟解释性文本/第二段 JSON（触发 "Extra data"），
        # 或对象前有前导说明。用 raw_decode 从第一个 '{' 起只解析首个完整对象。
        start = cleaned.find("{")
        if start >= 0:
            decoder = json.JSONDecoder(strict=False)
            try:
                obj, _end = decoder.raw_decode(_sanitize_json(cleaned[start:]))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        # 兜底：截取首个 '{' 到最后一个 '}'（处理对象内部换行/控制字符）
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(_sanitize_json(cleaned[start : end + 1]), strict=False)
        raise


def _sanitize_json(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text


def to_multimodal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            multimodal_content = content
        else:
            multimodal_content = [{"text": str(content)}]
        converted.append({"role": message.get("role", "user"), "content": multimodal_content})
    return converted


def extract_multimodal_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if output is None and hasattr(response, "get"):
        output = response.get("output")
    choices = getattr(output, "choices", None) if output is not None else None
    if choices is None and isinstance(output, dict):
        choices = output.get("choices")
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None and isinstance(choices[0], dict):
        message = choices[0].get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
        text = getattr(first, "text", "")
        return str(text)
    return ""
