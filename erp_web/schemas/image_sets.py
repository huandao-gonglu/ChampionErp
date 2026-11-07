"""套图的可编辑设计方案；图片执行进度仍归既有图片批次。"""

from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

ImageSetRole = Literal["main", "detail", "scene", "selling_point", "material", "size", "package"]


class ImageSetPagePayload(TypedDict):
    key: str
    title: str
    role: ImageSetRole
    prompt: str
    source_image_ids: list[str]
    scope: NotRequired[Literal["common", "sku"]]
    sku_ids: NotRequired[list[str]]


class ImageSetPlanPayload(TypedDict):
    style: str
    pages: list[ImageSetPagePayload]


class ImageSetPage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=100, description="供用户查看的中文图片名称")
    role: ImageSetRole
    prompt: str = Field(min_length=1, max_length=4000, description="该图的构图、事实依据、目标语言文案和视觉要求")
    source_image_ids: list[str] = Field(min_length=1, max_length=12, description="支持该画面的参考素材 ID")
    scope: Literal["common", "sku"] = "common"
    sku_ids: list[str] = Field(default_factory=list, description="SKU 图对应的规格 ID；共用图为空")


class ImageSetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    style: str = Field(min_length=1, max_length=2000, description="整套图片共同的配色、字体、布局和视觉风格")
    pages: list[ImageSetPage] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_pages(self):
        if len({page.key for page in self.pages}) != len(self.pages):
            raise ValueError("套图的页面标识不能重复。")
        common = [page for page in self.pages if page.scope == "common"]
        if common and not 2 <= len(common) <= 8:
            raise ValueError("共用套图需要 2 至 8 张图片。")
        if common and (common[0].role != "main" or any(page.role == "main" for page in common[1:])):
            raise ValueError("套图必须且只能以一张主图开始。")
        sku_ids = []
        for page in self.pages:
            if page.scope == "common" and page.sku_ids:
                raise ValueError("共用图不能绑定独立 SKU。")
            if page.scope == "sku":
                if not page.sku_ids or page.role != "main" or len(page.source_image_ids) != 1:
                    raise ValueError("SKU 图必须绑定规格，并以该规格的一张原图作为参考。")
                sku_ids.extend(page.sku_ids)
        if len(set(sku_ids)) != len(sku_ids):
            raise ValueError("每个 SKU 只能对应一张规格图。")
        return self
