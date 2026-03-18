"""
SmartTrip AI - Cache Layer
In-memory TTL cache for general data + disk-based cache for routing results.

TTL strategy:
    Traffic/weather (memory):   5-15 minutes
    Routing (disk):             24 hours (road layouts don't change)
    Optimize results (memory):  15 minutes
"""

import time
import hashlib
import json
import shelve
import pathlib
from typing import Any, Optional


# Disk cache directory
_CACHE_DIR = pathlib.Path(__file__).parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CacheStore:
    """Simple in-memory TTL cache. Replace with Redis in production."""

    def __init__(self, default_ttl: int = 900):
        self._store: dict = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
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
        ttl = ttl or self._default_ttl
        self._store[key] = {
            "value": value,
            "expires_at": time.time() + ttl,
            "created_at": time.time(),
        }

    def delete(self, key: str):
        self._store.pop(key, None)

    def clear(self):
        self._store.clear()

    def cleanup(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v["expires_at"]]
        for k in expired:
            del self._store[k]

    @property
    def stats(self) -> dict:
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
        raw = json.dumps(args, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()


class DiskCacheStore:
    """
    Disk-persistent TTL cache using Python shelve.
    Used for routing results (ORS responses) which are expensive quota-wise
    but stable for 24 hours.
    """

    def __init__(self, path: str, default_ttl: int = 86400):
        self._path = str(path)
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        try:
            with shelve.open(self._path) as db:
                entry = db.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() > entry["expires_at"]:
                self._misses += 1
                return None
            self._hits += 1
            return entry["value"]
        except Exception:
            self._misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        ttl = ttl or self._default_ttl
        try:
            with shelve.open(self._path) as db:
                db[key] = {
                    "value": value,
                    "expires_at": time.time() + ttl,
                }
        except Exception:
            pass  # disk cache failure is non-fatal

    def delete(self, key: str):
        try:
            with shelve.open(self._path) as db:
                db.pop(key, None)
        except Exception:
            pass

    def clear(self):
        try:
            with shelve.open(self._path) as db:
                db.clear()
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "type": "disk",
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
        }


# ── Cache Key Builders ────────────────────────────────────

def traffic_cache_key(origin_lat, origin_lon, dest_lat, dest_lon, city, hour, day_type, month):
    return CacheStore.make_key("traffic",
                               round(origin_lat, 4), round(origin_lon, 4),
                               round(dest_lat, 4), round(dest_lon, 4),
                               city, hour, day_type, month)


def weather_cache_key(city, month, hour, date_str=None):
    return CacheStore.make_key("weather", city, month, hour, date_str or "")


def optimize_cache_key(start_lat, start_lon, attraction_ids, city, date_str, start_hour, preference_mode, travel_mode="driving"):
    return CacheStore.make_key("optimize",
                               round(start_lat, 4), round(start_lon, 4),
                               sorted(attraction_ids), city, date_str, start_hour,
                               preference_mode, travel_mode)


def routing_cache_key(origin_lat, origin_lon, dest_lat, dest_lon, mode="driving"):
    """Disk cache key for ORS routing results (rounded to ~100m precision)."""
    return CacheStore.make_key("route",
                               round(origin_lat, 3), round(origin_lon, 3),
                               round(dest_lat, 3), round(dest_lon, 3), mode)


def matrix_cache_key(locations: list, mode: str = "driving") -> str:
    """Disk cache key for ORS matrix results."""
    coords = [(round(l["latitude"], 3), round(l["longitude"], 3)) for l in locations]
    return CacheStore.make_key("matrix", coords, mode)


# ── Singletons ────────────────────────────────────────────

_cache: Optional[CacheStore] = None
_disk_cache: Optional[DiskCacheStore] = None


def get_cache() -> CacheStore:
    global _cache
    if _cache is None:
        _cache = CacheStore(default_ttl=900)  # 15 minutes
    return _cache


def get_disk_cache() -> DiskCacheStore:
    global _disk_cache
    if _disk_cache is None:
        _disk_cache = DiskCacheStore(
            path=str(_CACHE_DIR / "route_cache"),
            default_ttl=86400,  # 24 hours
        )
    return _disk_cache
