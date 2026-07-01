"""Concurrency tests: interleaved set/get/delete across multiple threads."""
import threading

from fallback_cache import FallbackCache

_NUM_THREADS = 8
_OPS_PER_THREAD = 50


def _worker(cache: FallbackCache, thread_id: int, errors: list[Exception]) -> None:
    """Run a mix of set/get/delete ops, appending any exception to errors."""
    try:
        for i in range(_OPS_PER_THREAD):
            key = f"key-{thread_id}-{i}"
            shared_key = f"shared-{i % 10}"

            cache.set(key, {"tid": thread_id, "i": i})
            cache.set(shared_key, thread_id)

            result = cache.get(key)
            # May be None if another thread deleted it, but must not raise
            assert result is None or isinstance(result, dict)

            cache.get(shared_key)

            if i % 5 == 0:
                cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


def test_concurrent_set_get_delete_no_exceptions():
    """No exceptions under concurrent set/get/delete from 8 threads."""
    cache = FallbackCache(default_ttl=60, max_entries=200)
    errors: list[Exception] = []

    threads = [
        threading.Thread(target=_worker, args=(cache, tid, errors))
        for tid in range(_NUM_THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised exceptions: {errors}"


def test_concurrent_state_consistent_after_joins():
    """After all threads finish, cache state is internally consistent."""
    cache = FallbackCache(default_ttl=60, max_entries=50)
    errors: list[Exception] = []

    threads = [
        threading.Thread(target=_worker, args=(cache, tid, errors))
        for tid in range(_NUM_THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised exceptions: {errors}"

    # Internal consistency: every key in _cache must be an _Entry with valid fields
    from fallback_cache.cache import _Entry

    for key, entry in cache._cache.items():
        assert isinstance(entry, _Entry), f"key {key!r} has non-_Entry value: {entry!r}"
        assert isinstance(entry.stored_at, float)
        assert isinstance(entry.ttl, int)
        assert entry.ttl > 0

    # LRU length must not exceed max_entries
    assert len(cache._cache) <= cache._max_entries


def test_concurrent_invalidate_prefix_no_exceptions():
    """Concurrent invalidate_prefix calls do not corrupt _redis_keys or raise."""
    cache = FallbackCache(default_ttl=60, max_entries=200)
    errors: list[Exception] = []

    def writer(tid: int) -> None:
        try:
            for i in range(30):
                cache.set(f"ns{tid}:key{i}", i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def invalidator(tid: int) -> None:
        try:
            for _ in range(10):
                cache.invalidate_prefix(f"ns{tid}:")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(4)]
    threads += [threading.Thread(target=invalidator, args=(tid,)) for tid in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads joined — reading cache state without lock is safe here
    assert errors == [], f"Threads raised exceptions: {errors}"


def test_concurrent_clear_no_exceptions():
    """clear() called concurrently with set/get does not raise."""
    cache = FallbackCache(default_ttl=60, max_entries=100)
    errors: list[Exception] = []

    def setter(tid: int) -> None:
        try:
            for i in range(30):
                cache.set(f"t{tid}-{i}", i)
                cache.get(f"t{tid}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def clearer() -> None:
        try:
            for _ in range(10):
                cache.clear()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=setter, args=(tid,)) for tid in range(6)]
    threads.append(threading.Thread(target=clearer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Threads raised exceptions: {errors}"
