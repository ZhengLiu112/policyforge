"""
PolicyForge — OpenAI client wrapper.

Three things this adds over calling the SDK directly:

  1. Strict Structured Outputs done correctly. Pydantic's
     model_json_schema() is not accepted by strict mode as-is; schema.py
     normalises it and this module wires it in.

  2. On-disk response caching keyed by (model, prompt, schema). The
     evaluation harness reruns the same extractions many times across
     ablation levels, and the demo must never wait on a live API call
     while a video is recording. Cache hits cost nothing and are
     deterministic.

  3. Usage accounting, so the ablation table can carry a real
     cost-per-document column instead of a hand-wave.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from .schema import response_format_for

T = TypeVar("T", bound=BaseModel)

CACHE_DIR = Path(os.getenv("POLICYFORGE_CACHE", ".cache/llm"))

# USD per 1M tokens. Verify against current pricing before quoting these
# numbers in the report — they move, and a stale figure in a deliverable
# is worse than no figure.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o":      (2.50, 10.00),
}
DEFAULT_MODEL = os.getenv("POLICYFORGE_MODEL", "gpt-4o-mini")


class ExtractionRefusal(RuntimeError):
    """The model declined to answer. Distinct from a malformed response:
    a refusal is a policy event worth logging, not a parse failure."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cache_hits: int = 0
    latency_s: float = 0.0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls += other.calls
        self.cache_hits += other.cache_hits
        self.latency_s += other.latency_s

    def cost_usd(self, model: str = DEFAULT_MODEL) -> float:
        rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
        return (self.prompt_tokens * rate_in + self.completion_tokens * rate_out) / 1_000_000

    def as_dict(self, model: str = DEFAULT_MODEL) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_s": round(self.latency_s, 2),
            "cost_usd": round(self.cost_usd(model), 6),
        }


@dataclass
class LLMClient:
    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    max_retries: int = 4
    use_cache: bool = True
    usage: Usage = field(default_factory=Usage)
    _client: Any = None

    def __post_init__(self) -> None:
        if self._client is None:
            from openai import OpenAI  # imported lazily so tests run offline
            self._client = OpenAI()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # -- caching -----------------------------------------------------------

    def _cache_key(self, system: str, user: str, schema_name: str) -> str:
        payload = json.dumps(
            {"m": self.model, "t": self.temperature, "s": system,
             "u": user, "n": schema_name},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.json"

    # -- main entry point --------------------------------------------------

    def parse(
        self,
        *,
        system: str,
        user: str,
        model_cls: Type[T],
        schema_name: str,
    ) -> T:
        """Call the model and return a validated instance of model_cls.

        Raises ExtractionRefusal if the model refused, or ValueError if the
        payload will not validate after all retries.
        """
        key = self._cache_key(system, user, schema_name)
        path = self._cache_path(key)

        if self.use_cache and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            self.usage.add(Usage(cache_hits=1))
            return model_cls.model_validate(cached["content"])

        response_format = response_format_for(model_cls, schema_name)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                started = time.perf_counter()
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    response_format=response_format,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                elapsed = time.perf_counter() - started

                choice = resp.choices[0]
                if getattr(choice.message, "refusal", None):
                    raise ExtractionRefusal(choice.message.refusal)

                content = json.loads(choice.message.content)
                parsed = model_cls.model_validate(content)

                self.usage.add(Usage(
                    prompt_tokens=getattr(resp.usage, "prompt_tokens", 0),
                    completion_tokens=getattr(resp.usage, "completion_tokens", 0),
                    calls=1,
                    latency_s=elapsed,
                ))

                if self.use_cache:
                    path.write_text(
                        json.dumps({"model": self.model, "content": content},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                return parsed

            except ExtractionRefusal:
                raise
            except Exception as exc:  # transport, rate limit, validation
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** attempt)

        raise ValueError(
            f"extraction failed after {self.max_retries} attempts: {last_error}"
        ) from last_error


def preload_cache_report() -> dict[str, Any]:
    """Used before recording the demo: confirms every call the walkthrough
    makes is already cached, so nothing hits the network on camera."""
    files = list(CACHE_DIR.glob("*.json"))
    return {
        "cache_dir": str(CACHE_DIR),
        "entries": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }
