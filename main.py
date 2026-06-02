from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "minimaxai/minimax-m2.7"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"


IMAGE_SCENES = [
    ("01_main", "平台合规主图，纯净背景，商品完整居中，强调真实外观"),
    ("02_angle", "三分之四角度，展示轮廓、厚度和关键结构"),
    ("03_detail", "核心卖点特写，突出材质、按钮、接口、纹理或工艺"),
    ("04_scale", "尺寸比例图，使用手、桌面或常见物体辅助理解大小"),
    ("05_use_case", "真实使用场景图，展示目标用户如何使用产品"),
    ("06_feature_1", "卖点一的信息图，少量文字，画面清爽"),
    ("07_feature_2", "卖点二的信息图，展示过程或结构示意"),
    ("08_package", "包装清单图，展示随盒配件，排列整齐"),
    ("09_variant", "颜色、规格或角度组合图，帮助买家比较"),
    ("10_lifestyle", "轻场景氛围图，用于提升点击和购买想象")
]


@dataclass(frozen=True)
class PlatformPlan:
    key: str
    preset: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_json_text(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


def image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


def openai_client():
    from openai import OpenAI

    base_url = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
    if base_url:
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return OpenAI(base_url=base_url)
    return OpenAI()


def nvidia_chat_endpoint() -> str:
    base_url = os.getenv("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def nvidia_chat_text(prompt: str, model: str = NVIDIA_DEFAULT_MODEL) -> str:
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("缺少 NVIDIA_API_KEY 环境变量。")
    model = (model or os.getenv("NVIDIA_MODEL") or NVIDIA_DEFAULT_MODEL).strip()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an ecommerce listing assistant. Return only valid JSON when asked for JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "top_p": 0.7,
        "max_tokens": 4096,
    }
    request = urllib.request.Request(
        nvidia_chat_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403:
            raise RuntimeError(
                "NVIDIA 返回 403 Forbidden：这个 nvapi Key 暂时不能访问 NVIDIA hosted NIM API。"
                "常见原因是账号没有 Public API Endpoints 权限、Key 不是从 build.nvidia.com/API Catalog 当前账号生成，"
                "或 NVIDIA 侧服务权限未开通。请在 NVIDIA API Catalog 重新生成 Key，或联系 NVIDIA 开通 hosted API 权限。"
            ) from exc
        raise RuntimeError(f"NVIDIA API failed: {exc.code} {detail}") from exc
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise RuntimeError(f"NVIDIA API 未返回 choices: {data}")
    return choices[0].get("message", {}).get("content", "").strip()


def nvidia_chat_json(prompt: str, model: str = NVIDIA_DEFAULT_MODEL) -> dict[str, Any]:
    return parse_json_text(nvidia_chat_text(prompt, model))


def deepseek_chat_endpoint() -> str:
    base_url = os.getenv("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def deepseek_chat_text(prompt: str, model: str = DEEPSEEK_DEFAULT_MODEL) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY 环境变量。")
    model = (model or os.getenv("DEEPSEEK_MODEL") or DEEPSEEK_DEFAULT_MODEL).strip()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an ecommerce listing assistant. Return only valid JSON when asked for JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    request = urllib.request.Request(
        deepseek_chat_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in (401, 403):
            raise RuntimeError(
                "DeepSeek API 验证失败：请检查 API Key 是否完整、账号是否已充值/开通 API、Base URL 是否为 "
                "https://api.deepseek.com。"
            ) from exc
        raise RuntimeError(f"DeepSeek API failed: {exc.code} {detail}") from exc
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise RuntimeError(f"DeepSeek API 未返回 choices: {data}")
    return choices[0].get("message", {}).get("content", "").strip()


def deepseek_chat_json(prompt: str, model: str = DEEPSEEK_DEFAULT_MODEL) -> dict[str, Any]:
    return parse_json_text(deepseek_chat_text(prompt, model))


def merge_product(existing: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing, ensure_ascii=False))
    for key in [
        "name",
        "brand",
        "category",
        "target_customer",
        "dimensions",
    ]:
        value = extracted.get(key)
        if value:
            merged[key] = value

    for key in [
        "materials",
        "colors",
        "selling_points",
        "package_includes",
        "avoid_claims",
    ]:
        values = extracted.get(key)
        if values:
            merged[key] = unique_words([*merged.get(key, []), *values])

    terms = merged.setdefault("marketplace_terms", {})
    extracted_terms = extracted.get("marketplace_terms", {})
    for platform_key in ["mercadolibre", "wildberries"]:
        current = terms.setdefault(platform_key, {})
        incoming = extracted_terms.get(platform_key, {})
        for field in ["language", "product_type"]:
            if incoming.get(field):
                current[field] = incoming[field]
        for field in ["primary_keywords", "attribute_keywords"]:
            if incoming.get(field):
                current[field] = unique_words([*current.get(field, []), *incoming[field]])

    notes = extracted.get("recognition_notes")
    if notes:
        merged["recognition_notes"] = notes
    return merged


def analyze_product_info(
    existing: dict[str, Any],
    source_text: str,
    image_paths: list[Path],
    model: str = "gpt-5.4-mini",
    provider: str = "openai",
    nvidia_model: str = NVIDIA_DEFAULT_MODEL,
    deepseek_model: str = DEEPSEEK_DEFAULT_MODEL,
) -> dict[str, Any]:
    provider = (provider or "openai").lower()
    source_text = (source_text or "").strip()
    if not source_text:
        raise ValueError("source_text is empty")

    schema = """{
  "name": "",
  "brand": "",
  "category": "",
  "target_customer": "",
  "materials": [],
  "dimensions": "",
  "colors": [],
  "selling_points": [],
  "package_includes": [],
  "avoid_claims": [],
  "marketplace_terms": {
    "mercadolibre": {
      "language": "es-MX",
      "product_type": "",
      "primary_keywords": [],
      "attribute_keywords": []
    },
    "wildberries": {
      "language": "ru-RU",
      "product_type": "",
      "primary_keywords": [],
      "attribute_keywords": []
    }
  },
  "recognition_notes": []
}"""

    base_prompt = f"""You are extracting structured product data for cross-border marketplace listing generation.

Rules:
- Return valid JSON only. No markdown, no prose.
- You MUST always return these 8 core fields with these exact keys:
  name, brand, category, target_customer, dimensions, materials, package_includes, selling_points
- If a value cannot be confirmed, keep the key and return "" or [].
- Do not invent unsupported facts.
- selling_points must be short Chinese bullet lines suitable for directly filling a UI form.
- package_includes must list only items included in the package.
- materials must be a list of material names.
- Generate marketplace keywords for Mercado Libre in Spanish and Wildberries in Russian.
- Put uncertainty or missing details into recognition_notes.

Existing product JSON:
{json.dumps(existing, ensure_ascii=False, indent=2)}

Supplier page text / OCR / bullets:
{source_text}

Return exactly this JSON structure:
{schema}
"""

    if provider in ("nvidia", "deepseek"):
        prompt = base_prompt
        if image_paths:
            prompt += "\nReference images exist locally, but this request contains text only.\n"
        if provider == "deepseek":
            extracted = deepseek_chat_json(prompt, deepseek_model)
        else:
            extracted = nvidia_chat_json(prompt, nvidia_model)
        return merge_product(existing, extracted)

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Missing openai dependency, run `pip install -r requirements.txt` first.") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Missing OPENAI_API_KEY environment variable.")

    content: list[dict[str, Any]] = [{"type": "input_text", "text": base_prompt}]
    for path in image_paths:
        if path.exists():
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_to_data_url(path),
                }
            )

    client = openai_client()
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    extracted = parse_json_text(response.output_text)
    return merge_product(existing, extracted)


