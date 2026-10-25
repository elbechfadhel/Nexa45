"""Erreurs génériques d'un connecteur d'API distante, communes à tous les systèmes.

Un connecteur traduit les réponses de SON système dans ces classes ; le reste de l'application ne connaît
qu'elles. Le message est une phrase technique SANS secret (jamais d'en-tête d'autorisation, jamais le corps
brut d'une réponse) : il va aux journaux. Ce que voit l'utilisateur final est choisi par l'entrypoint
d'après la CLASSE de l'erreur, jamais d'après ce message."""

from __future__ import annotations


class ConnectorError(Exception):
    """Échec d'un appel au système distant."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        trace_id: str | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status = status  # statut HTTP, s'il y en a un
        self.trace_id = trace_id  # identifiant de trace renvoyé par le système (support)
        self.fields = fields  # champs en cause (noms seulement, jamais de valeur)


class AuthenticationError(ConnectorError):
    """Identifiants absents, invalides ou révoqués."""


class AuthorizationError(ConnectorError):
    """Identifiants acceptés, mais droits ou contrat insuffisants pour cette action."""


class ValidationError(ConnectorError):
    """Requête refusée par le système : donnée invalide ou incomplète."""


class NotFoundError(ConnectorError):
    """La ressource demandée n'existe pas (ou plus)."""


class ConflictError(ConnectorError):
    """L'état de la ressource interdit l'opération (ex. version périmée)."""


class RateLimitError(ConnectorError):
    """Limite de débit atteinte, y compris après les attentes prévues."""


class RemoteServerError(ConnectorError):
    """Erreur du système distant, ou réponse inexploitable."""


class NetworkError(ConnectorError):
    """Le système distant n'a pas pu être joint."""


class RemoteTimeoutError(ConnectorError):
    """Pas de réponse dans le délai : l'issue de la requête est INCONNUE (elle a pu aboutir)."""
