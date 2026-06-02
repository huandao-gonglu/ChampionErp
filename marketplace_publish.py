from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
ML_CATEGORY_CACHE_PATH = APP_DIR / "output" / "ml_category_cache.json"
ML_CATEGORY_TREE_PATH = APP_DIR / "output" / "ml_category_tree.json"
ML_CATEGORY_SHIPPING_CACHE_PATH = APP_DIR / "output" / "ml_category_shipping_cache.json"

ML_CATEGORY_WORDS = {
    "Home, Furniture and Garden": "家居、家具和花园",
    "Garden Tools": "花园工具",
    "Garden Multi Tools": "花园多功能工具",
    "Camping Equipment": "露营装备",
    "Sports and Fitness": "运动与健身",
    "Kitchen & Housewares": "厨房和家居用品",
    "Health and Health Supplies": "健康与医疗用品",
    "Industrial and Offices": "工业与办公",
    "Home": "家居",
    "Furniture": "家具",
    "Garden": "花园",
    "Kitchen": "厨房",
    "Housewares": "家居用品",
    "Tools": "工具",
    "Tool": "工具",
    "Storage": "收纳",
    "Organization": "整理收纳",
    "Sports": "运动",
    "Fitness": "健身",
    "Camping": "露营",
    "Hunting": "狩猎",
    "Fishing": "钓鱼",
    "Equipment": "装备",
    "Accessories": "配件",
    "Cycling": "骑行",
    "Medical": "医疗",
    "Dental": "牙科",
    "Industrial": "工业",
    "Office": "办公",
    "School": "学校",
    "Supplies": "用品",
    "Games": "游戏",
    "Toys": "玩具",
    "Workshop": "工作间",
    "Baking": "烘焙",
    "Utensils": "器具",
    "Tables": "桌子",
    "Chairs": "椅子",
    "Stools": "凳子",
    "Multi-function": "多功能",
    "Multi": "多",
    "function": "功能",
    "Oscillating": "摆动",
    "Carving": "雕刻",
}

ML_CATEGORY_CN_HINTS = {
    "Home": "家居",
    "Furniture": "家具",
    "Garden": "园艺",
    "Kitchen": "厨房",
    "Housewares": "家居用品",
    "Storage": "收纳",
    "Organization": "整理收纳",
    "Beauty": "美妆",
    "Health": "健康",
    "Sports": "运动",
    "Outdoors": "户外",
    "Tools": "工具",
    "Automotive": "汽车",
    "Toys": "玩具",
    "Baby": "母婴",
    "Clothing": "服装",
    "Shoes": "鞋",
    "Jewelry": "饰品",
    "Lighting": "照明",
    "Bath": "浴室",
    "Cleaning": "清洁",
    "Pet": "宠物",
    "Electronics": "电子",
    "Computers": "电脑",
    "Office": "办公",
}

CN_CATEGORY_TERMS = {
    "瓶": ["bottle", "bottles", "botella", "botellas", "frasco"],
    "水瓶": ["water bottle", "botella de agua", "termo"],
    "酒瓶": ["liquor flask", "botella de licor", "flask"],
    "杯": ["cup", "mug", "vaso", "taza"],
    "厨房": ["kitchen", "cocina"],
    "厨具": ["kitchenware", "utensilios de cocina"],
    "烘焙": ["baking", "reposteria"],
    "收纳": ["storage", "organization", "almacenamiento", "organizacion"],
    "家居": ["home", "hogar"],
    "家具": ["furniture", "muebles"],
    "工具": ["tools", "herramientas"],
    "玩具": ["toys", "juguetes"],
    "宠物": ["pet", "pets", "mascotas"],
    "汽车": ["car", "auto", "automotriz"],
    "手机": ["cell phone", "mobile phone", "celular"],
    "电脑": ["computer", "computadora"],
    "服装": ["clothing", "ropa"],
    "鞋": ["shoes", "zapatos"],
    "包": ["bag", "bags", "bolsa"],
    "饰品": ["jewelry", "joyeria", "accessories"],
    "灯": ["lighting", "lamp", "lampara"],
    "浴室": ["bathroom", "bano"],
    "清洁": ["cleaning", "limpieza"],
    "运动": ["sports", "deportes"],
    "户外": ["outdoor", "aire libre"],
    "母婴": ["baby", "bebes"],
    "美容": ["beauty", "belleza"],
    "化妆": ["makeup", "maquillaje"],
}

CN_WB_TERMS = {
    "瓶": ["бутылка", "бутылки", "флакон", "термос"],
    "水瓶": ["бутылка для воды", "термос"],
    "杯": ["кружка", "стакан", "чашка"],
    "厨房": ["кухня", "кухонные принадлежности"],
    "厨具": ["посуда", "кухонные принадлежности"],
    "收纳": ["хранение", "органайзер"],
    "家居": ["дом", "товары для дома"],
    "工具": ["инструменты"],
    "玩具": ["игрушки"],
    "宠物": ["товары для животных"],
    "汽车": ["авто", "автотовары"],
    "手机": ["телефон", "смартфон"],
    "服装": ["одежда"],
    "鞋": ["обувь"],
    "包": ["сумка"],
    "饰品": ["бижутерия", "аксессуары"],
    "灯": ["лампа", "освещение"],
    "浴室": ["ванная"],
    "清洁": ["уборка"],
    "运动": ["спорт"],
    "户外": ["туризм"],
    "母婴": ["детские товары"],
    "美容": ["красота"],
}