def product_summary(product: dict[str, Any]) -> str:
    fields = {
        "产品名": product.get("name"),
        "品牌": product.get("brand"),
        "品类": product.get("category"),
        "目标买家": product.get("target_customer"),
        "材质": "、".join(product.get("materials", [])),
        "尺寸": product.get("dimensions"),
        "颜色": "、".join(product.get("colors", [])),
        "核心卖点": "；".join(product.get("selling_points", [])),
        "包装清单": "、".join(product.get("package_includes", [])),
        "禁用表达": "；".join(product.get("avoid_claims", [])),
        "补充信息": product.get("supplemental_info") or product.get("source_text"),
    }
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v)


def marketplace_terms(product: dict[str, Any], platform_key: str) -> dict[str, Any]:
    terms = product.get("marketplace_terms", {}).get(platform_key, {})
    return {
        "language": terms.get("language"),
        "product_type": terms.get("product_type") or product.get("name", ""),
        "primary_keywords": terms.get("primary_keywords", []),
        "attribute_keywords": terms.get("attribute_keywords", []),
    }


def unique_words(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = " ".join(str(item).split())
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def fit_title(parts: list[str], max_chars: int) -> str:
    title = ""
    for part in unique_words(parts):
        candidate = f"{title} {part}".strip()
        if len(candidate) <= max_chars:
            title = candidate
    return title


def build_listing_copy(product: dict[str, Any], platform: PlatformPlan) -> dict[str, Any]:
    copy_rules = platform.preset["listing_copy"]
    terms = marketplace_terms(product, platform.key)
    max_chars = copy_rules["title_max_chars"]
    brand = product.get("brand", "")
    primary_keywords = terms["primary_keywords"] or [
        terms["product_type"],
        product.get("category", ""),
        product.get("name", ""),
    ]
    attribute_keywords = terms["attribute_keywords"] or [
        *product.get("colors", [])[:2],
        *product.get("materials", [])[:2],
        product.get("dimensions", ""),
    ]
    selling_points = product.get("selling_points", [])
    package = product.get("package_includes", [])

    title_parts = [
        terms["product_type"],
        brand,
        *(primary_keywords[:2]),
        *(attribute_keywords[:2]),
    ]
    title = fit_title(title_parts, max_chars)

    alt_title_1 = fit_title(
        [terms["product_type"], *(primary_keywords[:3]), *(attribute_keywords[:1])],
        max_chars,
    )
    alt_title_2 = fit_title(
        [terms["product_type"], brand, *(attribute_keywords[:3])],
        max_chars,
    )

    search_keywords = unique_words(primary_keywords + attribute_keywords)
    short_bullets = unique_words(selling_points)[:5]
    package_text = "、".join(package) if package else "以实际包装清单为准"
    keyword_line = " ".join(search_keywords)

    description = "\n".join(
        [
            f"{terms['product_type']}，适合{product.get('target_customer', '目标买家')}使用。",
            "",
            "五点描述草稿:",
            *[f"- {point}" for point in short_bullets],
            f"- 使用场景: {product.get('target_customer', '目标买家')}日常使用。",
            f"- 包装清单: {package_text}",
            "",
            f"材质/规格: {product.get('dimensions', '')}；{'、'.join(product.get('materials', []))}",
            "",
            f"搜索词覆盖: {keyword_line}",
            "说明: 以上内容只使用与产品真实相关的关键词，避免无关流量词和夸张承诺。",
        ]
    )

    prompt = build_copy_prompt(product, platform, search_keywords)
    return {
        "language": terms["language"] or copy_rules["language"],
        "title": title,
        "alt_titles": unique_words([alt_title_1, alt_title_2]),
        "title_max_chars": max_chars,
        "search_keywords": search_keywords,
        "description": description,
        "copy_prompt": prompt,
        "rules": copy_rules,
    }


def build_copy_prompt(
    product: dict[str, Any], platform: PlatformPlan, search_keywords: list[str]
) -> str:
    copy_rules = platform.preset["listing_copy"]
    title_rules = "\n".join(f"- {rule}" for rule in copy_rules["title_rules"])
    description_rules = "\n".join(f"- {rule}" for rule in copy_rules["description_rules"])
    keywords = ", ".join(search_keywords)
    language_name = "墨西哥西语" if platform.key == "mercadolibre" else "俄语"
    description_language = "English" if platform.key == "mercadolibre" else language_name
    market_name = "墨西哥 Mercado Libre" if platform.key == "mercadolibre" else "俄罗斯 Wildberries"
    return f"""请为 {market_name} 生成高曝光、可上架的标题和产品描述。

标题目标语言: {copy_rules["language"]}（{language_name}）
描述目标语言: {description_language}
标题最大字符数: {copy_rules["title_max_chars"]}
标题公式: {copy_rules["title_formula"]}

产品信息:
{product_summary(product)}

建议关键词:
{keywords}

标题规则:
{title_rules}
- 标题必须使用买家真实会搜索的核心品类词，不堆砌重复词。
- 关键词准确优先，高曝光第二；不要放无关热词。
- 优先覆盖: 品类词、关键规格、材质/颜色、适用场景或型号。

描述规则:
{description_rules}
- 描述采用 Amazon 五点描述风格，每点一行。
- 五点必须覆盖: 核心卖点、使用功能、使用场景、规格/材质、包装清单。
- 语言自然，适合当地买家阅读，不要像关键词列表。
- 可以自然埋入长尾关键词，但必须和产品真实相关。
- description 只能输出 {description_language} 的五点描述，不要写中文说明，不要写“关键词覆盖”。
- 每一点都必须是通顺句子，不要堆关键词。

输出 JSON:
{{
  "title": "首字母大写的最推荐标题",
  "alt_titles": ["不带品牌名、不带难翻译名词的备选标题", "另一个准确高曝光备选标题"],
  "search_keywords": ["准确关键词1", "准确关键词2"],
  "description": "五点描述，每点一行，符合目标语言和平台要求"
}}
"""


def build_image_prompt(
    product: dict[str, Any],
    platform: PlatformPlan,
    scene_id: str,
    scene_goal: str,
) -> str:
    preset = platform.preset
    rules = "\n".join(f"- {rule}" for rule in preset["main_image_rules"])
    secondary = "\n".join(f"- {rule}" for rule in preset["secondary_image_style"])
    scene_rules = rules if scene_id == "01_main" else secondary

    return f"""为 {preset["display_name"]} 生成一张电商产品图片。

画布比例/目标尺寸: {preset["canvas"]}
图片编号: {scene_id}
图片目标: {scene_goal}

产品信息:
{product_summary(product)}

平台和画面约束:
{scene_rules}

生成要求:
- 保持产品颜色、结构、材质真实可信。
- 不要生成平台 Logo、竞品 Logo、虚假认证、价格、夸张促销贴纸。
- 如需文字，只使用极短中文占位文案，方便后续替换为目标站点语言。
- 图片应像真实电商摄影或高质量商业渲染，不要像概念艺术。
"""


def build_video_prompt(product: dict[str, Any], platform: PlatformPlan) -> str:
    preset = platform.preset
    video = preset["video"]
    return f"""为 {preset["display_name"]} 生成一条 {video["seconds"]} 秒电商短视频。

视频规格: {video["size"]}
风格: {video["tone"]}

产品信息:
{product_summary(product)}

分镜:
1. 0-2 秒：商品从干净背景中自然出现，先给完整外观。
2. 2-4 秒：切到核心功能或关键结构的近景动作。
3. 4-6 秒：展示真实使用场景，让买家理解尺寸和使用方式。
4. 6-8 秒：回到商品和包装清单，画面稳定、干净、可信。

限制:
- 不出现平台 Logo、价格、二维码、水印、虚假认证。
- 不夸大产品能力，不展示产品信息中没有的功能。
- 保持真实材质、颜色和比例。
"""


def build_plan(product: dict[str, Any], platforms: list[PlatformPlan]) -> dict[str, Any]:
    plan: dict[str, Any] = {"product": product, "platforms": {}}
    for platform in platforms:
        images = []
        for scene_id, scene_goal in IMAGE_SCENES[: platform.preset.get("image_count", 10)]:
            images.append(
                {
                    "id": scene_id,
                    "canvas": platform.preset["canvas"],
                    "api_size": platform.preset["api_size"],
                    "prompt": build_image_prompt(product, platform, scene_id, scene_goal),
                }
            )
        plan["platforms"][platform.key] = {
            "display_name": platform.preset["display_name"],
            "listing": build_listing_copy(product, platform),
            "images": images,
            "video": {
                "size": platform.preset["video"]["size"],
                "seconds": platform.preset["video"]["seconds"],
                "prompt": build_video_prompt(product, platform),
            },
        }
    return plan


ProgressCallback = Callable[[int, int, str], None]


def refine_listing_copy(
    plan: dict[str, Any],
    model: str,
    progress_callback: ProgressCallback | None = None,
    provider: str = "openai",
    nvidia_model: str = NVIDIA_DEFAULT_MODEL,
    deepseek_model: str = DEEPSEEK_DEFAULT_MODEL,
) -> None:
    provider = (provider or "openai").lower()
    client = None
    if provider == "openai":
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise SystemExit("缺少 openai 依赖，请先运行: pip install -r requirements.txt") from exc

        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("缺少 OPENAI_API_KEY 环境变量。")
        client = openai_client()
    elif provider not in ("nvidia", "deepseek"):
        raise RuntimeError(f"不支持的模型通道: {provider}")

    total = len(plan["platforms"])
    for index, (platform_key, platform) in enumerate(plan["platforms"].items(), start=1):
        listing = platform["listing"]
        if progress_callback:
            progress_callback(index - 1, total, f"正在精修文案: {platform_key}")
        print(f"Refining listing copy for {platform_key}...")
        if provider == "nvidia":
            raw_text = nvidia_chat_text(listing["copy_prompt"], nvidia_model)
        elif provider == "deepseek":
            raw_text = deepseek_chat_text(listing["copy_prompt"], deepseek_model)
        else:
            response = client.responses.create(
                model=model,
                input=listing["copy_prompt"],
            )
            raw_text = response.output_text.strip()
        try:
            refined = parse_json_text(raw_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{platform_key} 文案返回值不是合法 JSON: {raw_text}") from exc

        listing.update(
            {
                "title": refined.get("title", listing["title"]),
                "alt_titles": refined.get("alt_titles", listing["alt_titles"]),
                "search_keywords": refined.get(
                    "search_keywords", listing["search_keywords"]
                ),
                "description": refined.get("description", listing["description"]),
                "refined_by": model,
            }
        )
        if progress_callback:
            progress_callback(index, total, f"文案精修完成: {platform_key}")


def write_storyboard(plan: dict[str, Any], out_dir: Path) -> None:
    lines = ["# Marketplace Media Storyboard", ""]
    for key, platform in plan["platforms"].items():
        listing = platform["listing"]
        lines.extend(
            [
                f"## {platform['display_name']} ({key})",
                "",
                "### 标题和描述",
                "",
                f"- 推荐标题: {listing['title']}",
                f"- 标题长度: {len(listing['title'])}/{listing['title_max_chars']}",
                f"- 备选标题: {' | '.join(listing['alt_titles'])}",
                f"- 关键词: {', '.join(listing['search_keywords'])}",
                "",
                listing["description"],
                "",
                "### 图片",
                "",
            ]
        )
        for image in platform["images"]:
            goal_line = next(
                line for line in image["prompt"].splitlines() if line.startswith("图片目标:")
            )
            first_line = goal_line.replace("图片目标: ", "")
            lines.append(f"- `{image['id']}` {image['canvas']}: {first_line}")
        lines.extend(["", "### 视频", "", platform["video"]["prompt"], ""])
    (out_dir / "storyboard.md").write_text("\n".join(lines), encoding="utf-8")


def save_b64_image(data: str, path: Path) -> None:
    path.write_bytes(base64.b64decode(data))


def save_image_result(image_data: Any, path: Path) -> None:
    b64_json = getattr(image_data, "b64_json", None)
    if not b64_json and isinstance(image_data, dict):
        b64_json = image_data.get("b64_json")
    if b64_json:
        save_b64_image(b64_json, path)
        return

    url = getattr(image_data, "url", None)
    if not url and isinstance(image_data, dict):
        url = image_data.get("url")
    if url:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())
        return

    raise RuntimeError("图片接口没有返回 b64_json 或 url，无法保存图片。")


def generate_image_with_optional_references(client: Any, prompt: str, size: str, source_images: list[Path]) -> Any:
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
    if source_images:
        opened = [p.open("rb") for p in source_images]
        try:
            return client.images.edit(
                model=model,
                image=opened,
                prompt=prompt,
                size=size,
            )
        except Exception as exc:
            print(f"images.edits failed, fallback to images.generations: {exc}")
        finally:
            for f in opened:
                f.close()
        prompt = (
            prompt
            + "\n\nReference image note: the upstream /images/edits endpoint failed, so generate from text only. "
            + "Preserve the product appearance, materials, color, structure and proportions described in the product information. "
            + f"The seller provided {len(source_images)} reference product images."
        )
    return client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
    )


