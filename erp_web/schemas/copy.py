"""商品本地化文案的模型输出 Schema。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


CopyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class LocalizedCopyOutput(BaseModel):
    """通用目标市场文案。"""

    model_config = ConfigDict(extra="forbid")

    title: CopyText = Field(description="目标市场语言的商品标题")
    description: CopyText = Field(
        description="目标市场语言的完整商品描述；将有事实依据的优势、用途、规格与包装清单组织成自然段或要点，自然覆盖相关搜索词，禁止堆砌关键词或编造信息"
    )
    alt_titles: list[CopyText] = Field(
        default_factory=list,
        max_length=3,
        description="目标市场语言的备选标题",
    )
    search_keywords: list[CopyText] = Field(
        default_factory=list,
        max_length=20,
        description="目标市场语言的搜索关键词",
    )


class MercadoLibreCbtLocalizedCopyOutput(LocalizedCopyOutput):
    """包含传统 CBT 根刊登英文标题的 Mercado Libre 文案。"""

    global_title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=60),
    ] = Field(
        description=(
            "Mercado Libre CBT 根 global item 使用的简洁英文标题；"
            "即使其他字段使用目标市场语言，本字段也必须使用英文"
        )
    )


__all__ = [
    "LocalizedCopyOutput",
    "MercadoLibreCbtLocalizedCopyOutput",
]
