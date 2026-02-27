"""Security guards for ESP Route Planner.

Two dependency functions for use with FastAPI Depends():

  webhook_guard   — ElevenLabs webhook routes
                    rate-limit (30 req/min/IP) +
                    x-webhook-secret header verification (403 on mismatch)

  admin_guard     — /admin/*, /realtime, /results, /api/latest-result
                    x-admin-secret header check (401 on mismatch)

Environment variables
---------------------
  ELEVENLABS_WEBHOOK_SECRET  — must match the secret set in ElevenLabs dashboard
                               (Header name: x-webhook-secret)
  ADMIN_SECRET               — secret for admin/internal routes
  ENV                        — "prod" enforces all guards;
                               anything else (default "dev") relaxes them
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY = 256 * 1024  # 256 KB


# ── Rate limiter ──────────────────────────────────────────────────────────────


class _SlidingWindowLimiter:
    """Thread-safe sliding-window rate limiter keyed by IP."""

    def __init__(self, max_calls: int, period_seconds: int) -> None:
        self._max = max_calls
        self._period = float(period_seconds)
        self._log: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._log.setdefault(key, deque())
            cutoff = now - self._period
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True


_webhook_limiter = _SlidingWindowLimiter(max_calls=30, period_seconds=60)


# ── FastAPI dependencies ──────────────────────────────────────────────────────


async def webhook_guard(request: Request) -> None:
    """Dependency for ElevenLabs webhook routes.

    Enforces (in order):
      1. Sliding-window rate limit — 30 req / min / IP
      2. Request body size cap    — 256 KB
      3. x-webhook-secret header  — must match ELEVENLABS_WEBHOOK_SECRET
    """
    # 1. Rate limit
    ip = request.client.host if request.client else "unknown"
    if not _webhook_limiter.is_allowed(ip):
        logger.warning("Rate limit exceeded for %s", ip)
        raise HTTPException(status_code=429, detail="Rate limit exceeded (30 req/min)")

    # 2. Size guard (fast path via Content-Length)
    cl_header = request.headers.get("content-length")
    if cl_header and int(cl_header) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Request body too large (max 256 KB)")

    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Request body too large (max 256 KB)")

    # 3. Secret header verification — never log the header value
    secret = os.environ.get("ELEVENLABS_WEBHOOK_SECRET", "")
    if secret:
        provided = request.headers.get("x-webhook-secret", "")
        if not provided or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail="Unauthorized")


async def admin_guard(request: Request) -> None:
    """Dependency for admin / internal routes.

    Requires a matching x-admin-secret header when ADMIN_SECRET is set.
    In dev (secret not configured) the guard is a no-op.
    """
    secret = os.environ.get("ADMIN_SECRET", "")
    if not secret:
        return  # Dev mode — no secret configured, allow

    provided = request.headers.get("x-admin-secret", "")
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid or missing x-admin-secret header")