def has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def expanded_category_keywords(keyword: str, mapping: dict[str, list[str]]) -> list[str]:
    raw = " ".join(keyword.split())
    terms: list[str] = []
    ascii_term = re.sub(r"[^\w\s-]", " ", raw, flags=re.ASCII)
    ascii_term = " ".join(ascii_term.split())
    if ascii_term:
        terms.append(ascii_term)
    for cn, mapped in mapping.items():
        if cn in raw:
            terms.extend(mapped)
    if not terms and has_cjk(raw):
        for char in raw:
            terms.extend(mapping.get(char, []))
    unique: list[str] = []
    for term in terms:
        term = term.strip()
        if term and term.lower() not in [item.lower() for item in unique]:
            unique.append(term)
    return unique


def load_ml_category_cache() -> dict[str, list[list[str]]]:
    try:
        if ML_CATEGORY_CACHE_PATH.exists():
            data = json.loads(ML_CATEGORY_CACHE_PATH.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def save_ml_category_cache(cache: dict[str, list[list[str]]]) -> None:
    try:
        ML_CATEGORY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ML_CATEGORY_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def cached_mercadolibre_categories(keyword: str) -> list[tuple[str, str]]:
    needle = keyword.strip().casefold()
    if not needle:
        return []
    cache = load_ml_category_cache()
    if ML_CATEGORY_TREE_PATH.exists():
        try:
            tree = json.loads(ML_CATEGORY_TREE_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(tree, dict):
                cache = {**tree, **cache}
        except Exception:
            pass
    matches: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, rows in cache.items():
        if needle not in key.casefold() and key.casefold() not in needle:
            continue
        for row in rows:
            if isinstance(row, list) and len(row) >= 2 and str(row[0]) not in seen:
                seen.add(str(row[0]))
                matches.append((str(row[0]), str(row[1])))
    return matches[:50]


def localize_mercadolibre_category_path(path: str) -> str:
    cn_parts: list[str] = []
    for part in [item.strip() for item in path.split("/") if item.strip()]:
        hit = ""
        for en, cn in sorted(ML_CATEGORY_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(en)}\b", part, flags=re.I):
                hit = re.sub(rf"\b{re.escape(en)}\b", cn, part, flags=re.I)
                break
        for en, cn in ML_CATEGORY_CN_HINTS.items():
            if not hit and en.casefold() in part.casefold():
                hit = cn
                break
        cn_parts.append(hit or part)
    cn_path = " / ".join(cn_parts)
    return f"{cn_path}  |  {path}" if cn_path and cn_path != path else path


def sync_mercadolibre_category_tree(token: str = "", max_nodes: int = 1200) -> int:
    roots = request_json("GET", "https://api.mercadolibre.com/sites/MLM/categories", token)
    queue: list[tuple[str, str]] = []
    for item in roots if isinstance(roots, list) else []:
        cid = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if cid:
            queue.append((cid, name))
    cache: dict[str, list[list[str]]] = {}
    count = 0
    while queue and count < max_nodes:
        category_id, fallback_name = queue.pop(0)
        try:
            data = request_json("GET", f"https://api.mercadolibre.com/categories/{category_id}", token)
        except Exception:
            continue
        path_items = data.get("path_from_root", []) if isinstance(data, dict) else []
        path = " / ".join(str(item.get("name") or "").strip() for item in path_items if isinstance(item, dict))
        label = localize_mercadolibre_category_path(path or fallback_name)
        search_key = " ".join([label, category_id, fallback_name]).strip()
        cache.setdefault(search_key, []).append([category_id, label])
        count += 1
        children = data.get("children_categories", []) if isinstance(data, dict) else []
        for child in children:
            child_id = str(child.get("id") or "")
            child_name = str(child.get("name") or "")
            if child_id:
                queue.append((child_id, child_name))
    ML_CATEGORY_TREE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ML_CATEGORY_TREE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return count


def load_store_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "mercadolibre": {
            "access_token": "",
            "app_id": "",
            "app_secret": "",
            "code_verifier": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
            "refresh_token": "",
            "redirect_uri": "",
            "category_id": "",
            "site_id": "MLM",
            "shop_name": "",
        },
        "wildberries": {
            "content_token": "",
            "prices_token": "",
            "marketplace_token": "",
            "stocks_token": "",
            "subject_id": "",
            "shop_name": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
        },
        "ozon": {
            "client_id": "",
            "api_key": "",
            "category_id": "",
            "shop_name": "",
            "auth_status": "",
            "auth_checked_at": "",
            "auth_masked_account": "",
            "auth_error_code": "",
            "auth_error_message": "",
            "auth_next_action": "",
        },
        "listing": {
            "mercadolibre_price": "",
            "wildberries_price": "",
            "ozon_price": "",
            "stock": "10",
            "sku": "",
            "currency_id": "MXN",
            "condition": "new",
            "listing_type_id": "gold_special",
            "mercadolibre_logistic_type": "remote",
        },
    }


def save_store_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def bearer_auth_value(token: str) -> str:
    value = token.strip()
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    value = "".join(value.split())
    return f"Bearer {value}" if value else ""


def request_json(
    method: str,
    url: str,
    token: str = "",
    payload: dict[str, Any] | list[Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = bearer_auth_value(token)
    if extra_headers:
        headers.update(extra_headers)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def request_form_json(method: str, url: str, payload: dict[str, str]) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def upload_mercadolibre_picture(path: str | Path, token: str) -> dict[str, Any]:
    image_path = Path(path)
    if not image_path.exists():
        raise RuntimeError(f"图片文件不存在: {image_path}")
    boundary = "----CodexBoundary" + secrets.token_hex(12)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    data = header + image_path.read_bytes() + footer
    request = urllib.request.Request(
        "https://api.mercadolibre.com/pictures/items/upload",
        data=data,
        headers={
            "Authorization": bearer_auth_value(token),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST Mercado Libre picture upload failed: {exc.code} {detail}") from exc


def extract_oauth_code(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("code"):
        return query["code"][0]
    if value.startswith("code="):
        return value.split("=", 1)[1].split("&", 1)[0]
    if parsed.scheme and parsed.netloc:
        return ""
    return value if len(value) > 20 and "/" not in value and " " not in value else ""


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def exchange_mercadolibre_code(
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code_or_url: str,
    code_verifier: str = "",
) -> dict[str, str]:
    code = extract_oauth_code(code_or_url)
    if not app_id or not app_secret or not redirect_uri or not code or not code_verifier:
        raise RuntimeError(
            "缺少 App ID、App Secret、Redirect URI、授权 code 或 Code Verifier。"
            "请粘贴授权后浏览器地址栏里包含 ?code= 的完整地址，"
            "并且必须先用软件里的“复制美客多授权链接”生成新的授权链接。"
        )
    data = request_form_json(
        "POST",
        "https://api.mercadolibre.com/oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"未返回 access_token: {data}")
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or ""),
        "user_id": str(data.get("user_id") or ""),
    }


def refresh_mercadolibre_token(app_id: str, app_secret: str, refresh_token: str) -> dict[str, str]:
    if not app_id or not app_secret or not refresh_token:
        raise RuntimeError("缺少美客多 App ID、App Secret 或 Refresh Token，无法自动刷新 Access Token。")
    data = request_form_json(
        "POST",
        "https://api.mercadolibre.com/oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "refresh_token": refresh_token,
        },
    )
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"美客多刷新 Token 未返回 access_token: {data}")
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or refresh_token),
        "user_id": str(data.get("user_id") or ""),
    }


def is_mercadolibre_auth_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in [
            "401",
            "unauthorized",
            "invalid access token",
            "invalid_token",
            "invalid grant",
            "invalid_grant",
        ]
    )


def number_or_zero(value: Any) -> float:
    try:
        text = str(value or "").replace(",", ".").strip()
        if not text:
            return 0.0
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else 0.0
    except Exception:
        return 0.0


def request_ozon_json(
    method: str,
    url: str,
    client_id: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Client-Id": client_id, "Api-Key": api_key}
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def fetch_mercadolibre_shop_name(token: str) -> str:
    data = request_json("GET", "https://api.mercadolibre.com/users/me", token)
    if not isinstance(data, dict):
        return ""
    return data.get("nickname") or data.get("first_name") or str(data.get("id") or "")


def fetch_wildberries_shop_name(token: str) -> str:
    if not token.strip():
        raise RuntimeError("WB Token 为空。")
    try:
        data = request_json("GET", "https://common-api.wildberries.ru/api/v1/seller-info", token)
    except RuntimeError as exc:
        message = str(exc)
        if "429" in message or "Too Many Requests" in message:
            return "WB Token 已保存（接口限流，稍后再查店铺名）"
        if "401" in message or "403" in message:
            raise RuntimeError(
                "WB Token 无法通过正式环境验证。请确认创建令牌时没有勾选 Test Environment/测试环境，"
                "并且复制的是完整 Token。原始错误: " + message
            ) from exc
        raise
    if not isinstance(data, dict):
        return ""
    return data.get("tradeMark") or data.get("name") or data.get("sid") or ""


def search_mercadolibre_categories(keyword: str, token: str = "") -> list[tuple[str, str]]:
    if not keyword.strip():
        raise RuntimeError("请先填写产品名或品类。")
    cached = cached_mercadolibre_categories(keyword)
    if cached:
        return filter_mercadolibre_remote_categories(cached, token)
    results = []
    seen = set()
    terms = expanded_category_keywords(keyword, CN_CATEGORY_TERMS)
    if not terms:
        raise RuntimeError("没有识别到可查询的分类关键词，请换一个中文品类词或输入英文/西语关键词。")
    for term in terms[:8]:
        if token:
            url = "https://api.mercadolibre.com/marketplace/domain_discovery/search?" f"q={urllib.parse.quote(term)}"
            data = request_json("GET", url, token)
        else:
            url = (
                "https://api.mercadolibre.com/sites/MLM/domain_discovery/search?"
                f"limit=8&q={urllib.parse.quote(term)}"
            )
            data = request_json("GET", url)
        for item in data if isinstance(data, list) else []:
            category_id = str(item.get("category_id") or "")
            name = item.get("category_name") or item.get("domain_name") or ""
            path = mercadolibre_category_path(category_id, token) if category_id else ""
            if category_id and category_id not in seen:
                seen.add(category_id)
                label = localize_mercadolibre_category_path(path or name)
                results.append((category_id, f"{label}  |  关键词: {term}"))
    if results:
        results = filter_mercadolibre_remote_categories(results, token)
        cache = load_ml_category_cache()
        cache[keyword.strip()] = [[category_id, label] for category_id, label in results[:50]]
        save_ml_category_cache(cache)
    return results[:50]


def mercadolibre_category_path(category_id: str, token: str = "") -> str:
    if not category_id:
        return ""
    try:
        data = request_json("GET", f"https://api.mercadolibre.com/categories/{category_id}", token)
        path = data.get("path_from_root", []) if isinstance(data, dict) else []
        names = [str(item.get("name") or "").strip() for item in path if isinstance(item, dict)]
        return " / ".join(name for name in names if name)
    except Exception:
        return ""


def load_ml_category_shipping_cache() -> dict[str, dict[str, Any]]:
    try:
        if ML_CATEGORY_SHIPPING_CACHE_PATH.exists():
            data = json.loads(ML_CATEGORY_SHIPPING_CACHE_PATH.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_ml_category_shipping_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        ML_CATEGORY_SHIPPING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ML_CATEGORY_SHIPPING_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def collect_shipping_modes(value: Any) -> set[str]:
    modes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"mode", "modes", "shipping_mode", "shipping_modes", "logistic_type", "logistic_types"}:
                if isinstance(item, str):
                    modes.add(item.lower())
                elif isinstance(item, list):
                    for element in item:
                        if isinstance(element, str):
                            modes.add(element.lower())
                        elif isinstance(element, dict):
                            modes.update(collect_shipping_modes(element))
            modes.update(collect_shipping_modes(item))
    elif isinstance(value, list):
        for item in value:
            modes.update(collect_shipping_modes(item))
    return modes


def mercadolibre_category_remote_status(category_id: str, token: str = "") -> tuple[bool, str]:
    cache = load_ml_category_shipping_cache()
    cached = cache.get(category_id)
    if isinstance(cached, dict) and "supported" in cached:
        return bool(cached.get("supported")), str(cached.get("reason") or "")
    urls = [
        f"https://api.mercadolibre.com/categories/{category_id}/shipping_preferences",
        f"https://api.mercadolibre.com/catalog_categories/{category_id}/shipping_preferences",
    ]
    last_error = ""
    for url in urls:
        try:
            data = request_json("GET", url, token)
        except Exception as exc:
            last_error = str(exc)
            continue
        modes = collect_shipping_modes(data)
        supported = "me1" in modes or "remote" in modes
        reason = "支持 remote/me1" if supported else f"不支持 remote/me1，平台返回模式: {', '.join(sorted(modes)) or '空'}"
        cache[category_id] = {"supported": supported, "reason": reason, "modes": sorted(modes)}
        save_ml_category_shipping_cache(cache)
        return supported, reason
    cache[category_id] = {"supported": False, "reason": f"无法校验物流偏好: {last_error}"}
    save_ml_category_shipping_cache(cache)
    return False, f"无法校验物流偏好: {last_error}"


def filter_mercadolibre_remote_categories(options: list[tuple[str, str]], token: str = "") -> list[tuple[str, str]]:
    if not token:
        return options
    filtered: list[tuple[str, str]] = []
    rejected: list[str] = []
    for category_id, label in options:
        supported, reason = mercadolibre_category_remote_status(category_id, token)
        if supported:
            filtered.append((category_id, f"[可发墨西哥] {label}"))
        else:
            rejected.append(f"{category_id}: {reason}")
    if filtered:
        return filtered
    return [(category_id, f"[未通过发货校验] {label}") for category_id, label in options]


def search_wildberries_subjects(keyword: str, token: str) -> list[tuple[str, str]]:
    if not keyword.strip():
        raise RuntimeError("请先填写产品名或品类。")
    if not token.strip():
        raise RuntimeError("WB Token 为空，请先在授权店铺里保存 WB Token。")
    results = []
    seen = set()
    terms = expanded_category_keywords(keyword, CN_WB_TERMS) or [keyword.strip()]
    for term in terms[:8]:
        data = request_json(
            "GET",
            "https://content-api.wildberries.ru/content/v2/object/all?"
            f"name={urllib.parse.quote(term)}&locale=ru",
            token,
        )
        raw = data.get("data", []) if isinstance(data, dict) else []
        for item in raw:
            subject_id = str(item.get("subjectID") or item.get("objectID") or item.get("id") or "")
            name = item.get("subjectName") or item.get("objectName") or item.get("name") or ""
            if subject_id and subject_id not in seen:
                seen.add(subject_id)
                results.append((subject_id, f"{name}  |  关键词: {term}"))
    return results[:50]


def estimate_mercadolibre_shipping(
    token: str,
    zip_from: str,
    zip_to: str,
    length_cm: str,
    width_cm: str,
    height_cm: str,
    weight_kg: str,
    price: str,
) -> float:
    if not token.strip():
        raise RuntimeError("缺少美客多 Access Token。")
    def num(value: str) -> float:
        return float((value or "0").replace(",", "."))
    length = max(1, int(round(num(length_cm))))
    width = max(1, int(round(num(width_cm))))
    height = max(1, int(round(num(height_cm))))
    grams = max(1, int(round(num(weight_kg) * 1000)))
    params = urllib.parse.urlencode(
        {
            "zip_code_from": zip_from or "01000",
            "zip_code_to": zip_to or "05000",
            "dimensions": f"{length}x{width}x{height},{grams}",
            "item_price": str(num(price)),
        }
    )
    data = request_json("GET", f"https://api.mercadolibre.com/sites/MLM/shipping_options?{params}", token)
    options = data.get("options", []) if isinstance(data, dict) else []
    costs = []
    for option in options:
        cost = option.get("cost") if isinstance(option, dict) else None
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
    if not costs:
        raise RuntimeError(f"未返回美客多运费选项: {data}")
    return round(min(costs), 2)


def fetch_ozon_shop_name(client_id: str, api_key: str) -> str:
    def result_name(data: dict[str, Any]) -> str:
        result = data.get("result") if isinstance(data, dict) else None
        candidates: list[Any] = []
        if isinstance(result, list):
            candidates = result
        elif isinstance(result, dict):
            for key in ("warehouses", "items", "products"):
                values = result.get(key)
                if isinstance(values, list):
                    candidates = values
                    break
        for item in candidates:
            if isinstance(item, dict):
                name = item.get("name") or item.get("warehouse_name") or item.get("offer_id")
                if name:
                    return str(name)
        return ""

    checks = [
        (
            "warehouse/list",
            "https://api-seller.ozon.ru/v1/warehouse/list",
            {},
        ),
        (
            "product/list v3",
            "https://api-seller.ozon.ru/v3/product/list",
            {"filter": {"visibility": "ALL"}, "limit": 1, "last_id": ""},
        ),
        (
            "product/info/stocks",
            "https://api-seller.ozon.ru/v3/product/info/stocks",
            {"filter": {"visibility": "ALL"}, "limit": 1, "last_id": ""},
        ),
    ]
    errors = []
    for name, url, payload in checks:
        try:
            data = request_ozon_json("POST", url, client_id, api_key, payload)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        return result_name(data) or f"Ozon {client_id}"
    raise RuntimeError(" / ".join(errors))


def required_mercadolibre_attributes(category_id: str, token: str) -> list[dict[str, Any]]:
    attrs = mercadolibre_category_attributes(category_id, token)
    required = []
    for attr in attrs if isinstance(attrs, list) else []:
        if attr.get("required"):
            required.append(
                {
                    "id": attr.get("id"),
                    "name": attr.get("name"),
                    "value_type": attr.get("value_type"),
                    "values": attr.get("values", [])[:20],
                }
            )
    return required


def mercadolibre_category_attributes(category_id: str, token: str) -> list[dict[str, Any]]:
    if not category_id:
        return []
    attrs = request_json(
        "GET",
        f"https://api.mercadolibre.com/categories/{category_id}/attributes",
        token,
    )
    normalized = []
    for attr in attrs if isinstance(attrs, list) else []:
        tags = attr.get("tags", {})
        normalized.append(
            {
                "id": attr.get("id"),
                "name": attr.get("name") or attr.get("id"),
                "value_type": attr.get("value_type"),
                "values": attr.get("values", [])[:50],
                "required": bool(
                    tags.get("required")
                    or tags.get("catalog_required")
                    or tags.get("conditional_required")
                    or tags.get("new_required")
                ),
            }
        )
    normalized.sort(key=lambda item: (not item.get("required"), str(item.get("name") or "")))
    return normalized


def required_wildberries_characteristics(subject_id: str, token: str) -> list[dict[str, Any]]:
    if not subject_id:
        return []
    data = request_json(
        "GET",
        f"https://content-api.wildberries.ru/content/v2/object/charcs/{subject_id}?locale=ru",
        token,
    )
    chars = data.get("data", []) if isinstance(data, dict) else []
    return [
        {
            "charcID": item.get("charcID"),
            "name": item.get("name"),
            "type": item.get("charcType") or item.get("type"),
            "required": item.get("required", False),
            "unitName": item.get("unitName"),
        }
        for item in chars
        if item.get("required")
    ]


def listing_for(plan: dict[str, Any], platform_key: str) -> dict[str, Any]:
    return plan["platforms"].get(platform_key, {}).get("listing", {})


def build_mercadolibre_payload(
    product: dict[str, Any],
    plan: dict[str, Any],
    config: dict[str, Any],
    image_urls: list[str],
) -> dict[str, Any]:
    listing = listing_for(plan, "mercadolibre")
    store = config["mercadolibre"]
    settings = config["listing"]
    mxn_rate = number_or_zero(settings.get("mxn_usd_rate")) or 18.0
    price_input = number_or_zero(settings.get("mercadolibre_price") or settings.get("price"))
    currency_id = str(settings.get("currency_id") or "MXN").upper()
    price_usd = price_input if currency_id == "USD" else round(price_input / mxn_rate, 2)
    logistic_type = str(settings.get("mercadolibre_logistic_type") or "remote").strip() or "remote"
    sku = settings.get("sku") or product.get("name") or "SKU-1"
    site_id = store.get("site_id") or "MLM"
    category_id = product.get("category_id") or store.get("category_id")
    is_global_selling = str(category_id or "").startswith("CBT")
    attributes = [
        {"id": "BRAND", "value_name": product.get("brand") or "Generic"},
        {"id": "SELLER_SKU", "value_name": sku},
    ]
    model = settings.get("model") or product.get("model") or product.get("name") or sku
    if model:
        attributes.append({"id": "MODEL", "value_name": str(model)[:255]})
    if product.get("colors"):
        attributes.append({"id": "COLOR", "value_name": product["colors"][0]})
    dims = [
        float(x.replace(",", "."))
        for x in re.findall(r"\d+(?:[,.]\d+)?", str(product.get("dimensions", "")))
    ]
    length, width, height = (dims + [1, 1, 1])[:3]
    weight_kg = number_or_zero(product.get("weight_kg")) or 0.1
    length = number_or_zero(settings.get("package_length_cm")) or length
    width = number_or_zero(settings.get("package_width_cm")) or width
    height = number_or_zero(settings.get("package_height_cm")) or height
    weight_kg = number_or_zero(settings.get("package_weight_kg")) or weight_kg
    package_length = max(1, round(length, 1))
    package_width = max(1, round(width, 1))
    package_height = max(1, round(height, 1))
    package_weight = max(10, int(round(weight_kg * 1000)))
    title = (
        str(settings.get("mercadolibre_title") or "").strip()
        or str(listing.get("title") or "").strip()
        or str(product.get("name") or "").strip()
    )[:60]
    upc = str(settings.get("upc") or product.get("upc") or "").strip()
    if upc:
        attributes.append({"id": "GTIN", "value_name": upc})
    else:
        attributes.append({"id": "EMPTY_GTIN_REASON", "value_name": "The product does not have a registered code"})
    attributes.extend(
        [
            {"id": "PACKAGE_LENGTH", "value_name": f"{package_length} cm"},
            {"id": "PACKAGE_WIDTH", "value_name": f"{package_width} cm"},
            {"id": "PACKAGE_HEIGHT", "value_name": f"{package_height} cm"},
            {"id": "PACKAGE_WEIGHT", "value_name": f"{package_weight} g"},
        ]
    )
    extra_attributes = settings.get("mercadolibre_attributes") or {}
    if isinstance(extra_attributes, dict):
        for attr_id, value in extra_attributes.items():
            value = str(value or "").strip()
            if attr_id and value:
                attributes.append({"id": str(attr_id), "value_name": value})
    product_attributes = product.get("attributes") or {}
    if isinstance(product_attributes, dict):
        for attr_id, value in product_attributes.items():
            value = str(value or "").strip()
            if attr_id and value:
                attributes.append({"id": str(attr_id), "value_name": value})
    if not any(attr.get("id") == "PART_NUMBER" for attr in attributes):
        attributes.append({"id": "PART_NUMBER", "value_name": str(model or sku)[:255]})
    if not any(attr.get("id") == "VEHICLE_TYPE" for attr in attributes):
        attributes.append({"id": "VEHICLE_TYPE", "value_name": "Car/Truck"})
    if is_global_selling:
        attributes = [attr for attr in attributes if str(attr.get("id") or "") != "CONDITION"]
        attributes.append({"id": "ITEM_CONDITION", "value_name": str(settings.get("condition") or "new")})
    deduped: dict[str, dict[str, Any]] = {}
    for attr in attributes:
        attr_id = str(attr.get("id") or "").strip()
        if attr_id and attr.get("value_name"):
            deduped[attr_id] = attr
    attributes = list(deduped.values())
    pictures = [
        {"id": url.split(":", 1)[1]} if str(url).startswith("ml-id:") else {"source": url}
        for url in image_urls
        if url
    ]
    commission_rate = number_or_zero(settings.get("mercadolibre_commission_percent")) / 100
    shipping_usd = number_or_zero(settings.get("ml_shipping_usd"))
    net_proceeds = number_or_zero(settings.get("mercadolibre_net_proceeds_usd"))
    if not net_proceeds:
        net_proceeds = max(0.01, round(price_usd * (1 - commission_rate) - shipping_usd, 2))
    sale_terms = settings.get("mercadolibre_sale_terms")
    if not isinstance(sale_terms, list) or not sale_terms:
        sale_terms = [
            {
                "id": "WARRANTY_TYPE",
                "name": "Warranty type",
                "value_id": "6150835",
                "value_name": "No warranty",
            },
            {
                "id": "WARRANTY_TIME",
                "name": "Warranty time",
                "value_name": "0 days",
            },
        ]

    site_entry: dict[str, Any] = {
        "site_id": site_id,
        "logistic_type": logistic_type,
        "price": price_usd,
        "net_proceeds": net_proceeds,
        "listing_type_id": settings.get("listing_type_id") or "gold_special",
        "sale_terms": sale_terms,
        "title": title,
    }
    if pictures:
        site_entry["pictures"] = pictures

    payload = {
        "_global_selling": True,
        "title": title,
        "category_id": category_id,
        "price": price_usd,
        "currency_id": "USD",
        "available_quantity": int(settings.get("stock") or 1),
        "buying_mode": "buy_it_now",
        "catalog_listing": False,
        "listing_type_id": settings.get("listing_type_id") or "gold_special",
        "package_length": package_length,
        "package_width": package_width,
        "package_height": package_height,
        "package_weight": package_weight,
        "sites_to_sell": [site_entry],
        "attributes": attributes,
        "sale_terms": sale_terms,
        "description": {"plain_text": listing.get("description", "")},
    }
    if not is_global_selling:
        payload["condition"] = settings.get("condition") or "new"
        payload["pictures"] = pictures
    return payload


def build_wildberries_payload(
    product: dict[str, Any],
    plan: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    listing = listing_for(plan, "wildberries")
    store = config["wildberries"]
    settings = config["listing"]
    sku = settings.get("sku") or product.get("name") or "SKU-1"
    chars = []
    if product.get("brand"):
        chars.append({"id": 0, "name": "Бренд", "value": product.get("brand")})
    if product.get("colors"):
        chars.append({"id": 0, "name": "Цвет", "value": product["colors"][0]})
    dims = [float(x.replace(",", ".")) for x in __import__("re").findall(r"\d+(?:[,.]\d+)?", str(product.get("dimensions", "")))]
    length, width, height = (dims + [1, 1, 1])[:3]
    weight = float(str(product.get("weight_kg") or "0.1").replace(",", ".") or 0.1)

    return [
        {
            "subjectID": int(store.get("subject_id") or 0),
            "variants": [
                {
                    "vendorCode": sku,
                    "title": listing.get("title") or product.get("name"),
                    "description": listing.get("description", ""),
                    "brand": product.get("brand") or "Нет бренда",
                    "dimensions": {
                        "length": int(round(length)),
                        "width": int(round(width)),
                        "height": int(round(height)),
                        "weightBrutto": max(0.01, round(weight, 3)),
                    },
                    "characteristics": chars,
                    "sizes": [{"techSize": "0", "wbSize": "", "price": int(float(settings.get("wildberries_price") or settings.get("price") or 0)), "skus": [sku]}],
                }
            ],
        }
    ]


def build_ozon_payload(product: dict[str, Any], plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    listing = listing_for(plan, "wildberries") or listing_for(plan, "mercadolibre")
    settings = config["listing"]
    return {
        "items": [
            {
                "name": listing.get("title") or product.get("name"),
                "description": listing.get("description", ""),
                "category_id": config.get("ozon", {}).get("category_id", ""),
                "offer_id": settings.get("sku") or product.get("name") or "SKU-1",
                "price": settings.get("ozon_price") or "",
                "currency_code": "RUB",
                "vat": "0",
                "attributes": [],
                "images": [],
            }
        ]
    }


def publish_mercadolibre(payload: dict[str, Any], token: str) -> dict[str, Any]:
    item_payload = dict(payload)
    global_selling = bool(item_payload.pop("_global_selling", False)) or str(item_payload.get("category_id", "")).startswith("CBT")
    description = item_payload.get("description") if global_selling else item_payload.pop("description", None)
    if global_selling:
        item = request_json(
            "POST",
            "https://api.mercadolibre.com/global/items",
            token,
            item_payload,
            extra_headers={"parent-item-info": "true"},
        )
    else:
        item = request_json("POST", "https://api.mercadolibre.com/items", token, item_payload)
    if (not global_selling) and description and isinstance(item, dict) and item.get("id"):
        request_json(
            "POST",
            f"https://api.mercadolibre.com/items/{item['id']}/description",
            token,
            description,
        )
    return item if isinstance(item, dict) else {"response": item}


def publish_wildberries(payload: list[dict[str, Any]], token: str) -> dict[str, Any]:
    result = request_json(
        "POST",
        "https://content-api.wildberries.ru/content/v2/cards/upload",
        token,
        payload,
    )
    return result if isinstance(result, dict) else {"response": result}


def parse_mercadolibre_error(error: Exception | str) -> dict[str, Any]:
    """解析美客多 API 返回的错误，提取缺失字段、错误码和可读消息。

    返回结构:
    {
        "error": str,            # 错误码，如 "body.required_fields"
        "message": str,          # 可读错误信息
        "missing_attributes": list[str],  # 缺失属性 ID 列表
        "cause": list[dict],     # 平台原始 cause 列表
        "raw": str,              # 原始错误文本
    }
    """
    raw = str(error)
    result: dict[str, Any] = {
        "error": "",
        "message": raw,
        "missing_attributes": [],
        "missing_fields": [],
        "cause": [],
        "raw": raw,
    }

    # 尝试从错误文本中提取 JSON 部分
    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if json_start < 0 or json_end < json_start:
        lowered = raw.lower()
        for needle, field_name in [
            ("invalid access token", "auth"),
            ("invalid_token", "auth"),
            ("token expired", "auth"),
            ("title", "title"),
            ("price", "price"),
            ("available_quantity", "available_quantity"),
            ("category_id", "category_id"),
            ("category id", "category_id"),
            ("sale_terms", "sale_terms"),
            ("warranty", "sale_terms"),
            ("picture", "pictures"),
            ("images", "pictures"),
            ("shipping_mode", "logistic_type"),
            ("logistic", "logistic_type"),
            ("package_length", "package_length"),
            ("package_width", "package_width"),
            ("package_height", "package_height"),
            ("package_weight", "package_weight"),
        ]:
            if needle in lowered and field_name not in result["missing_fields"]:
                result["missing_fields"].append(field_name)
        return result

    try:
        data = json.loads(raw[json_start: json_end + 1])
    except Exception:
        return result

    result["error"] = str(data.get("error") or data.get("status") or "")
    result["message"] = str(data.get("message") or data.get("error") or raw)

    cause_raw = data.get("cause") or []
    if isinstance(cause_raw, list):
        result["cause"] = cause_raw
    elif isinstance(cause_raw, dict):
        result["cause"] = [cause_raw]

    missing: list[str] = []
    missing_fields: list[str] = []

    # body.required_fields —— cause 里每条是 {"field": "attributes.COLOR", ...}
    for item in result["cause"]:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("id") or item.get("code") or "")
        if field:
            # 去掉 "attributes." 前缀，保留属性 ID
            attr_id = field.removeprefix("attributes.").removeprefix("attribute.")
            if attr_id:
                if field.lower().startswith(("attributes.", "attribute.")):
                    missing.append(attr_id)
                else:
                    missing_fields.append(normalize_mercadolibre_error_field(attr_id))

    # item.attributes.missing_required —— message 里有时直接列出属性名
    if not missing:
        for pattern in [
            r'"id"\s*:\s*"([A-Z_]{2,})"',
            r"'([A-Z_]{2,})'",
            r"\b([A-Z][A-Z_]{2,})\b",
        ]:
            hits = re.findall(pattern, raw)
            for hit in hits:
                if hit not in missing and hit not in {
                    "GET", "POST", "PUT", "DELETE", "HTTP", "HTTPS", "JSON", "URL", "API",
                    "USD", "MXN", "BRL", "ARS", "COP", "CLP", "PEN", "UYU",
                }:
                    missing.append(hit)
            if missing:
                break

    result["missing_attributes"] = missing
    lowered = raw.lower()
    keyword_map = [
        ("invalid access token", "auth"),
        ("invalid_token", "auth"),
        ("token expired", "auth"),
        ("title", "title"),
        ("price", "price"),
        ("available_quantity", "available_quantity"),
        ("category_id", "category_id"),
        ("category id", "category_id"),
        ("sale_terms", "sale_terms"),
        ("warranty", "sale_terms"),
        ("picture", "pictures"),
        ("images", "pictures"),
        ("shipping_mode", "logistic_type"),
        ("logistic", "logistic_type"),
        ("package_length", "package_length"),
        ("package_width", "package_width"),
        ("package_height", "package_height"),
        ("package_weight", "package_weight"),
    ]
    for needle, field_name in keyword_map:
        if needle in lowered and field_name not in missing_fields:
            missing_fields.append(field_name)
    result["missing_fields"] = list(
        dict.fromkeys(
            normalized
            for normalized in (normalize_mercadolibre_error_field(item) for item in missing_fields)
            if normalized
        )
    )
    return result


def normalize_mercadolibre_error_field(field: str) -> str:
    lowered = str(field or "").strip().lower()
    if not lowered:
        return ""
    if "shipping.mode" in lowered or "shipping_mode" in lowered or "logistic" in lowered:
        return "logistic_type"
    if "invalid access token" in lowered or "invalid_token" in lowered or "token expired" in lowered or lowered == "auth":
        return "auth"
    if "picture" in lowered or "image" in lowered:
        return "pictures"
    if "warranty" in lowered or "sale_terms" in lowered:
        return "sale_terms"
    if "category_id" in lowered or "category id" in lowered:
        return "category_id"
    if "title" in lowered:
        return "title"
    if "price" in lowered:
        return "price"
    if "available_quantity" in lowered or "quantity" in lowered or "stock" in lowered:
        return "stock"
    return str(field or "").strip()
