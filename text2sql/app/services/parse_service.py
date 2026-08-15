"""系统工作1：用户描述拆解确认 -> 结构化输出。

产品 story Q1：用户描述需求，如"找最近三个月与多位不同男性同住的年轻女性"
A1：结构化特征列表（特征 + 值域）+ 输出字段
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..core.config import Settings
from ..feature.description import DescriptionParser, StructuredFeature
from ..llm.client import LLMClient


class ParseService:
    """用户描述 -> 结构化特征。"""

    def __init__(self, settings: Settings, llm: Optional[LLMClient] = None):
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.parser = DescriptionParser(self.llm)

    def parse(self, description: str) -> Dict[str, Any]:
        """解析用户描述，输出结构化特征列表。"""
        features = self._split_features(description)
        structured = [self.parser.parse(f) for f in features]
        return {
            "input": description,
            "features": [s.to_dict() for s in structured],
            "output_fields": self._guess_output_fields(structured),
            "confirm_hint": self._build_confirm_hint(structured),
        }

    # ---------- 描述拆分 ----------

    def _split_features(self, description: str) -> List[str]:
        """将一段用户描述拆成多个特征描述（启发式 + LLM）。"""
        if self.llm.chat_available():
            try:
                return self._split_with_llm(description)
            except Exception:
                pass
        return self._split_heuristic(description)

    def _split_heuristic(self, description: str) -> List[str]:
        # 简单规则：按 "、" "，" "，" "。 与特征词拆分
        # 先找值域特征（包含 数字/比较词 的短语）
        feats: List[str] = []
        if re.search(r"同住|合住|室友", description):
            if re.search(r"男性|男", description):
                feats.append("用户_近三个月_男性_同住人数：>5")
            else:
                feats.append("用户_近三个月_同住人数：>5")
        if re.search(r"女", description):
            feats.append("用户_性别：=女")
        elif re.search(r"男性|男", description) and not feats:
            feats.append("用户_性别：=男")
        if re.search(r"年龄|年轻|年长", description):
            feats.append("用户_年龄：>18;<30")
        if not feats:
            feats = [description]
        return feats

    def _split_with_llm(self, description: str) -> List[str]:
        prompt = f"""
请将用户需求拆解为结构化特征描述列表，每行一个特征，格式：主体_限定词_属性：值域。
需求：{description}
只输出特征列表，每行一个，不要其它内容。
"""
        resp = self.llm.complete(prompt)
        return [line.strip() for line in resp.splitlines() if line.strip() and "：" in line or ":" in line]

    # ---------- 输出字段 ----------

    def _guess_output_fields(self, structured: List[StructuredFeature]) -> List[str]:
        fields = []
        for s in structured:
            fields.append(f"{s.subject}_{s.attr}" if s.subject else s.attr)
        return fields

    def _build_confirm_hint(self, structured: List[StructuredFeature]) -> str:
        """生成供用户确认的提示文案（A1 模板）。"""
        lines = []
        for i, s in enumerate(structured, 1):
            lines.append(f"特征{i}：{s.to_text()}")
        return "\n".join(lines)
