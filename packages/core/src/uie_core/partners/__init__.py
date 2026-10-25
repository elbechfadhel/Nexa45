"""Domaine PARTNER côté lecture : plan (semantic bindings) et lecture d'un système source vers le Partner CDM.

Aucun nom de système ; aucune donnée de partenaire persistée (lecture à la demande).
"""

from uie_core.partners.plan import PARTNER_KEYS, PartnerPlanError, PartnerReadPlan, build_partner_plan
from uie_core.partners.service import (
    PartnerError,
    PartnerIssue,
    PartnerNotReady,
    PartnerReadResult,
    PartnerReadService,
    partner_read_max,
    read_partners,
)
from uie_core.partners.target import (
    PartnerTargetError,
    PartnerTargetPreview,
    PartnerTargetService,
    PreparedPartner,
    PreparedValue,
    PreviewPartner,
    SendView,
    TargetOption,
    prepare_partners,
)

__all__ = [
    "PARTNER_KEYS",
    "PartnerError",
    "PartnerIssue",
    "PartnerNotReady",
    "PartnerPlanError",
    "PartnerReadPlan",
    "PartnerReadResult",
    "PartnerReadService",
    "PartnerTargetError",
    "PartnerTargetPreview",
    "PartnerTargetService",
    "SendView",
    "PreparedPartner",
    "PreparedValue",
    "PreviewPartner",
    "TargetOption",
    "build_partner_plan",
    "partner_read_max",
    "prepare_partners",
    "read_partners",
]
