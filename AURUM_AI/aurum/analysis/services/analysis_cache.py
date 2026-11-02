"""Small in-process LRU cache. Keys include the last candle time, candle count, provisional flag and config
fingerprint, so a new candle or a config change always produces a fresh analysis."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Hashable


class AnalysisCache:
    def __init__(self, max_items: int = 128, ttl_seconds: float = 300.0):
        self.max_items, self.ttl = max_items, ttl_seconds
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or time.monotonic() - item[0] > self.ttl:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return item[1]

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
