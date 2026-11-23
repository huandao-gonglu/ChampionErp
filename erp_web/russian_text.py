"""俄语词形归一化、中俄检索分词打分与不可翻译值判定；纯文本函数，无副作用。"""

from __future__ import annotations

from functools import lru_cache
import re
from threading import local

import snowballstemmer


_thread_state = local()

SEARCH_STOP_WORDS = frozenset(
    {
        "и",
        "или",
        "для",
        "на",
        "в",
        "во",
        "с",
        "со",
        "от",
        "по",
        "за",
        "к",
        "из",
        "у",
        "о",
        "об",
    }
)

_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_ASCII_SIZE_CODE_RE = re.compile(
    r"^(?:X{0,3}[SML]|[2-6]XL|XXS|XS|S|M|L|XL|XXL|XXXL|ONE\s*SIZE)$",
    re.IGNORECASE,
)
_ALNUM_MODEL_CODE_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._/\-+]+$")


@lru_cache(maxsize=32768)
def normalize_russian_word(word: str) -> str:
    word = word.casefold().replace("ё", "е")
    if not re.fullmatch(r"[а-я]{3,}", word):
        return word
    # Snowball 实例可重入但不能跨线程同时使用；HTTP worker 各持有一个。
    if not hasattr(_thread_state, "stemmer"):
        _thread_state.stemmer = snowballstemmer.stemmer("russian")
    return _thread_state.stemmer.stemWord(word)


def text_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", str(text or "").casefold().replace("ё", "е"))


def has_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(str(text or "")))


def normalize_search_terms(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            word for word in text_words(text) if word not in SEARCH_STOP_WORDS
        )
    )


def stemmed_search_tokens(text: str) -> list[str]:
    return [normalize_russian_word(word) for word in normalize_search_terms(text)]


def cjk_index_tokens(text: str) -> str:
    """为 SQLite FTS5 unicode61 生成中文单字+双字切分串，兼顾混合英文词。"""

    source = str(text or "").strip().casefold()
    if not source:
        return ""
    tokens: list[str] = []
    for run in _CJK_RUN_RE.findall(source):
        chars = list(run)
        tokens.extend(chars)
        for index in range(len(chars) - 1):
            tokens.append(chars[index] + chars[index + 1])
    for latin in re.findall(r"[a-z0-9]+", source):
        tokens.append(latin)
    return " ".join(dict.fromkeys(tokens))


def score_russian_search_match(
    query_terms: list[str],
    name_stems: set[str],
    path_stems: set[str],
) -> tuple[int, list[str]]:
    """俄语/多语词干精确评分；避免子串误召回（如 бафф 不命中 бафферы）。"""

    query_stems = {normalize_russian_word(term) for term in query_terms}
    matched = [term for term in query_terms if normalize_russian_word(term) in path_stems]
    if not query_stems or not matched:
        return 0, []
    name_matches = query_stems & name_stems
    if query_stems == name_stems:
        return 100, matched
    if query_stems <= name_stems:
        return 95, matched
    if name_matches:
        # 修饰词缺失不能把商品名命中整条丢弃；优先覆盖叶子名称，路径仅作补充。
        score = 40 + int(35 * len(name_matches) / max(1, len(name_stems)))
        score += int(20 * len(query_stems & path_stems) / len(query_stems))
        return score, matched
    # 只命中上级目录时，必须覆盖全部实词，避免泛词召回整棵树。
    return (60, matched) if query_stems <= path_stems else (0, [])


def score_cjk_search_match(
    query: str,
    name_zh: str,
    path_zh: str,
) -> tuple[int, list[str]]:
    """中文类目名/路径评分（供 Ozon 官方中文检索使用）。"""

    raw = str(query or "").strip().casefold()
    if not raw:
        return 0, []
    terms = [
        part
        for part in re.split(r"[\s/,，、;；>＞|]+", raw)
        if part
    ]
    if not terms:
        return 0, []
    norm_name = str(name_zh or "").strip().casefold()
    norm_path = str(path_zh or "").strip().casefold() or norm_name
    if not norm_path:
        return 0, []
    if raw == norm_name:
        return 100, terms
    if raw in norm_name:
        return 96, terms
    matched_name = [term for term in terms if term in norm_name]
    matched_path = [term for term in terms if term in norm_path]
    if not matched_path:
        return 0, []
    if len(matched_name) == len(terms):
        return 92, matched_name
    if matched_name:
        score = 45 + int(30 * len(matched_name) / len(terms))
        score += int(15 * len(matched_path) / len(terms))
        return score, matched_path
    if len(matched_path) == len(terms):
        return 70, matched_path
    return 0, []


def is_non_translatable_value(text: str) -> bool:
    """判断词条是否无需翻译（已含中文、纯数字/符号、单字、尺码码或型号代码）。"""

    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    if has_cjk(cleaned):
        return True
    if not any(char.isalpha() for char in cleaned):
        return True
    if len(cleaned) == 1:
        return True
    if _ASCII_SIZE_CODE_RE.fullmatch(cleaned):
        return True
    if _ALNUM_MODEL_CODE_RE.fullmatch(cleaned):
        return True
    return False


def contains_russian_word_forms(value: str, text: str) -> bool:
    """接受连续俄语词的变格，不用词干子串推断数值、型号或其他语言。"""

    if not re.fullmatch(r"[а-яё]{3,}(?:\s+[а-яё]+)*", value.casefold()):
        return False
    expected = [normalize_russian_word(word) for word in text_words(value)]
    actual = [normalize_russian_word(word) for word in text_words(text)]
    return any(
        actual[index : index + len(expected)] == expected
        for index in range(len(actual) - len(expected) + 1)
    )


__all__ = [
    "SEARCH_STOP_WORDS",
    "cjk_index_tokens",
    "contains_russian_word_forms",
    "has_cjk",
    "is_non_translatable_value",
    "normalize_russian_word",
    "normalize_search_terms",
    "score_cjk_search_match",
    "score_russian_search_match",
    "stemmed_search_tokens",
    "text_words",
]
