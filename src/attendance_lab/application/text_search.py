"""Reusable in-memory token search without storage or presentation dependencies."""

from collections.abc import Callable, Iterable
from typing import Any


def normalized(value: object) -> str:
    return str(value).casefold().replace("ё", "е")


class TextSearch[T]:
    def __init__(
        self,
        items: Iterable[T],
        *,
        text: Callable[[T], object] = str,
        order: Callable[[T], Any] = str,
    ):
        self.items = list(items)
        self.order = order
        self.entries = [(item, tuple(normalized(text(item)).split())) for item in self.items]
        # Repeated labels share token scores, computed once per query.
        self.tokens = frozenset(token for _, tokens in self.entries for token in tokens)
        self.deletion_index = {}
        for token in self.tokens:
            if token.isdecimal() or len(token) > 40:
                continue
            for deletion in self.deletions(token):
                self.deletion_index.setdefault(deletion, set()).add(token)
        self.query_cache = {}

    @staticmethod
    def deletions(word):
        """Candidate lookup for at most two edits, built once for the text collection."""
        found, level = {word}, {word}
        for _ in range(2):
            level = {text[:i] + text[i + 1 :] for text in level for i in range(len(text))}
            found.update(level)
        return found

    def word_scores(self, word):
        scores = {token: 1.0 for token in self.tokens if word in token}
        if len(word) < 3 or word.isdecimal() or len(word) > 40:
            return scores
        candidates = set()
        for deletion in self.deletions(word):
            candidates.update(self.deletion_index.get(deletion, ()))
        for token in candidates - scores.keys():
            score = self.token_score(word, token)
            if score:
                scores[token] = score
        return scores

    @staticmethod
    def token_score(word, token):
        if word in token:
            return 1.0
        if len(word) < 3:
            return 0.0
        limit = min(2, int(max(len(word), len(token)) * 0.32))
        if abs(len(word) - len(token)) > limit:
            return 0.0
        # Bounded Damerau-Levenshtein recognizes the adjacent transpositions
        # common when typing words. Length and row cutoffs avoid
        # the quadratic, allocation-heavy general-purpose sequence matcher.
        previous = list(range(len(token) + 1))
        before_previous = None
        for i, left in enumerate(word, 1):
            current = [i]
            for j, right in enumerate(token, 1):
                distance = min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left != right),
                )
                if (
                    before_previous is not None
                    and j > 1
                    and left == token[j - 2]
                    and word[i - 2] == right
                ):
                    distance = min(distance, before_previous[j - 2] + 1)
                current.append(distance)
            if min(current) > limit:
                return 0.0
            before_previous, previous = previous, current
        distance = previous[-1]
        return 1.0 - distance / max(len(word), len(token)) if distance <= limit else 0.0

    def find(self, query):
        query = " ".join(normalized(query).split())
        if query in self.query_cache:
            return list(self.query_cache[query])
        words = query.split()
        if not words:
            return sorted(self.items, key=self.order)
        scores = {word: self.word_scores(word) for word in set(words)}
        ranked = []
        for item, tokens in self.entries:
            parts = [
                max((scores[word].get(token, 0) for token in tokens), default=0) for word in words
            ]
            score = sum(parts)
            if all(parts) and score / len(words) >= 0.68:
                ranked.append((score, item))
        result = [
            item for _, item in sorted(ranked, key=lambda pair: (-pair[0], self.order(pair[1])))
        ]
        if len(self.query_cache) >= 128:
            self.query_cache.pop(next(iter(self.query_cache)))
        self.query_cache[query] = tuple(result)
        return result
