from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate marketplace media plans and storyboards.")
    parser.add_argument("--product", required=True, help="产品 JSON 文件路径")
    parser.add_argument("--platform", default="all", choices=["all", "mercadolibre", "wildberries"])
    parser.add_argument("--out", default="output", help="输出目录")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).parent
    product_path = Path(args.product)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    product = load_json(product_path)
    presets = load_json(root / "presets" / "platforms.json")
    keys = list(presets.keys()) if args.platform == "all" else [args.platform]
    platforms = [PlatformPlan(key=k, preset=presets[k]) for k in keys]
    plan = build_plan(product, platforms)

    (out_dir / "media_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_storyboard(plan, out_dir)

    print(f"Done. Files written to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
