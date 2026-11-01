"""Yandex 禁售类目导入和持久化契约。"""
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ForbiddenCategory(TypedDict):
    category_id: str
    name: str


class ForbiddenCategories(TypedDict):
    platform: Literal["yandex"]
    filename: str
    imported_at: str
    categories: list[ForbiddenCategory]


class CategoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    name: str = Field(min_length=1, max_length=1000)


class ForbiddenCategoryConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: Literal["yandex"] = "yandex"
    filename: str = Field(default="", max_length=255)
    imported_at: str = ""
    categories: list[CategoryEntry] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def unique_categories(self):
        ids = [item.category_id for item in self.categories]
        if len(ids) != len(set(ids)):
            raise ValueError("禁售类目 ID 不能重复")
        return self


class ImportForbiddenCategoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: Literal["yandex"]
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=4 * 1024 * 1024)


__all__ = ["ForbiddenCategory", "ForbiddenCategories", "CategoryEntry",
           "ForbiddenCategoryConfiguration", "ImportForbiddenCategoriesRequest"]
