"""LLM / Embedding 客户端。

支持通过配置文件接入任意兼容 OpenAI 接口的模型（如通义千问、DeepSeek、文心等）。
未启用或接口不可用时，提供内置的确定性兜底（规则/关键词），保证离线可运行。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.config import Settings


class LLMClient:
    """统一的 LLM 客户端门面。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.chat_cfg = settings.llm_chat
        self.embed_cfg = settings.llm_embedding
        self.enabled = settings.llm_enabled

    # ---------- 可用性 ----------

    def chat_available(self) -> bool:
        return self.enabled and bool(self.chat_cfg.get("base_url") and self.chat_cfg.get("api_key"))

    def embedding_available(self) -> bool:
        return self.enabled and bool(self.embed_cfg.get("base_url") and self.embed_cfg.get("api_key"))

    # ---------- Chat ----------

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """调用 chat 接口；未启用时抛错（由上层决定兜底）。"""
        if not self.chat_available():
            raise RuntimeError("LLM chat 未配置")
        import requests

        url = self.chat_cfg["base_url"].rstrip("/") + "/chat/completions"
        payload = {
            "model": self.chat_cfg.get("model", "gpt-3.5-turbo"),
            "messages": messages,
            **kwargs,
        }
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self.chat_cfg['api_key']}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def complete(self, prompt: str, system: str = "你是 SQL 特征计算专家。", **kwargs) -> str:
        return self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )

    # ---------- Embedding ----------

    def embed(self, text: str) -> List[float]:
        if not self.embedding_available():
            raise RuntimeError("LLM embedding 未配置")
        import requests

        url = self.embed_cfg["base_url"].rstrip("/") + "/embeddings"
        payload = {"model": self.embed_cfg.get("model", "text-embedding-v3"), "input": text}
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self.embed_cfg['api_key']}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]
