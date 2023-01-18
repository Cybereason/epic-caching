import os
import threading

from typing import TypeVar, Any
from collections import defaultdict
from contextlib import contextmanager
from abc import ABCMeta, abstractmethod
from collections.abc import MutableMapping, Iterator, Hashable


KT = TypeVar("KT", bound=Hashable)
VT = TypeVar("VT")


class Cache(MutableMapping[KT, VT], metaclass=ABCMeta):
    @abstractmethod
    def __init__(self, cache: dict[KT, VT]):
        self.cache = cache

    def __getitem__(self, key: KT) -> VT:
        return self.cache[key]

    def __setitem__(self, key: KT, value: VT) -> None:
        self.cache[key] = value

    def __delitem__(self, key: KT) -> None:
        del self.cache[key]

    def __len__(self) -> int:
        return len(self.cache)

    def __iter__(self) -> Iterator[KT]:
        return iter(self.cache)

    @contextmanager
    def lock(self, key: KT):
        yield


class ThreadCache(Cache[KT, VT]):
    STORE = threading.local()

    def __init__(self, name: str):
        super().__init__(self.STORE.__dict__.setdefault(name, {}))


class ProcessCache(Cache[KT, VT]):
    STORE_LOCK = threading.Lock()
    STORE_NAME = f'_cache_store_{os.getpid()}'

    def __init__(self, name: str, host: MutableMapping[str, Any] | None = None, n_locks: int = 0):
        self.n_locks = max(int(n_locks), 0)
        if host is None:
            host = globals()
        if self.STORE_NAME not in host:
            with self.STORE_LOCK:
                # Must check again, after the lock
                if self.STORE_NAME not in host:
                    host[self.STORE_NAME] = {}
        cache_store: dict[str, tuple[dict[KT, VT], Any]] = host[self.STORE_NAME]
        if name not in cache_store:
            with self.STORE_LOCK:
                if name not in cache_store:
                    cache_store[name] = (
                        {},
                        [threading.Lock() for _ in range(self.n_locks)] or defaultdict(threading.Lock),
                    )
        cache, self.locks = cache_store[name]
        super().__init__(cache)

    @contextmanager
    def lock(self, key: KT):
        try:
            with self.locks[hash(key) % self.n_locks if self.n_locks else key]:
                yield
        finally:
            if not self.n_locks and key not in self.cache:
                del self.locks[key]
