"""
SmartTrip AI - Cache Layer
In-memory TTL cache for MVP. Drop-in replacement for Redis in production.

Architecture spec: "Cache traffic/weather results for 15 minutes"
"""

import time
import hashlib
import json
from typing import Any, Optional


class CacheStore:
    """Simple in-memory TTL cache. Replace with Redis in production."""

    def __init__(self, default_ttl: int = 900):
        """
        Args:
            default_ttl: Default time-to-live in seconds (900 = 15 minutes)
        """
        self._store: dict = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if not found or expired."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if time.time() > entry["expires_at"]:
            del self._store[key]
            self._misses += 1
            return None

        self._hits += 1
        return entry["value"]

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set a value in cache with TTL."""
        ttl = ttl or self._default_ttl
        self._store[key] = {
            "value": value,
            "expires_at": time.time() + ttl,
            "created_at": time.time(),
        }

    def delete(self, key: str):
        """Delete a key from cache."""
        self._store.pop(key, None)

    def clear(self):
        """Clear all cached entries."""
        self._store.clear()

    def cleanup(self):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v["expires_at"]]
        for k in expired:
            del self._store[k]

    @property
    def stats(self) -> dict:
        """Return cache statistics."""
        self.cleanup()
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
        }

    @staticmethod
    def make_key(*args) -> str:
        """Generate a cache key from arguments."""
        raw = json.dumps(args, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()


# ── Cache Key Builders ────────────────────────────────────

def traffic_cache_key(origin_lat, origin_lon, dest_lat, dest_lon, city, hour, day_type, month):
    return CacheStore.make_key("traffic",
                               round(origin_lat, 4), round(origin_lon, 4),
                               round(dest_lat, 4), round(dest_lon, 4),
                               city, hour, day_type, month)


def weather_cache_key(city, month, hour):
    return CacheStore.make_key("weather", city, month, hour)


def optimize_cache_key(start_lat, start_lon, attraction_ids, city, date_str, start_hour, preference_mode):
    return CacheStore.make_key("optimize",
                               round(start_lat, 4), round(start_lon, 4),
                               sorted(attraction_ids), city, date_str, start_hour, preference_mode)


# Singleton
_cache: Optional[CacheStore] = None


def get_cache() -> CacheStore:
    global _cache
    if _cache is None:
        _cache = CacheStore(default_ttl=900)  # 15 minutes per spec
    return _cache
