"""Limiteur de débit du connecteur Lexware Office : seau à jetons, partagé par client de l'API.

Limite officielle : 2 requêtes par seconde pour TOUTE l'API (documentation, section « API Rate Limits »).
Le seau n'a qu'UN jeton de capacité : aucune rafale, les requêtes sont espacées d'au moins 1 / débit seconde
(un seau plus grand laisserait passer plus de 2 requêtes dans une même seconde).

Limite connue : le registre des seaux vit dans la mémoire du processus ; avec plusieurs processus de l'API,
chacun a le sien (à remplacer par un stockage partagé avant un usage en production à plusieurs workers).

L'horloge et l'attente sont injectables : les tests n'attendent jamais réellement."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

RATE_LIMIT_PER_SECOND = 2.0


class TokenBucket:
    def __init__(
        self,
        rate: float = RATE_LIMIT_PER_SECOND,
        capacity: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("débit et capacité strictement positifs")
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._at = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Prend un jeton, en attendant s'il le faut. Retourne le temps attendu (secondes)."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._at) * self.rate)
            self._at = now
            wait = 0.0
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                self._sleep(wait)
                self._at = self._clock()
                self._tokens = 1.0
            self._tokens -= 1.0
            return wait


_buckets: dict[str, TokenBucket] = {}
_guard = threading.Lock()


def shared_bucket(client_id: str) -> TokenBucket:
    """Seau du client de l'API désigné par `client_id` (une EMPREINTE de la clé, jamais la clé)."""
    with _guard:
        bucket = _buckets.get(client_id)
        if bucket is None:
            bucket = _buckets[client_id] = TokenBucket()
        return bucket
