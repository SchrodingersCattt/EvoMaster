"""
base.py — SoftwareBackend 抽象基类。

所有软件（CP2K、ORCA、QE 等）均须继承此类并实现全部抽象方法。
"""

from abc import ABC, abstractmethod

from engine.completion import CompletionItem
from engine.diagnostics import Diagnostic
from engine.document import DocumentModel
from engine.renderer import RenderIntent
from engine.schema import SchemaRegistry


class SoftwareBackend(ABC):
    """软件后端抽象基类。

    每个子类对应一种计算软件，负责：
      - 解析该软件的输入文件格式 → :class:`DocumentModel`
      - 根据 :class:`RenderIntent` 生成输入文件文本
      - 基于 Schema 对文档做静态诊断
      - 提供光标处的补全候选项
    """

    software_name: str
    """子类须以类变量形式声明软件名称，如 ``software_name = 'cp2k'``。"""

    # ------------------------------------------------------------------
    # 抽象方法（子类必须实现）
    # ------------------------------------------------------------------

    @abstractmethod
    def parse(self, text: str, source: str = "<string>") -> DocumentModel:
        """将输入文件文本解析为 DocumentModel（AST）。

        Parameters
        ----------
        text:
            输入文件的完整文本内容。
        source:
            文档来源描述（路径或 ``'<string>'``），用于诊断消息定位。

        Returns
        -------
        DocumentModel
            解析结果；解析错误应追加到 ``DocumentModel.parse_errors``，
            不应在此抛出异常（除非文本完全无法处理）。
        """

    @abstractmethod
    def render(self, intent: RenderIntent) -> str:
        """根据 RenderIntent 生成输入文件文本。

        Parameters
        ----------
        intent:
            描述要生成什么样输入文件的意图对象。

        Returns
        -------
        str
            生成的输入文件完整文本。
        """

    @abstractmethod
    def get_diagnostics(
        self, doc: DocumentModel, schema: SchemaRegistry
    ) -> list[Diagnostic]:
        """对文档进行静态诊断（参数合法性检查等）。

        Parameters
        ----------
        doc:
            已解析的文档模型。
        schema:
            Schema 注册中心，用于查询参数元数据。

        Returns
        -------
        list[Diagnostic]
            诊断结果列表；若无问题则返回空列表。
        """

    @abstractmethod
    def get_completions(
        self,
        doc: DocumentModel,
        line: int,
        col: int,
        schema: SchemaRegistry,
    ) -> list[CompletionItem]:
        """返回光标位置处的补全候选列表。

        Parameters
        ----------
        doc:
            已解析的文档模型。
        line:
            光标所在行（1-based）。
        col:
            光标所在列（0-based）。
        schema:
            Schema 注册中心。

        Returns
        -------
        list[CompletionItem]
            补全候选列表；按 sort_priority 升序排列。
        """

    # ------------------------------------------------------------------
    # 非抽象方法（子类可直接使用）
    # ------------------------------------------------------------------

    def get_param_doc(self, param_name: str, schema: SchemaRegistry) -> str | None:
        """查询指定参数的 Markdown 文档字符串。

        通过 SchemaRegistry 查找 ParamTag 并调用 ``to_markdown()``。
        若参数不存在则返回 None。

        Parameters
        ----------
        param_name:
            参数名（大小写不敏感）。
        schema:
            Schema 注册中心。
        """
        tag = schema.get_tag(self.software_name, param_name)
        return tag.to_markdown() if tag else None
