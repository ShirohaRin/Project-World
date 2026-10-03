from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class CharacterLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _get(self, name: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(name, threading.RLock())

    @contextmanager
    def hold(self, name: str) -> Iterator[None]:
        lock = self._get(name)
        with lock:
            yield
