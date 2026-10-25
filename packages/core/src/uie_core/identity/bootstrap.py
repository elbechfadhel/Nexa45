"""Bootstrap idempotent de l'administrateur plateforme (`uie admin bootstrap`).

La migration ne crée aucun compte et ne dépend d'aucun secret : l'admin initial est créé par cette
opération explicite (UIE_INITIAL_ADMIN_EMAIL, UIE_INITIAL_ADMIN_PASSWORD). Si un utilisateur existe déjà
avec cet email, rien n'est créé ni modifié (pas de doublon, mot de passe inchangé).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.identity.models import PlatformRole, User, UserStatus
from uie_core.identity.passwords import hash_password, normalize_email


@dataclass(frozen=True)
class BootstrapResult:
    user_id: str
    email: str
    created: bool
    platform_role: str  # rôle effectif de l'utilisateur existant (peut ne pas être ADMIN)


def bootstrap_platform_admin(
    session: Session, email: str, password: SecretStr, display_name: str = "GO45 Administrator"
) -> BootstrapResult:
    normalized = normalize_email(email)
    existing = session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        return BootstrapResult(str(existing.id), existing.email, False, existing.platform_role)
    user = User(
        email=normalized,
        display_name=display_name,
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE.value,
        platform_role=PlatformRole.ADMIN.value,
    )
    session.add(user)
    session.flush()
    return BootstrapResult(str(user.id), user.email, True, user.platform_role)
