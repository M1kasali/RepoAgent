"""Small-corpus BM25 and source-rank fusion for Skill discovery."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import islice
import math
import re


def tokenize(text):
    return re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]", str(text).lower())


class BM25:
    def __init__(self, documents):
        self.frequencies = tuple(Counter(doc) for doc in documents)
        self.lengths = tuple(sum(doc.values()) for doc in self.frequencies)
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        frequencies = Counter(word for doc in self.frequencies for word in doc)
        self.idf = {
            word: math.log(1 + (len(self.lengths) - count + 0.5) / (count + 0.5))
            for word, count in frequencies.items()
        }

    def scores(self, query):
        scores = [0.0] * len(self.frequencies)
        for word in query:
            for index, document in enumerate(self.frequencies):
                frequency = document.get(word, 0)
                if frequency:
                    norm = 1.5 * (
                        0.25 + 0.75 * self.lengths[index] / (self.average or 1)
                    )
                    scores[index] += (
                        self.idf.get(word, 0) * frequency * 2.5 / (frequency + norm)
                    )
        return scores


class LocalSkillSource:
    name = "local"
    weight = 1.0

    def __init__(self, pool, *, min_score=0):
        if (
            type(min_score) not in {int, float}
            or not math.isfinite(min_score)
            or min_score < 0
        ):
            raise ValueError("skill minimum score must be nonnegative and finite")
        self.pool, self.min_score = pool, min_score

    def search(self, query, history, k):
        return tuple(
            hit
            for hit in self.pool.search(query, top_k=k)
            if hit.score >= self.min_score
        )


class SkillRouter:
    """Host-owned synchronous sources; multi-source calls run concurrently.

    Source weights/ranks determine fusion, never cross-source raw score scales.
    Sources must bound their own I/O; this is not a hard timeout boundary.
    """

    def __init__(self, sources, *, over_fetch=2):
        self.sources = tuple(sources)
        if len(self.sources) > 8 or len({s.name for s in self.sources}) != len(
            self.sources
        ):
            raise ValueError(
                "skill sources require unique names and at most eight entries"
            )
        if type(over_fetch) is not int or not 1 <= over_fetch <= 10:
            raise ValueError("invalid skill over-fetch factor")
        for source in self.sources:
            if (
                type(source.weight) not in {int, float}
                or not math.isfinite(source.weight)
                or source.weight <= 0
            ):
                raise ValueError("source weight must be positive and finite")
        self.over_fetch = over_fetch

    def select(self, query, history=(), k=5):
        if type(k) is not int or not 0 <= k <= 100:
            raise ValueError("invalid skill result limit")
        diagnostics = {"failed_sources": {}, "contributing_sources": {}}
        if not k or not self.sources:
            return (), diagnostics

        def search(source):
            try:
                from .skills import ActivatedSkill

                hits = tuple(
                    islice(
                        source.search(query, history, k * self.over_fetch),
                        k * self.over_fetch + 1,
                    )
                )
                if len(hits) > k * self.over_fetch:
                    raise ValueError("source exceeded candidate limit")
                if any(
                    not isinstance(hit, ActivatedSkill) or not math.isfinite(hit.score)
                    for hit in hits
                ):
                    raise ValueError("invalid source score")
                return hits, None
            except Exception as exc:
                return (), type(exc).__name__

        if len(self.sources) == 1:
            results = [search(self.sources[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
                results = list(pool.map(search, self.sources))
        scores, representatives, contributions = {}, {}, {}
        for source, (hits, failure) in zip(self.sources, results):
            if failure:
                diagnostics["failed_sources"][source.name] = failure
            seen = set()
            for rank, hit in enumerate(hits, 1):
                key = hit.manifest.name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                contribution = source.weight / (60 + rank)
                scores[key] = scores.get(key, 0) + contribution
                contributions.setdefault(key, []).append(source.name)
                previous = representatives.get(key)
                if previous is None or contribution > previous[0]:
                    representatives[key] = (contribution, hit)
        output = []
        for key in sorted(scores, key=lambda key: (-scores[key], key))[:k]:
            hit = replace(representatives[key][1], score=scores[key])
            output.append(hit)
            diagnostics["contributing_sources"][hit.qualified_id] = contributions[key]
        return tuple(output), diagnostics
