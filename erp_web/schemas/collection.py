"""采集验证与商品推送的边界契约，不包含凭据或浏览器连接地址。"""
from typing import Literal, NotRequired, TypedDict

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class DraftClaimTarget(TypedDict):
    """用户勾选的销售市场，语言必须与市场注册表一致。"""

    platform: str
    site: str
    language: str


class CollectionVerification(TypedDict):
    browser_tab_id: str
    source_url: str
    platform: str


class CollectionVerificationStatus(TypedDict):
    ok: bool
    status: Literal["waiting_verification", "loading", "ready", "unavailable"]
    message: str


class CollectionLinkInput(BaseModel):
    url: str
    mode: Literal['browser', 'api'] = 'browser'

    @field_validator('url')
    @classmethod
    def normalize_url(cls, value):
        url = AnyHttpUrl(value.strip())
        if url.username or url.password or '\\' in value or any(c.isspace() for c in value.strip()):
            raise ValueError('仅支持不含账号密码的 HTTP / HTTPS 商品链接。')
        return str(url).partition('#')[0]

    @field_validator('mode')
    @classmethod
    def supported_mode(cls, value, info):
        host = AnyHttpUrl(info.data['url']).host if 'url' in info.data else ''
        if value == 'api' and host != '1688.com' and not (host or '').endswith('.1688.com'):
            raise ValueError('API 采集仅支持 1688 商品链接。')
        return value


class LegacyCollectionLink(CollectionLinkInput):
    status: Literal['pending', 'failed', 'unconfirmed', 'waiting_verification', 'success'] = 'pending'
    title: str = ''
    image: str = ''
    productId: str = ''
    error: str = ''
    errorCode: str = ''
    nextAction: str = ''
    verification: dict[str, str] | None = None


class CollectionLinksAddResult(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    added: int = Field(ge=0, description='本次新加入待采集列表的链接数。')
    duplicates: int = Field(ge=0, description='跳过的重复链接数，已有记录保留原方式和状态。')
    urls: tuple[str, ...] = Field(description='规范化后的去重地址，包含已存在的链接。')


class CollectionLinkQuery(BaseModel):
    model_config = {'extra': 'forbid'}

    id: str = Field(min_length=1, max_length=64)


CollectionStatus = Literal['pending', 'running', 'waiting_verification', 'success', 'failed', 'unconfirmed']


class CollectionLink(TypedDict):
    id: str
    revision: int
    url: str
    mode: Literal['browser', 'api']
    status: CollectionStatus
    platform: str
    createdAt: str | None
    updatedAt: str
    title: NotRequired[str]
    productId: NotRequired[str]
    image: NotRequired[str]
    price: NotRequired[str]
    currency: NotRequired[str]
    skuCount: NotRequired[int]
    imageCount: NotRequired[int]
    partial: NotRequired[bool]
    error: NotRequired[str]
    errorCode: NotRequired[str]
    message: NotRequired[str]
    nextAction: NotRequired[str]
    screenshot: NotRequired[str]
    snapshot: NotRequired[str]


class CollectionLinkEvent(TypedDict):
    at: str
    status: CollectionStatus
    message: str
    code: str
