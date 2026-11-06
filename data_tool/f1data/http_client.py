"""HTTP access with an on-disk cache, rate limiting and retries.

Every API response is stored gzip-compressed under ``cache/``, so re-running a
build costs no network requests and the public APIs are only asked once.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote

import requests

USER_AGENT = "f1-replay-data-tool/1.0 (personal, non-commercial)"
EMPTY_RESULT_TTL_S = 24 * 3600
MAX_ATTEMPTS = 7


class DataUnavailable(Exception):
    """The source answered, but it does not have (or will not give) this data."""


class NetworkError(Exception):
    """The source could not be reached after all retries."""


class RateLimiter:
    """Sliding-window limiter enforcing several (max_calls, window_s) limits."""

    def __init__(self, limits: list[tuple[int, float]], clock=time.monotonic, sleep=time.sleep):
        self.limits = limits
        self.clock = clock
        self.sleep = sleep
        self.calls: deque[float] = deque()

    def wait_time(self) -> float:
        now = self.clock()
        longest = max(window for _, window in self.limits)
        while self.calls and now - self.calls[0] >= longest:
            self.calls.popleft()
        wait = 0.0
        for max_calls, window in self.limits:
            recent = [c for c in self.calls if now - c < window]
            if len(recent) >= max_calls:
                wait = max(wait, recent[-max_calls] + window - now)
        return wait

    def acquire(self) -> None:
        while (wait := self.wait_time()) > 0:
            self.sleep(wait + 0.01)
        self.calls.append(self.clock())


def build_url(base: str, path: str, params: list[tuple[str, str, object]] | None) -> str:
    """Build a URL from (key, operator, value) triples.

    OpenF1 puts comparison operators in the query string, for example
    ``date>=2024-03-02``, which a normal key=value encoder would mangle.
    """
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + "&".join(f"{k}{op}{quote(str(v), safe=':-.')}" for k, op, v in params)
    return url


class CachedClient:
    """GETs JSON from one API through a shared disk cache and its rate limiter."""

    def __init__(self, name: str, base: str, cache_dir: Path, limiter: RateLimiter,
                 session: requests.Session | None = None, log=print, sleep=time.sleep):
        self.name = name
        self.base = base
        self.dir = Path(cache_dir) / name
        self.limiter = limiter
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.log = log
        self.sleep = sleep
        self.network_calls = 0

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json.gz")

    def _read_cache(self, url: str, ttl_s: float | None):
        path = self._path(url)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError, EOFError):
            path.unlink(missing_ok=True)  # Interrupted write: fetch it again.
            return None
        age = time.time() - entry["fetched"]
        if not entry["data"] and age > EMPTY_RESULT_TTL_S:
            return None  # An empty answer may just mean "not published yet".
        if ttl_s is not None and age > ttl_s:
            return None
        return entry

    def _write_cache(self, url: str, data) -> None:
        path = self._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump({"url": url, "fetched": time.time(), "data": data}, fh)
        tmp.replace(path)

    def get(self, path: str, params=None, ttl_s: float | None = None):
        """Return decoded JSON. ``ttl_s=None`` caches forever (finished races never change)."""
        url = build_url(self.base, path, params)
        cached = self._read_cache(url, ttl_s)
        if cached is not None:
            return cached["data"]
        data = self._fetch(url)
        self._write_cache(url, data)
        return data

    def _fetch(self, url: str):
        last_problem = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.limiter.acquire()
            self.network_calls += 1
            delay = min(2 ** attempt, 60) + random.uniform(0, 0.5)
            try:
                resp = self.session.get(url, timeout=(15, 120))
            except requests.RequestException as exc:
                last_problem = f"connection problem: {exc.__class__.__name__}"
            else:
                code = resp.status_code
                if code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        last_problem = "server sent a damaged response"
                elif code == 404:
                    return []  # OpenF1 uses 404 for "no results found".
                elif code in (401, 403):
                    raise DataUnavailable(
                        f"{self.name} refused access (HTTP {code}). OpenF1 locks data while a "
                        "session is live; try again about an hour after it ends.")
                elif code == 429:
                    retry_after = resp.headers.get("Retry-After", "")
                    delay = max(delay, float(retry_after) if retry_after.isdigit() else 20.0)
                    last_problem = "rate limited (HTTP 429)"
                elif code >= 500:
                    last_problem = f"server error (HTTP {code})"
                else:
                    raise DataUnavailable(f"{self.name} rejected the request (HTTP {code}): {url}")
            if attempt < MAX_ATTEMPTS:
                self.log(f"  {self.name}: {last_problem}; retry {attempt}/{MAX_ATTEMPTS - 1} "
                         f"in {delay:.0f}s")
                self.sleep(delay)
        raise NetworkError(f"{self.name} failed after {MAX_ATTEMPTS} attempts: {last_problem}\n"
                           f"  URL: {url}")


def make_clients(cache_dir: Path, log=print) -> tuple[CachedClient, CachedClient]:
    """Return (openf1, jolpica) clients, limited just under each API's published limits.

    OpenF1 free tier: 3 requests/s and 30/minute.
    Jolpica unauthenticated: 4 requests/s and 500/hour.
    """
    openf1 = CachedClient("openf1", "https://api.openf1.org/v1", cache_dir,
                          RateLimiter([(2, 1.0), (28, 60.0)]), log=log)
    jolpica = CachedClient("jolpica", "https://api.jolpi.ca/ergast/f1", cache_dir,
                           RateLimiter([(3, 1.0), (450, 3600.0)]), log=log)
    return openf1, jolpica
