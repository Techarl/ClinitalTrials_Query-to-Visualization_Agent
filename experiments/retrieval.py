"""Small local BM25 + dense + RRF candidate index. Never a statistical universe."""

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from openai import APIError, OpenAI

from app.sources import content_hash


def tokens(text):
    words = re.findall(r"[a-z0-9]+", text.casefold())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return words


def cosine(left, right):
    denominator = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0


class Embeddings:
    def __init__(self, settings, cache: Path, model="text-embedding-3-small", dimensions=256):
        self.model, self.dimensions, self.cache = model, dimensions, cache
        self.values = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        self.client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=str(settings.openai_base_url),
            max_retries=0,
        )
        self.calls, self.input_tokens = 0, 0

    def close(self):
        self.client.close()

    def encode(self, texts):
        keys = [content_hash([self.model, self.dimensions, text]) for text in texts]
        missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in self.values}
        pairs = list(missing.items())
        for offset in range(0, len(pairs), 100):
            batch = pairs[offset : offset + 100]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=[text for _, text in batch],
                    dimensions=self.dimensions,
                    timeout=60,
                )
            except APIError as error:
                raise RuntimeError(f"Embedding API failed ({type(error).__name__})") from None
            assert len(response.data) == len(batch)
            for item in response.data:
                vector = item.embedding
                assert len(vector) == self.dimensions
                self.values[batch[item.index][0]] = vector
            self.calls += 1
            self.input_tokens += response.usage.prompt_tokens
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            pending = self.cache.with_suffix(".pending")
            pending.write_text(json.dumps(self.values), encoding="utf-8")
            pending.replace(self.cache)
        return [self.values[key] for key in keys]


class HybridIndex:
    def __init__(self, documents, embedder=None):
        self.documents = documents
        self.embedder = embedder
        self.frequency = [Counter(tokens(d["text"])) for d in documents]
        self.lengths = [sum(c.values()) for c in self.frequency]
        self.average_length = sum(self.lengths) / max(1, len(documents))
        self.df = Counter(token for counter in self.frequency for token in counter)
        self.vectors = embedder.encode([d["text"] for d in documents]) if embedder and documents else []

    def search(self, query, limit=5):
        if not self.documents:
            return {"bm25": [], "dense": [], "hybrid": []}
        scores = []
        for index, counter in enumerate(self.frequency):
            score = 0
            for token in set(tokens(query)):
                frequency = counter[token]
                if frequency:
                    idf = math.log(1 + (len(self.documents) - self.df[token] + 0.5) / (self.df[token] + 0.5))
                    score += (
                        idf
                        * frequency
                        * 2.5
                        / (
                            frequency
                            + 1.5 * (0.25 + 0.75 * self.lengths[index] / max(1, self.average_length))
                        )
                    )
            scores.append(score)
        lexical = sorted(
            (i for i, score in enumerate(scores) if score > 0),
            key=lambda i: (-scores[i], self.documents[i]["id"]),
        )
        dense_scores = []
        dense = []
        if self.vectors:
            vector = self.embedder.encode([query])[0]
            dense_scores = [cosine(vector, value) for value in self.vectors]
            dense = sorted(
                range(len(self.documents)), key=lambda i: (-dense_scores[i], self.documents[i]["id"])
            )
        fusion = defaultdict(float)
        # Limit each retrieval list before RRF; ranks are one-based, k=60 fixed.
        for ranked in (lexical[:50], dense[:50]):
            for rank, index in enumerate(ranked, 1):
                fusion[index] += 1 / (60 + rank)
        hybrid = sorted(fusion, key=lambda i: (-fusion[i], self.documents[i]["id"]))

        def result(indices, values):
            return [
                {"id": self.documents[i]["id"], "score": values[i], "document": self.documents[i]}
                for i in indices[:limit]
            ]

        return {
            "bm25": result(lexical, scores),
            "dense": result(dense, dense_scores),
            "hybrid": result(hybrid, fusion),
        }
