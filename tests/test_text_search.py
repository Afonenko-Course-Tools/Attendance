"""The shared token index searches domain-free phrases and preserves item identity."""

from dataclasses import dataclass

from attendance_lab.application.text_search import TextSearch, normalized


@dataclass(frozen=True)
class Phrase:
    key: int
    title: str


def test_phrases_support_typos_word_order_and_original_items():
    first = Phrase(1, "Защита персональных данных")
    second = Phrase(2, "Учёт сетевых соединений")
    index = TextSearch([second, first], text=lambda item: item.title, order=lambda item: item.key)
    assert index.find("данных зашита") == [first]
    assert index.find("  СЕТЕВЫХ   учет ") == [second]
    assert index.find("защита отсутствующее") == []
    assert index.find("") == [first, second]
    assert index.find("персоналных")[0] is first
    assert normalized("УЧЁТ") == "учет"


def test_cache_is_bounded_and_results_cannot_mutate_the_cached_list():
    phrase = Phrase(1, "Резервное копирование")
    calls = []

    def describe(item):
        calls.append(item.key)
        return item.title

    index = TextSearch([phrase], text=describe, order=lambda item: item.key)
    assert calls == [1]
    result = index.find("резеврное")
    assert result == [phrase]
    result.clear()
    assert index.find("  РЕЗЕВРНОЕ ") == [phrase]
    for number in range(130):
        index.find(str(number))
    assert len(index.query_cache) == 128
    assert calls == [1]
