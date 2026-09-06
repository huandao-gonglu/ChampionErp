"""俄语词形归一化；只处理文本，不依赖平台、模型或持久化。"""

from functools import lru_cache
import re
from threading import local

import snowballstemmer


_thread_state = local()


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
    return re.findall(r"[^\W_]+", text.casefold().replace("ё", "е"))


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
