"""业务异常定义。"""


class Text2SQLError(Exception):
    """系统基础异常。"""


class DSLParseError(Text2SQLError):
    """DSL 解析错误。"""


class KnowledgeBaseError(Text2SQLError):
    """知识库读写错误。"""


class TranslationError(Text2SQLError):
    """规则翻译错误。"""
