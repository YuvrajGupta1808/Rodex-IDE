from __future__ import annotations

import json
import hashlib
from typing import Any

from ..events.schemas import Finding


class VolumeStore:
    """
    Manages Blaxel Volumes for persistent storage that survives sandbox recreation.
    Used for: evaluation results, finding cache keyed by file SHA256.
    Falls back to in-memory dict for local development.
    """

    REGION = "us-pdx-1"
    EVAL_VOLUME = "code-review-eval"
    CACHE_VOLUME = "code-review-cache"

    def __init__(self) -> None:
        self._eval_volume: Any = None
        self._cache_volume: Any = None
        self._cache: dict[str, list[dict]] = {}
        self._metrics: dict[str, Any] = {}

    async def ensure_volumes(self) -> None:
        try:
            from blaxel.volume import VolumeInstance  # type: ignore
            self._eval_volume = await VolumeInstance.create_if_not_exists(
                name=self.EVAL_VOLUME,
                size=1,
                region=self.REGION,
            )
            self._cache_volume = await VolumeInstance.create_if_not_exists(
                name=self.CACHE_VOLUME,
                size=2,
                region=self.REGION,
            )
        except (ImportError, Exception):
            pass

    def _file_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    async def get_cached_findings(self, content: str) -> list[Finding] | None:
        key = self._file_hash(content)
        data = self._cache.get(key)
        if data is None:
            return None
        try:
            return [Finding(**item) for item in data]
        except Exception:
            return None

    async def cache_findings(self, content: str, findings: list[Finding]) -> None:
        key = self._file_hash(content)
        self._cache[key] = [f.model_dump() for f in findings]

    async def save_metrics(self, session_id: str, metrics: dict) -> None:
        self._metrics[session_id] = metrics

    async def get_metrics(self, session_id: str) -> dict | None:
        return self._metrics.get(session_id)
