from __future__ import annotations

from .common import *

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