def media_language_instruction(language: str) -> str:
    language = (language or "").strip().lower()
    mapping = {
        "俄语": "Russian",
        "russian": "Russian",
        "ru": "Russian",
        "西班牙语": "Spanish",
        "spanish": "Spanish",
        "es": "Spanish",
        "英语": "English",
        "english": "English",
        "en": "English",
    }
    target = mapping.get(language, "")
    if not target:
        return ""
    return (
        "\nVisible text rule: if the image or video contains labels, callouts, "
        f"or promotional text, write them in natural {target} only."
    )


def generate_assets(
    plan: dict[str, Any],
    product: dict[str, Any],
    out_dir: Path,
    progress_callback: ProgressCallback | None = None,
    *,
    generate_images: bool = True,
    generate_videos: bool = True,
    image_count: int | None = None,
    prompt_language: str = "",
) -> None:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("缺少 openai 依赖，请先运行: pip install -r requirements.txt") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("缺少 OPENAI_API_KEY 环境变量。")

    client = openai_client()
    source_images = [
        Path(p) for p in product.get("source_images", []) if Path(p).exists()
    ]

    language_note = media_language_instruction(prompt_language)
    platform_images: dict[str, list[dict[str, Any]]] = {}
    for platform_key, platform in plan["platforms"].items():
        images = list(platform.get("images", []))
        if image_count is not None:
            images = images[: max(0, image_count)]
        platform_images[platform_key] = images

    total_steps = 0
    if generate_images:
        total_steps += sum(len(images) for images in platform_images.values())
    if generate_videos:
        total_steps += sum(1 for platform in plan["platforms"].values() if platform.get("video"))
    current_step = 0

    for platform_key, platform in plan["platforms"].items():
        platform_dir = out_dir / platform_key
        platform_dir.mkdir(parents=True, exist_ok=True)

        if generate_images:
            for image in platform_images[platform_key]:
                if progress_callback:
                    progress_callback(
                        current_step,
                        total_steps,
                        f"正在生成图片: {platform_key}/{image['id']}",
                    )
                print(f"Generating {platform_key}/{image['id']}...")
                prompt = image["prompt"] + language_note
                result = generate_image_with_optional_references(
                    client,
                    prompt,
                    image["api_size"],
                    source_images,
                )
                save_image_result(result.data[0], platform_dir / f"{image['id']}.png")
                current_step += 1
                if progress_callback:
                    progress_callback(
                        current_step,
                        total_steps,
                        f"图片完成: {platform_key}/{image['id']}",
                    )

        if not generate_videos or not platform.get("video"):
            continue

        if progress_callback:
            progress_callback(current_step, total_steps, f"正在生成视频: {platform_key}")
        print(f"Generating {platform_key}/video...")
        video = platform["video"]
        video_result = client.videos.create(
            model="sora-2-pro",
            prompt=video["prompt"] + language_note,
            size=video["size"],
            seconds=str(video["seconds"]),
        )
        video_id = video_result.id

        while True:
            status = client.videos.retrieve(video_id)
            if status.status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(5)

        if status.status != "completed":
            raise SystemExit(f"视频生成失败: {platform_key}, status={status.status}")

        content = client.videos.download_content(video_id)
        (platform_dir / "video.mp4").write_bytes(content.read())
        current_step += 1
        if progress_callback:
            progress_callback(current_step, total_steps, f"视频完成: {platform_key}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate marketplace image/video plans.")
    parser.add_argument("--product", required=True, help="产品 JSON 文件路径")
    parser.add_argument("--platform", default="all", choices=["all", "mercadolibre", "wildberries"])
    parser.add_argument("--out", default="output", help="输出目录")
    parser.add_argument("--analyze-product", action="store_true", help="根据文案和图片自动识别产品信息")
    parser.add_argument("--source-text", default="", help="供应商文案/说明书/OCR 文本")
    parser.add_argument("--source-text-file", default="", help="包含供应商文案的文本文件")
    parser.add_argument("--analysis-model", default="gpt-5.4-mini", help="产品识别模型")
    parser.add_argument("--generate-copy", action="store_true", help="调用 OpenAI API 精修标题和描述")
    parser.add_argument("--copy-model", default="gpt-5.4-mini", help="标题和描述生成模型")
    parser.add_argument("--generate", action="store_true", help="调用 OpenAI API 真实生成图片和视频")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).parent
    product_path = Path(args.product)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    product = load_json(product_path)
    if args.analyze_product:
        source_text = args.source_text
        if args.source_text_file:
            source_text += "\n" + Path(args.source_text_file).read_text(encoding="utf-8")
        image_paths = [Path(p) for p in product.get("source_images", [])]
        product = analyze_product_info(
            product,
            source_text=source_text,
            image_paths=image_paths,
            model=args.analysis_model,
        )
        product_path.write_text(
            json.dumps(product, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    presets = load_json(root / "presets" / "platforms.json")
    keys = list(presets.keys()) if args.platform == "all" else [args.platform]
    platforms = [PlatformPlan(key=k, preset=presets[k]) for k in keys]
    plan = build_plan(product, platforms)

    if args.generate_copy:
        refine_listing_copy(plan, args.copy_model)

    (out_dir / "media_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_storyboard(plan, out_dir)

    if args.generate:
        generate_assets(plan, product, out_dir)

    print(f"Done. Files written to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
