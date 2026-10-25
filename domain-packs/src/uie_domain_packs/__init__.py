"""UIE Domain Packs : la connaissance métier d'un domaine pour un système est de la DONNÉE (YAML).

Importer ce paquet (racine de composition : API, CLI) enregistre les domain packs découverts dans
`DomainPackRegistry`. Le Core ne l'importe jamais.
"""

from uie_domain_packs.catalog import discover_catalog_packs, discover_domain_packs

discover_catalog_packs()
discover_domain_packs("partner")  # PARTNER (client / fournisseur)
discover_domain_packs("accounting")  # ACCOUNTING (pièces comptables)
