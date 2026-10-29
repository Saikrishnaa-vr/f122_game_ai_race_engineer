"""Optional LLM intent routing; all race facts and answers stay deterministic."""

from __future__ import annotations

import json
import os
from threading import Lock
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .intelligence import answer_question
from .logging_setup import LOGGER


INTENTS = {"tyres", "fuel", "ers", "weather", "pit", "lap", "damage", "rival", "fastest"}


class IntentModel:
    def __init__(self, endpoint: str, model: str, api_key: str | None = None) -> None:
        url = urlparse(endpoint)
        if (url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost")) or url.scheme not in ("http", "https"):
            raise ValueError("LLM endpoint must be HTTPS or loopback HTTP")
        if not endpoint.endswith("/chat/completions"):
            raise ValueError("LLM endpoint must end in /chat/completions")
        if not model.strip():
            raise ValueError("LLM model name is required")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key or os.environ.get("RACE_ENGINEER_LLM_API_KEY")

    def classify(self, question: str, previous: str | None) -> str | None:
        payload = json.dumps({"model": self.model, "stream": False, "max_tokens": 12,
            "messages": [
                {"role": "system", "content": "Classify the driver's F1 race question. Reply with exactly one of: tyres, fuel, ers, weather, pit, lap, damage, rival, fastest, unknown. Use fastest for best-lap comparisons. A follow-up such as 'why?' may use the previous topic. No other text."},
                {"role": "user", "content": f"Previous topic: {previous or 'none'}\nQuestion: {question[:160]}"},
            ]}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=2) as response:
                if int(response.headers.get("Content-Length", "0")) > 8192:
                    return None
                data = json.loads(response.read(8193))
            result = data["choices"][0]["message"]["content"].strip().casefold()
            return result if result in INTENTS else None
        except Exception as exc:
            LOGGER.warning("Optional intent model unavailable: %s", type(exc).__name__)
            return None


class Conversation:
    def __init__(self, model: IntentModel | None = None) -> None:
        self.model = model
        self.previous: str | None = None
        self.lock = Lock()

    def answer(self, question: str, facts: dict) -> str:
        with self.lock:
            words = question.casefold()
            intent = "fastest" if any(phrase in words for phrase in
                                      ("fastest", "best lap", "compare laps", "the grid", "the field")) else None
            if intent is None and self.model:
                intent = self.model.classify(question, self.previous)
            if intent is None:
                for candidate, triggers in (
                    ("pit", ("pit", "box", "strategy", "stop")),
                    ("tyres", ("tyre", "tire", "wear")),
                    ("fuel", ("fuel", "finish", "lift", "coast")),
                    ("ers", ("ers", "battery")),
                    ("weather", ("rain", "weather")),
                    ("damage", ("damage", "wing")),
                    ("rival", ("rival", "ahead", "behind", "catch")),
                    ("lap", ("lap", "pace", "position")),
                ):
                    if any(token in words for token in triggers):
                        intent = candidate
                        break
                if intent is None and any(token in words for token in ("why", "more", "explain")):
                    intent = self.previous
            if intent:
                self.previous = intent
                return answer_question(intent, facts)
            return answer_question(question, facts)
