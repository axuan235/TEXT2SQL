"""特征描述结构化。

特征的标准描述 ==>> 业务_主体_限定词组_(维度)属性：值域
  - 业务空间 [D]：如 链家访客（口语中一般缺省）
  - 主体 [S]：如 用户、学生
  - 限定词组 [C]：如 最近三天、男性（可缺省或多个）
  - (维度)属性 [O]：如 访问次数、性别、年龄
  - 值域：如 >5、=女、>18;<30

结构化结果分为两部分：主体_属性 与 限定词列表。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..llm.client import LLMClient

# 常见主体词（用于离线启发式切分，可扩展）
COMMON_SUBJECTS = {
    "用户", "学生", "访客", "客户", "会员", "考生", "教师", "员工", "患者", "买家", "卖家",
}

# 常见业务空间词
COMMON_DOMAINS = {
    "链家", "链家访客", "教务", "教务系统", "电商", "医疗", "金融", "教育", "房地产",
}


@dataclass
class StructuredFeature:
    """结构化特征描述。"""

    subject: str = ""           # 主体 [S]
    attr: str = ""              # (维度)属性 [O]
    domain: str = ""            # 业务空间 [D]
    qualifiers: List[str] = field(default_factory=list)  # 限定词组 [C]
    value_cond: str = ""        # 值域条件，如 >5、=女

    @property
    def subject_attr(self) -> str:
        """主体_属性（知识库检索元素）。"""
        return f"{self.subject}_{self.attr}" if self.subject else self.attr

    def to_text(self) -> str:
        parts = []
        if self.domain:
            parts.append(self.domain)
        if self.subject:
            parts.append(self.subject)
        parts.extend(self.qualifiers)
        parts.append(self.attr)
        name = "_".join(parts)
        if self.value_cond:
            return f"{name}：{self.value_cond}"
        return name

    def to_dict(self) -> Dict[str, object]:
        return {
            "domain": self.domain,
            "subject": self.subject,
            "qualifiers": self.qualifiers,
            "attr": self.attr,
            "value_cond": self.value_cond,
            "subject_attr": self.subject_attr,
        }


class DescriptionParser:
    """特征描述解析器（LLM 优先，离线启发式兜底）。"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm

    def parse(self, text: str) -> StructuredFeature:
        text = text.strip()
        name, _, value_cond = text.partition("：")
        if not value_cond:
            name, _, value_cond = text.partition(":")
        name = name.strip()
        value_cond = value_cond.strip()

        # LLM 路径
        if self.llm and self.llm.chat_available():
            try:
                return self._parse_with_llm(name, value_cond)
            except Exception:
                pass
        return self._parse_heuristic(name, value_cond)

    # ---------- LLM 切分 ----------

    def _parse_with_llm(self, name: str, value_cond: str) -> StructuredFeature:
        prompt = f"""
将以下特征描述切分为四个部分，返回 JSON（不要输出其它内容）：
- domain: 业务空间[D]，口语缺省时为空字符串
- subject: 主体[S]
- qualifiers: 限定词组[C]列表，可空
- attr: (维度)属性[O]

特征描述：{name}
"""
        resp = self.llm.complete(prompt)
        m = re.search(r"\{.*\}", resp, re.S)
        if not m:
            raise ValueError("LLM 返回格式错误")
        data = json.loads(m.group(0))
        return StructuredFeature(
            subject=str(data.get("subject", "")),
            attr=str(data.get("attr", "")),
            domain=str(data.get("domain", "")),
            qualifiers=[str(q) for q in data.get("qualifiers", [])],
            value_cond=value_cond,
        )

    # ---------- 离线启发式 ----------

    def _parse_heuristic(self, name: str, value_cond: str) -> StructuredFeature:
        tokens = [t.strip() for t in name.split("_") if t.strip()]
        if not tokens:
            return StructuredFeature(value_cond=value_cond)

        domain, subject, qualifiers, attr = "", "", [], tokens[-1]

        if len(tokens) == 1:
            # 仅属性
            attr = tokens[0]
        else:
            rest = tokens[:-1]
            # 业务空间：首个 token 命中常见业务空间词，或带"系统/网/平台"等后缀
            if rest and (rest[0] in COMMON_DOMAINS or self._looks_like_domain(rest[0])):
                domain = rest.pop(0)
            # 主体：下一个 token
            if rest:
                if rest[0] in COMMON_SUBJECTS or len(rest) == 1:
                    subject = rest.pop(0)
                else:
                    # 多词主体（如 链家访客_用户 已被 domain 吸收时，这里是用户）
                    subject = rest.pop(0)
            qualifiers = rest

        return StructuredFeature(
            subject=subject,
            attr=attr,
            domain=domain,
            qualifiers=qualifiers,
            value_cond=value_cond,
        )

    @staticmethod
    def _looks_like_domain(token: str) -> bool:
        return any(k in token for k in ("系统", "网", "平台", "集团", "银行", "大学", "公司"))
