"""Authentification HTTP (V1d) : cookie de session et rate limit du login.

Le cookie `uie_session` porte un jeton aléatoire (HttpOnly, SameSite=Lax, Secure si UIE_ENV=prod) ; la
base n'en connaît que le sha256 (IdentityService). Les contextes (require_admin, require_tenant) sont
construits dans `uie_api.deps` depuis ce jeton.

Rate limit du login EN MÉMOIRE DU PROCESS (V1d) : fenêtre glissante par email et par IP. Limitation
assumée : inefficace avec plusieurs workers / instances de l'API et remis à zéro au redémarrage ; à
remplacer par un stockage partagé avant la production.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Request
from uie_core.identity import DEFAULT_SESSION_TTL

COOKIE_NAME = "uie_session"


class RateLimited(Exception):  # noqa: N818 — nom du domaine (→ HTTP 429)
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many login attempts")
        self.retry_after = retry_after


class LoginRateLimiter:
    """Échecs de login par email et par IP sur une fenêtre glissante (mémoire du process)."""

    def __init__(self, window_s: int = 900, per_email: int = 5, per_ip: int = 20) -> None:
        self.window_s, self.per_email, self.per_ip = window_s, per_email, per_ip
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and q[0] <= now - self.window_s:
            q.popleft()
        return q

    def check(self, email: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key, limit in ((f"email:{email.strip().lower()}", self.per_email), (f"ip:{ip}", self.per_ip)):
                q = self._recent(key, now)
                if len(q) >= limit:
                    raise RateLimited(int(q[0] + self.window_s - now) + 1)

    def failure(self, email: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures[f"email:{email.strip().lower()}"].append(now)
            self._failures[f"ip:{ip}"].append(now)

    def success(self, email: str) -> None:
        with self._lock:
            self._failures.pop(f"email:{email.strip().lower()}", None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()


def session_ttl() -> timedelta:
    hours = os.environ.get("UIE_SESSION_TTL_HOURS", "").strip()
    return timedelta(hours=float(hours)) if hours else DEFAULT_SESSION_TTL


def cookie_secure() -> bool:
    return os.environ.get("UIE_ENV", "").strip().lower() == "prod"


def session_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)
