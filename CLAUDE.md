# Universal Integration / Interface Engine — guide pour Claude Code

Ce fichier est lu automatiquement par Claude Code. Il résume l'architecture gelée,
les règles de code et l'état d'avancement. Les documents de référence complets sont
dans `../vision/` et `../System Knowledge Registry/` (Vision, Décisions d'architecture,
Socle Technique V2, System Knowledge Registry) : les lire avant toute décision structurante.

## Ce qu'est le produit

Une couche d'intégration indépendante des ERP : Sources → Adapters → Canonical Data Model →
Mapping sémantique → Transformation → Validation → Target Adapters, avec un
**System Knowledge Registry** qui connaît d'avance les schémas des systèmes supportés
(Odoo 17/18/19 d'abord, puis DATEV, Dynamics, Ariba...). Odoo, SAP, DATEV ne sont pas le
produit : ce sont des connecteurs du moteur.

## Règle finale (Product Domain V1)

```
Registry            → WHAT EXISTS?
Product Domain      → WHAT BELONGS TO THE CATALOG?
Semantic Enrichment → WHAT DOES IT MEAN?   (V1b / V1c)
Mapping             → WHERE DOES IT GO?    (futur)
```

## Architecture gelée (ne pas rouvrir)

- **Modular Monolith** Python ≥ 3.12, FastAPI = couche HTTP uniquement, Core testable sans FastAPI.
- **Règle d'entrée unique** : CLI, FastAPI, worker, futur consumer Kafka sont des entrypoints qui
  appellent le même Core. Aucune logique métier dans les routes ou les commandes CLI.
- **PostgreSQL** source de vérité (SQLAlchemy 2.0 + Alembic). JSONB réservé aux métadonnées
  variables ; identités, statuts, clés de recherche et relations = colonnes relationnelles.
- **Registry générique** : aucune notion Odoo/DATEV dans `uie_core`. Tout ce qui est spécifique
  à un système vit dans son `SchemaLoader` (`uie_connectors`) et dans les colonnes `metadata` JSONB.
- **Snapshots immutables** : un snapshot publié n'est jamais modifié (checksum + provenance
  dans `schema_snapshots.source`). Nouveau contenu = nouvelle version.
- **Definition vs Instance** : définition standard (Odoo 17) réutilisable pour tous les clients ;
  instance client = connexion réelle + snapshot réel + diff standard/réel.
- Polars limité à INGEST → NORMALIZE ; ensuite tout passe par des IntegrationRecords/Envelopes.
- Idempotence garantie par le Core (Identity Registry, identity_hash / content_hash), jamais par la queue.
- Kafka, Kubernetes, base vectorielle dédiée, microservices : reportés jusqu'à besoin réel.
- Queue : abstraction d'abord ; spike Dramatiq / Celery / arq AVANT d'implémenter le worker.
- Frontend : Next.js + React + TypeScript + Tailwind + shadcn/ui + TanStack Query ; SSE (pas WebSocket) en V1.
- **Charte Transliate, mode clair uniquement** (2026-10-16, remplace la charte GO45 du 2026-09-27 ; pack copié dans
  `apps/web/src/design-pack/`, source `C:\Novobit-AI\Transliate\design-pack`, style guide `index.html`) :
  `transliate.css` importé dans `globals.css` en `layer(components)` (les utilitaires Tailwind restent prioritaires) ;
  Tailwind v4 : le preset v3 du pack n'est pas utilisé, ses valeurs sont dans `@theme` (rayons). Les tokens
  shadcn du thème clair pointent vers ceux du pack. Primitives shadcn RESTYLÉES avec les classes du pack, API
  inchangée : `Button` (`.btn-*` ; primaire = encre `#0f172a`, y compris pour les actions de création ; AUCUN
  bouton orange, `.btn-cta` du pack non utilisé : décision du 2026-10-16), `Input` / `Select` / natifs
  (`.input`), `Field` (`.label` 12px majuscules), `Card` (`.card`, 12px), `Table` (`.tbl`), `Badge` (`.badge-*`),
  `Dialog` (`.modal-overlay`, 16px), `DropdownMenu` / popup de `Select` (`.menu`, `.menu-item`). Bleu brand
  (`--brand` = `#1A56DB`) : focus, liens, navigation active. DM Sans / DM Mono par next/font (pas de Google Fonts au
  chargement). Header : logo Nexa45 en image `apps/web/public/brand/nexa45-logo.png` (recadré,
  fond rendu transparent, 779×144 = 3× l'affichage),
  36px de haut via next/image (logo Transliate NON utilisé) ; nom affiché « Nexa45 · Universal Integration Platform » (onglet, carte de login) ; la NAVIGATION du
  projet est conservée (liens typographiques, actif = texte + trait bleu, menu sous 900px), pas celle du pack.
  Fond de page = celui de la landing GO45 (`go45-fe`) : slate-50 + deux halos en haut (bleu 16 % à gauche, ardoise
  8 % à droite, `--page-gradient`), limités au premier écran et qui défilent avec la page (pas de fond fixe).
  Exception : `/login` = fond de la page de connexion de Transliate (`--login-gradient` : dégradé horizontal
  #e8effb → #f8fafc → #f1f5f9 + halo bleu 8 %), via `data-page-background="login"` sur la racine de la page,
  et mise en page de la connexion Transliate : présentation à gauche (`.hero-badge`, `.h-hero`, slogan, texte,
  pastille), carte de connexion à droite (eyebrow « Authentication ») ; empilées sous `lg`.
  Thème sombre (`.dark`, ancienne charte GO45, `ThemeToggle`) conservé dans le code mais ni appliqué ni proposé.
  Statuts = tokens sémantiques `success / warning / changed / danger / info / third-party / neutral` (couleurs de
  texte des badges du pack, AA en 12px) : jamais de couleur Tailwind codée en dur. Header et contenu sur la grille
  `app-container` (min(1440px, 100% − 40px)) ; exception : `data-content-width="full"`. Badges : `StatusBadge`
  (point à gauche) et `Pill` (point à droite). Origine : une seule source `components/origin.tsx` — STANDARD gris
  (`neutral`), CUSTOM jaune (`warning`), THIRD_PARTY bleu (`info`), UNKNOWN rouge (`danger`). Corps 14px ; titre de
  page (`<h1>`) = `.hero-badge` du pack (pastille bleue avec point, 14px, comme « Secure access » ; décision du
  2026-10-16), sauf `/login` (`.h-hero`) ; rôles (`@utility`) : `page-title` 30 (titres de section larges seulement), `page-description` 14, `section-title` 20, `item-title` 16, `eyebrow`, `meta` 12,
  `tech` (DM Mono : versions, hashes, références, identifiants), `page-stack` ; rayons 8 boutons / champs, 12 cartes,
  16 menus / modales, 28 grands panneaux.
- **Langue de l'interface** : anglais d'abord ; i18n (de, fr) à venir. Commentaires de code en français.
- Outillage : `uv` workspaces (Python), `pnpm` (web), Docker Compose (dev), ruff (lint/format, ligne 110).

## Organisation du monorepo

```
apps/cli/            uie_cli    — Admin CLI `uie` (Typer + Rich)            ✅ fait
apps/api/            uie_api    — API FastAPI : /auth, /admin (Registry, instances, suppliers),
                                  /api (Supplier API, DTO propres)                        ✅ fait (6a, 7-8, V1d)
apps/worker/         worker (queue abstraite)                                ⬜ à faire
apps/web/            console Next.js 16 (App Router, shadcn/ui, TanStack Query) ✅ fait (6b) ;
                                  login, suppliers, portail « Coming soon » (V1d)
packages/core/       uie_core   — registry/ (models, schema, loader, service, compare), instances/
                                  (InstanceService, SchemaDiscoveryProvider, SecretStore, diff),
                                  domains/ (ProductDomainService, DomainPack, modèles, semantics
                                  = moteur de règles, enrichment = SemanticEnrichmentService),
                                  identity/ (contextes, IdentityService, OrganizationService),
                                  catalog/ (CatalogProduct, CatalogReader, plan de lecture, sync),
                                  imports/ (fichiers catalogue : profil, matching, FileStore), db/ ✅ fait
domain-packs/        uie_domain_packs — catalog/vocabulary.yaml, catalog/<système>/product_domain.yaml
                                  (+ règles R01-R18), <système>/protected_fields.yaml (liste de sûreté)
                                  + adaptateur YAML → DomainPack (découverte auto) ✅ V1a/V1b/V1b.1
packages/connectors/ uie_connectors — odoo/ (metadata, loader, discovery provider, catalog reader, testing) ✅ ;
                                  datev/ (spec, loader, spec_builder : Registry D1 ; extf : écrivain EXTF) ✅
packages/cdm/        uie_cdm — Canonical Data Model : Partner 1.0.0 (Pydantic + JSON Schema publié)  ✅ PARTNER
packages/core/…      partners/ (plan + lecture → CDM), mapping/ (binding cible, moteur, contrat d'écriture),
                     exports/ (profil, numéros de compte, aperçu, fichier, journal)                ✅ PARTNER
packages/core/…      accounting/ (lecture → CDM, écritures, préparation et fichier d'un format cible) ✅ ACCOUNTING ph. 5
packages/ai/         uie_ai — proposers du contrat SemanticProposer : Null, Replay, Recording,
                                  OpenAI, Anthropic (sortie structurée)                       ✅ V1c
schemas/cdm, schemas/targets, domain-packs/catalog, docker/, docs/, tests/
```

## Commandes utiles

```bash
uv sync                                           # installe Python 3.12 + workspace
docker compose -f docker/docker-compose.yml up -d postgres
cp .env.example .env                              # .env chargé automatiquement (PowerShell : Copy-Item)
# ou : export UIE_DATABASE_URL=postgresql+psycopg://uie:uie@localhost:5433/uie
#      PowerShell : $env:UIE_DATABASE_URL = "postgresql+psycopg://uie:uie@localhost:5433/uie"
uv run uie db upgrade                             # migrations Alembic
uv run uie registry import odoo --json packages/connectors/tests/fixtures/odoo17_ref.json
uv run uie registry import odoo --json packages/connectors/tests/fixtures/odoo18_ref.json
uv run uie registry systems | show odoo 17 [entité] | fields odoo 18 -s price | compare odoo 17 odoo 18
uv run python -m uie_connectors.datev.spec_builder --portal <datev-format.json> --pruefprogramm <zip> \
    --samples <zip> --out var/datev/specs/extf_700   # specs DATEV LOCALES (hors git), voir « DATEV D1 »
uv run uie registry import datev --all --spec var/datev/specs/extf_700   # ou --spec <fichier>.yaml
uv run uie registry compare datev 12 datev 13 -P buchungsstapel          # -P / --product : show, fields aussi
uv run uie partner analyze | questions | list --instance <id|nom>       # domaine PARTNER (lecture seule)
uv run uie partner confirm --instance <id|nom> --entity res.partner --field x_customer_no [--key K]
uv run uie export targets | profile show|set -t datev/debitoren-kreditoren --org <slug> [--set nom=valeur]*
uv run uie export preview | generate -t datev/debitoren-kreditoren -i <id|nom> [-o fichier.csv]
docker compose -f docker/docker-compose.yml --profile odoo up -d         # Odoo 17/18/19 de référence
uv run uie registry import odoo --url http://localhost:8017 --db ref17 -u admin -p admin
uv run uie registry dump odoo --url ... -o docs/dumps/odoo17_ref.json   # dump reproductible (compact ; --indent N)
uv run uie registry import odoo --json docs/dumps/odoo17_ref.json      # schéma réel complet, hors-ligne
uv run uie api serve [--port 8000] [--reload]     # API : http://127.0.0.1:8000/docs
uv run uie admin bootstrap                        # admin plateforme initial (idempotent ; UIE_INITIAL_ADMIN_*)
uv run uie org list | create --name N [--slug S] | show <slug|id>
uv run uie user create -e E [-n N] [--org S --role OWNER|MEMBER | --admin] | add-member -e E --org S | list
#   mot de passe : UIE_USER_PASSWORD | --password-stdin | saisie masquée (jamais en argument)
pnpm install                                      # dépendances web (workspace pnpm racine), une fois
pnpm dev                                          # console : http://localhost:3000 (API via le proxy /uie-api)
pnpm gen:api                                      # régénère apps/web/openapi.json + src/lib/api/schema.d.ts
pnpm typecheck && pnpm lint && pnpm build
pnpm test                                         # use cases (Overview) + présentation comptable, node --test
uv run uie secret gen-key                         # → UIE_SECRET_KEY=... dans .env (chiffrement des secrets)
docker compose -f docker/docker-compose.yml --profile customer up -d odoo17-customer   # instance client :8027
docker compose -f docker/docker-compose.yml --profile accounting up -d odoo17-accounting   # Odoo DE :8037
uv run python docker/accounting17-setup.py        # base accounting17 : l10n_de, SKR03, EUR, GO45 Demo GmbH,
                                                  # pièces INVENTÉES UIE-ACC-A..M (janvier / février 2026) — idempotent
uv run python docker/customer-customize.py        # x_customer_no (res.partner) ; x_supplier_ref, x_pack_code,
                                                  # x_notes_internal (product.template) + mini catalogue V2a
                                                  # (8 produits, 16 variantes, images) — idempotent
uv run uie instance add -n customer17 -s odoo -v 17 --url http://localhost:8027 --db customer17 -u admin \n    [--org <slug>]                                # défaut : go45-development
#   mot de passe : invite masquée | --password-stdin | UIE_INSTANCE_PASSWORD (jamais en argument)
uv run uie instance list | show <nom> | test <nom> | discover <nom> | diff <nom> [--snapshot N] [--details]
uv run uie instance delete <nom> [--yes]         # instance + connexion + capabilities + snapshots + diffs + secret
uv run uie product-domain show-standard --system odoo --version 17 [--fields]
uv run uie product-domain analyze | show | fields [--group --origin --relevance] | extensions | missing
                          | history --instance <id|nom>          # analyze = domaine effectif + Semantic Enrichment
uv run uie product-domain summary | questions | decisions | exclusions [--extensions] --instance <id|nom>
uv run uie product-domain ai-status --instance <id|nom>          # fournisseur LLM, candidats (aucun appel)
uv run uie product-domain enrich-ai --instance <id|nom> [--field e.f]* [--force] [--max-fields N]
                          [--provider P] [--replay-file F] [--record-replay F]   # Ask AI (V1c)
uv run uie product-domain confirm [--key K] | change --key K|other | ignore | include | reopen
                          --instance <id|nom> --entity <entité> --field <champ> [--note ...]
uv run pytest                                     # 695 tests, PostgreSQL requis (uie_test créée automatiquement)
uv run ruff check . && uv run ruff format .
```

PostgreSQL du Registry exposé sur le port **5433** (5432 souvent pris par un PostgreSQL local sous Windows).
Sous Windows, installer uv : `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

Les migrations Alembic se lancent depuis `packages/core` (`alembic.ini` y est) ; `uie db upgrade` le fait.
Pour une nouvelle migration : `cd packages/core && uv run alembic revision --autogenerate -m "..."`.

## État d'avancement — plan « System Knowledge Registry V1 » (document SKR §17)

1. ✅ Modèle PostgreSQL du Registry + migrations (21 tables ; 23 avec Product Domain V1a ; + 4 tables V1d :
   organizations, users, organization_memberships, user_sessions)
2. ✅ Services Core du Registry sans FastAPI (`RegistryService`, `compare_snapshots`)
3. ✅ CLI Admin minimale (`uie registry ...`)
4. ✅ Schema Loader Odoo (introspection `ir.model` / `ir.model.fields` via XML-RPC, mode dump JSON)
5. ✅ Odoo 17, 18, 19 chargés depuis les vraies instances de référence (2026-09-26, XML-RPC, y compris
   Odoo 19) : 17 → 315 entités / 6056 champs, 18 → 343 / 6440, 19 → 346 / 6954 (transients exclus).
   Dumps reproductibles dans `docs/dumps/odooNN_ref.json` (même checksum que l'import direct).
   Particularités réelles corrigées dans le loader (tests sur extraits réels,
   `fixtures/extract_real.py` → `odoo17_real_extract.json`, `odoo19_real_extract.json`) :
   - `ir.model.modules` est trié alphabétiquement → module d'origine via `ir.model.data` (ordre de chargement) ;
   - `ir.model.abstract` n'existe qu'en Odoo 19 → avant : abstract = ni `write_date` ni ACL (`kind_inferred`) ;
   - `ir.model.fields.compute` est vide hors champs manuels → calculé = non stocké, related ou `depends`
     (via `fields_get`, seul moyen de voir les champs calculés stockés) ;
   - `company_dependent` (fields_get en 17, ir.model.fields en 18+) ; `translate` booléen (≤ 18) ou mode (19) ;
   - les x2many avec domain ont des `depends` sur leur propre nom (« child_ids.active ») : ignorés pour `computed`.
   Registry de dev : un snapshot @1.0.0 par version, importé depuis `docs/dumps/` (voir limites ci-dessous).
6. ✅ Schema Browser + Version Compare (API + console web)
   - 6a ✅ API Admin FastAPI en lecture seule (`apps/api`, SKR §16) : `GET /admin/systems`,
     `/admin/snapshots/resolve?system=&version=`, `/admin/schema-snapshots/{id}`,
     `/admin/schema-snapshots/{id}/entities?search=`, `/admin/entities/{id}` (champs + relations),
     `/admin/entities/{id}/fields`, `/admin/schema-compare?from=&to=&entity=`. Les routes n'appellent que
     `RegistryService` ; réponses = schémas Pydantic dédiés (`uie_api.schemas`) ; session par requête
     (`uie_api.deps.get_session`, surchargée dans les tests) ; `NotFound` → 404 ; CORS GET pour
     localhost:3000. À optimiser : `schema-compare` recharge les deux snapshots complets (~2 s pour
     Odoo 17 → 19, même avec `entity=`).
     Ajouts : `GET /admin/schema-snapshots/{id}/fields?search=&limit=` et pagination
     `?limit=&offset=` sur `/entities` (réponse `{total, limit, offset, items}`).
   - 6b ✅ Console web (`apps/web`, workspace pnpm racine) : `/systems`, `/snapshots/[id]` (Schema Browser,
     champs techniques en gris / `x_` en jaune d'après `metadata.technical` / `metadata.custom`, badge `req`,
     cible des relations sur la ligne du champ), `/compare` (sélecteurs system/version →
     `/admin/snapshots/resolve`, résultat groupé par entité, filtre par type ADDED/REMOVED/CHANGED
     `?types=`, REMOVED en tête de chaque entité).
     Client typé `openapi-fetch` + types `openapi-typescript` générés depuis `apps/web/openapi.json`
     (`pnpm gen:api`, test pytest de fraîcheur). Aucune logique de comparaison côté front.
     Next 16 : lire `apps/web/AGENTS.md` (docs de la version dans `node_modules/next/dist/docs/`).
7. ✅ Instance Management + Test Connection (2026-09-26) — générique, Odoo = premier provider :
   `InstanceService` (create / list / get / test_connection / discover_schema / get_schema_diff) ne connaît que
   le contrat `SchemaDiscoveryProvider` (résolu par system + version + interface via
   `DiscoveryProviderRegistry`) et `SecretStore` ; test d'architecture : aucun import de connecteur ni
   identifiant Odoo dans `uie_core`. API `/admin/instances[...]`, CLI `uie instance ...`, page `/instances`
   (liste + formulaire de création `?new=1`, secret en écriture seule) et `/instances/[id]`.
8. ✅ Discovery + Instance Diff : chaque discovery = NOUVEAU `instance_schema_snapshot` immutable (ORM +
   trigger PostgreSQL), `base_snapshot_id` = baseline standard EXACTE du moment, diff persisté
   (`instance_schema_diffs`, classé) et reproductible (`recompute_diff`). Validé sur l'instance Docker
   `odoo17-customer` (hr + project + `x_customer_no`) : voir « Instances client » ci-dessous.
9. ⬜ Fixer le produit/interface DATEV du POC (note : DATEV n'a pas de catalogue produit → domaine
   CUSTOMER/SUPPLIER ou INVOICE pour Odoo↔DATEV ; PRODUCT reste le domaine d'Excel→Odoo)
10. 🟡 Schema Loader DATEV : D1 ✅ (2026-10-02) Registry des formats EXTF (Buchungsstapel 10-13,
   Debitoren/Kreditoren 5) depuis les sources officielles, voir « DATEV D1 » ci-dessous
11. ⬜ Instance DATEV de test + snapshot
12. 🟡 Domain Bindings : PRODUCT (Product Domain V1a) ; PARTNER ✅ (2026-10-02, clients + fournisseurs dans UN
    domaine, voir « Domaine PARTNER » ci-dessous)
13. 🟡 Odoo → DATEV : PARTNER → Debitoren/Kreditoren (fichier EXTF, sans envoi) ✅ ; DATEV → Odoo ⬜
14. ⬜ Mapping Templates après validation du POC

## Product Domain V1 (docs/PRODUCT_DOMAIN_V1_IMPLEMENTATION_PLAN.docx)

- ✅ **V1a** (2026-09-28, étapes 1-5 et 8 du plan) : définition standard, overlay instance, snapshot immutable.
  Console : `/instances/[id]/product-domain` (vue technique « Advanced », lecture seule, lien depuis
  `/instances/[id]`) : bouton Analyze, historique des domain snapshots (`?domain=`), entités par rôle, médias,
  extensions, missing standard, champs par groupe filtrés PAR L'API (`?group=&origin=&relevance=`) ;
  couleurs d'origine = convention commune de la console (`components/origin.tsx`, voir la charte).
- ✅ **V1b** (2026-09-28, sections 8 et 10-16 du plan) : Semantic Enrichment par RÈGLES + décisions humaines
  (aucun LLM, aucune dépendance vers packages/ai), page supplier `/instances/[id]/catalog` « Product Catalog
  Analysis ». Voir « Semantic Enrichment V1b » ci-dessous.
- ✅ **V1b.1** (2026-09-28) : réduction des POSSIBLE par règles génériques R11-R18, sans LLM, champs protégés
  jamais exclus (125 → 23 POSSIBLE sur customer17, 3 questions, 0 faux négatif).
- ✅ **V1c** (2026-09-28) : proposition sémantique par LLM derrière `SemanticEnrichmentService`
  (« Ask AI »), voir « LLM (V1c) » ci-dessous.
- ✅ **V1d** (2026-09-29) : comptes fournisseurs et isolation multi-tenant, voir « Comptes et isolation
  multi-tenant (V1d) » ci-dessous.
- ✅ **V2a — Real Catalog** (2026-09-29) : lecture des vrais produits de l'ERP via les semantic bindings,
  CatalogProduct canonique, pages Catalog et fiche produit du fournisseur, voir « Catalogue réel (V2a) »
  ci-dessous.
- ✅ **V2b.1 — Connect my system** (2026-09-29) : le fournisseur connecte lui-même son Odoo (My systems →
  Connect a system → Test connection → Save). ✅ V2b.2 : Analyze côté
  fournisseur.
- ✅ **V2c.1 — Catalog file compatibility** (2026-09-30) : UPLOAD .xlsx / .csv → système cible → rapport de
  compatibilité des colonnes, sans écriture vers l'ERP. Voir « Imports de fichiers (V2c.1) » ci-dessous.
- ✅ **V2c.2 — Learned Mapping Memory** (2026-09-30) : les choix explicites du fournisseur (Choose / Change
  target, Confirm) sont réappliqués aux imports suivants de SON organisation, sans LLM ni action en plus.
- ✅ **V2c.2b — Learned Not Relevant** (2026-09-30) : un Ignore explicite (colonne REVIEW / UNMATCHED, Ignore all
  unmatched) est mémorisé pour l'organisation ET le système cible, puis réappliqué d'office.
- ✅ **V2c.3 — LLM fallback for first-time imports** (2026-09-30) : après mémoire et règles, les colonnes
  restantes partent en UN appel LLM ; TARGET ≥ 0.95 résolu (V2c.5b), le reste (dont « Probably not needed ») proposé au
  fournisseur.
- ✅ **V2c.4 — Target Catalog Coverage** (2026-10-01) : cibles d'import = clés PLATES du Product Domain
  (`IMPORT_KEYS`), séparées de l'affichage V2a ; tags, coût, ventes / achats, descriptions, image principale par
  URL. ⬜ Suite : variantes (tranche à part).
- ✅ **V2d.1 — Import Preview** (2026-10-01) : ce que l'import essaierait de faire, en LECTURE SEULE (plan
  d'import + lignes préparées réutilisables par l'import réel).
- ✅ **V2d.2 — Real Odoo Import** (2026-10-01) : première écriture dans l'Odoo d'un client, idempotente
  (request_key, items commités avant l'appel, identifiant externe Odoo) ; bout en bout réel préparé, pas encore exécuté.
  ⬜ Suite : V2d.2.1 (images), variantes, offre fournisseur.

Concepts V1a :
- **Domain pack** = donnée : `domain-packs/catalog/<système>/product_domain.yaml` (entités + rôle, champs +
  groupe, règle media, valeurs par défaut) ; l'adaptateur `uie_domain_packs` le valide et l'enregistre dans
  `DomainPackRegistry` d'après son `domain` / `system`. `uie_core.domains` ne lit aucun fichier et ne
  contient aucun nom de système (test d'architecture AST). Le pack DÉCLARE (union des versions) ; la
  résolution contre un snapshot rend `present=false` un élément absent, jamais une erreur.
- Champs d'une entité du scope : médias (règle du pack) → `media` ; listés → leur groupe, RELEVANT ;
  techniques (`metadata.technical`) → `technical`, NOT_RELEVANT ; autres → `other`, POSSIBLE.
  Médias Odoo : type natif `binary` → détection par nom (`image_N`, `image_variant_N`) ; plus haute
  résolution = main, autres tailles = derivative ; entités VARIANT = variant.
- **Définition standard** (`product_domain_definitions`, immutable) : pack résolu sur un snapshot standard
  exact ; persistée une fois, avec domain_bindings (entité ↔ PRODUCT ↔ rôle). Même `definition_version` +
  contenu différent = refus → **toute modification du YAML impose d'incrémenter definition_version**.
- **Domaine effectif** (`product_domain_snapshots`, immutable) : snapshot d'instance PERSISTÉ + sa baseline
  exacte + son diff. Standard + scope présent dans l'instance + extensions (ADDED CUSTOM / THIRD_PARTY du
  scope, relevance UNRESOLVED : c'est V1b qui décidera) − missing_standard. Une entité ajoutée hors scope
  (hr.employee) n'entre pas. Même snapshot d'instance + même définition → même domain snapshot ; nouvelle
  découverte → nouveau domain snapshot, l'ancien intact. Suppression d'instance → CASCADE.
- Jamais de lecture d'instance en direct, jamais de LLM, jamais de valeurs métier échantillonnées.

## Semantic Enrichment V1b — concepts

Le moteur fait le maximum seul ; le supplier ne répond qu'aux questions réellement ambiguës.
- **Vocabulaire** (`domain-packs/catalog/vocabulary.yaml`, 1.2.0) : semantic_key + libellé, alias, motifs,
  types attendus, rôle de la cible (`relation_target_roles`) et des entités (`entity_roles`). Une clé
  déclarée par le pack (`key:` d'un champ du YAML) prime (0.98) et ignore `entity_roles`. Nouvelle clé :
  pas d'alias générique et `entity_roles` au plus juste, sinon un recouvrement de tokens crée une
  proposition (donc une QUESTION) sur un autre champ standard ; vérifier sur les 4 snapshots de mesure.
- **Proposition** (au plus une clé) : clé du pack 0.98, alias exact (nom ou libellé) 0.95, motif 0.90,
  libellé du vocabulaire 0.85, recouvrement de tokens ≥ 0.6 → 0.60-0.80 ; type incompatible ou cible du
  mauvais rôle → pas de proposition.
- **Pertinence** : règles du pack (`semantics.rules`, données) évaluées dans l'ordre, la première gagne,
  `rule_id` tracé, raison lisible (anglais). Ordre : R01 technique, R02 listé, R03 média, R04 relation hors
  catalogue, R05 modules hors catalogue, R06 helper calculé non stocké, R07 drapeau d'UI, R18 copie related
  standard (suit son champ d'origine), R08 vocabulaire (≥ 0.90), puis les exclusions V1b.1 : R11
  back-référence, R12 quantité / compteur opérationnel, R15 mécanique ORM / aide d'UI, R16 valorisation
  comptable, R17 défaut opérationnel de catégorie, R13 logistique / réapprovisionnement, R14 politique de
  vente / achat ; enfin R09 custom sans correspondance, R10 défaut. Les exclusions viennent APRÈS R08 : un
  champ qui correspond au vocabulaire reste RELEVANT. Une règle porte sur une PROPRIÉTÉ du champ (type,
  calculé / stocké, relation et rôle de la cible, module, motif de nom), jamais sur une liste de noms.
  Conditions : `RuleCondition` (dont `back_reference`, `related_copy`, `entity_roles`) ; une règle a
  `relevance` OU `follow_source`.
- **Champs protégés** (`<système>/protected_fields.yaml`, liste de sûreté, pas une règle) : champs du
  catalogue par nature + toute extension CUSTOM + tout média. Une règle NOT_RELEVANT ou follow_source qui
  leur correspond est sautée (`ProtectionSpec`) : seul un humain peut les ignorer. Test de sûreté sur les
  dumps complets 17 / 18 / 19 et sur l'instance client. Entre dans le checksum des règles.
- **Statut** : NOT_RELEVANT → EXCLUDED ; proposition ≥ 0.90 sur champ STANDARD → KNOWN ; sinon (ou
  extension) → PROPOSED ; RELEVANT sans clé → KNOWN ; sinon UNRESOLVED. Question (`pending`) = PROPOSED, STALE,
  ou UNRESOLVED hors STANDARD. Clé `single` : deux KNOWN dans une entité → le second PROPOSED (« Conflicts
  with … ») ; proposition faible sur une clé déjà tenue avec certitude → abandonnée (pas de question).
- **Copies related** : une copie `related` d'un champ d'une autre entité du domaine suit la décision FINALE
  de ce champ, jamais une question en double : extension (`product.product.x_supplier_ref` ←
  `product_tmpl_id.x_supplier_ref`) → rule MIRROR ; champ standard non protégé → règle R18 (« Reflects … »,
  `mirror_of`), chaînes résolues (copie d'une copie). Une copie ne compte pas dans l'unicité. Statut EXCLUDED
  = NOT_RELEVANT par une règle (y compris une copie d'un champ exclu), contestable (include).
- **FieldSemanticBinding** (`field_semantic_bindings`, clé logique instance + domaine + entité + champ ; le
  snapshot est une provenance) + journal `field_semantic_binding_events` (chaque changement de statut).
  Règles de réutilisation à chaque analyse (`ProductDomainService.analyze` → `enrich`) :
  décision HUMAN (CONFIRMED / IGNORED / include / « other ») → signature compatible (même type natif, même
  cible, sélection ⊇) = réappliquée ; signature changée = STALE (question reposée, ancienne réponse
  pré-sélectionnée) ; champ disparu = ORPHANED (conservé, invisible, restauré s'il réapparaît). Décision de
  règle = recalculée ; **une règle n'écrase jamais une décision HUMAN**. Lire (API, page) ne recalcule rien.
- Le domain snapshot référence `semantic_rules_version` / `semantic_rules_checksum` (pack + vocabulaire) :
  modifier le vocabulaire OU le pack impose d'incrémenter `definition_version`.
- Réponses : confirm (proposition ou clé), change (clé du vocabulaire ou `other` → UNRESOLVED + note),
  ignore (IGNORED), include (conteste une exclusion → question). Clé hors vocabulaire → 400.
  Correction d'une erreur : `list_decisions` (réponses du supplier, la plus récente d'abord, compteur
  `supplier_decisions` hors partition) ; change / ignore directement, ou reopen → question de nouveau
  (décision HUMAN pending, clé confirmée pré-sélectionnée, `previous` = ancienne réponse, événement REOPEN).
- Résumé : chaque champ (hors ORPHANED) dans UN compteur : pending + excluded_by_supplier + excluded_auto
  + identified + possible = total ; `complete` = au moins un enrichissement et 0 question.
- Console : `/instances/[id]/catalog` (lien « Product Catalog Analysis ») : contrôles de structure, compteurs,
  cartes de question (Confirm / Change / Ignore, bandeau STALE), dialogue « Change meaning » (`?change=`),
  revue des exclusions avec Include (`?review=all|extensions`), section « Your answers » (Change / Ignore /
  Reopen, bandeau « You reopened this answer ») ; état final « Catalog analysis complete ».

**Mesure de référence SANS LLM — baseline pour V1c** (V1b.1, customer17, dump complet
`docs/dumps/odoo17_customer.json` = domaine du snapshot #6, règles 1.3.0+vocabulary-1.2.0, 2026-09-28, avant
toute réponse) : 430 champs ; identifiés 132 (31 %) ; exclus automatiquement 272 (63 % : R01 45, R04 74,
R05 2, R06 51, R07 8, MIRROR 4, R11 3, R12 19, R13 9, R14 10, R15 16, R16 6, R17 2, R18 23) ; possibles
restants **23** (5 %, ce que V1c devra traiter, liste et raison par champ dans le rapport) ; questions : 3
(x_supplier_ref, x_pack_code, x_notes_internal). Odoo standard 17 / 18 / 19 : 20 / 25 / 27 POSSIBLE, 0 question.
Tests : `domain-packs/tests/test_catalog_customer17_v1b1.py` (seuil = 23, les 121 identifiés V1b restent
identifiés). Avant (V1b, règles 1.1.0) : 117 identifiés, 185 exclus, 125 possibles.

## LLM (V1c) — principe et frontière

**Le LLM aide à comprendre un champ. Il ne décide jamais de retirer un champ du catalogue.**
`Rules → LLM semantic proposal → Human confirmation when needed`. Le LLM ne produit JAMAIS NOT_RELEVANT,
ni pertinence, ni exclusion, ni KNOWN : le seul chemin vers l'exclusion reste une règle ou un humain.

**Le LLM ne peut proposer que ce que le vocabulaire contient ; la qualité du vocabulaire borne la qualité
de l'IA.** Il choisit une clé parmi celles offertes pour le champ, ou s'abstient. Un sens absent du
vocabulaire donne au mieux une abstention (mesure customer17 : les 5 propositions justes utilisent toutes
une clé ajoutée pour V1c). Améliorer l'IA commence donc par le vocabulaire (clés légitimes, sans fourre-tout,
entity_roles au plus juste), vérifié sur les 4 snapshots de mesure, avant tout travail sur le prompt.
- **Frontière** : contrat `uie_core.domains.proposer` (ProposalRequest / ProposalResult, Protocol
  `SemanticProposer`) ; implémentations dans `uie_ai` (Null par défaut, Replay, Recording, OpenAI,
  Anthropic). Aucun import de fournisseur dans `uie_core` (test). Racines de composition : `build_proposer()`.
  Toute réponse brute passe par `ProposalResult.from_raw` : MATCH (clé du vocabulaire FOURNI, confiance,
  justification ≤ 200) ou NO_CONFIDENT_MATCH ; clé hors vocabulaire → NO_CONFIDENT_MATCH + erreur ;
  pertinence / exclusion dans une réponse → ignorées + événement AI_IGNORED. Un proposer ne lève jamais.
- **Opération explicite** : `enrich_with_llm` (API `POST .../enrich-ai`, CLI `enrich-ai`, bouton Ask AI) ;
  jamais par analyze, discover, une lecture ou l'ouverture d'une page (tests). Cibles : POSSIBLE /
  UNRESOLVED sans décision humaine, hors copies related, hors propositions de règle (sauf force), hors
  cache (signature technique + checksum du prompt, sauf force), budget max_fields 50. Seules les clés
  compatibles (type, rôles) sont offertes ; aucune compatible → pas d'appel.
- **Seuils** : MATCH ≥ 0.85 → PROPOSED source LLM ; 0.60-0.85 → PROPOSED low_confidence ; sinon (dont
  conflit d'unicité, échec) → UNRESOLVED + `ai_note`. Toujours une question (jamais KNOWN). Ré-analyse :
  proposition conservée tant que la signature est la même et que la règle n'a rien de plus sûr.
- **Clés tenues et arbitrage** : une clé `single` est tenue par un seul champ (KNOWN, CONFIRMED, ou proposée
  par le LLM hors du lot) ; la requête nomme le champ qui la tient (`keys_held_on_entity` : clé → champ) et
  le prompt 1.1.0 demande alors NO_CONFIDENT_MATCH en nommant ce champ. Dans un lot, deux MATCH sur la même
  clé `single` → la plus haute confiance la garde (jamais l'ordre de traitement), l'autre = conflit.
- **Prompt** : `domain-packs/catalog/llm/product_semantic_proposal.yaml` (1.1.0, versionné, checksum =
  provenance) ; exemples INVENTÉS uniquement (jamais un système mesuré : test). Modifier le prompt =
  incrémenter `version` (les champs déjà analysés redeviennent candidats à Ask AI).
- **Journal** : `field_semantic_binding_events.details` (fournisseur, modèle, prompt, request id, tokens,
  latence, coût, réponse brute) ; `ai-status` agrège sans appeler.
- **Données envoyées** : métadonnées du champ uniquement (nom, libellé, aide, types, cible, sélection,
  modules), `request_ref` opaque ; jamais de valeur métier, de secret, d'URL ni de nom d'instance.
- **Variables** (`.env.example`) : `UIE_LLM_PROVIDER` (none | openai | anthropic | replay : interrupteur
  explicite, une clé seule n'active rien), `OPENAI_API_KEY` / `OPENAI_MODEL` (ou `UIE_OPENAI_API_KEY` /
  `UIE_LLM_MODEL`, prioritaires), `UIE_ANTHROPIC_API_KEY`, `UIE_LLM_REPLAY_FILE`, `UIE_LLM_TIMEOUT`,
  `UIE_LLM_PRICE_INPUT` / `_OUTPUT` (coût estimé).
- **Replay** : `domain-packs/catalog/llm/replay/customer17_v1c.json` (openai / gpt-5.6-sol, prompt 1.1.0),
  rejoué par les tests à travers la même validation ; `--record-replay` pour en enregistrer un nouveau.

**Mesure à trois niveaux — baseline V1c** (customer17, règles 1.4.0+vocabulary-1.3.0, prompt 1.1.0,
openai / gpt-5.6-sol, 2026-09-28) :

    430 champs → 15 ambiguïtés → 5 propositions + 10 sans proposition fiable → 0

- Rules only : 430 champs → 15 ambiguïtés (POSSIBLE hors copies related).
- Rules + LLM : 5 propositions ≥ 0.85 (attribute_value_image, supplier_discount, supplier_net_price,
  property_values, uom_ratio sur factor_inv), 0 faible confiance ; 10 sans proposition : 3 sans clé
  compatible (aucun appel), 1 conflit arbitré (ratio, 0.85 < factor_inv 0.99), 1 doublon nommé par le
  modèle (code : « Same data as product.product.default_code (sku) »), 5 abstentions.
- Human : 15 questions → 0 (5 confirmées, 10 ignorées). Précision : 5 / 5 propositions justes.
- Coût : 12 appels, 18 507 + 1 142 tokens. Re-discover (#7) → analyze : 18 décisions réappliquées,
  0 question, aucun appel LLM. Test : `domain-packs/tests/test_catalog_customer17_v1c.py`.

## Catalogue réel (V2a) — concepts

V2a validée le 2026-09-29 (V2b = onboarding et workspace). Lecture seule vers l'ERP.

    Odoo ──► OdooCatalogReader ──► CatalogReadPlan (semantic bindings) ──► CatalogProduct ──► /api ──► portail

- **Le connecteur sait lire, pas ce que ça veut dire** : contrat `uie_core.catalog.CatalogReader` /
  `CatalogSource` (compter, lire une page, lire par ids, lire un binaire) ; `OdooCatalogReader`
  (`uie_connectors.odoo.catalog`, enregistré par `@CatalogReaderRegistry.register`) réutilise la connexion
  de la discovery et son `ReadOnlyObjectProxy`. Valeurs normalisées : référence → `Ref(id, label)`, collection →
  ids, binaire → présence (contexte `bin_size`), vide → None ; archivés lus (`active_test=False`).
- **Le Core décide quoi lire** (`build_read_plan`, aucun nom de système, test AST) : entités par RÔLE
  (PRIMARY, VARIANT, CATEGORY, VARIANT_VALUE, SUPPLIER_INFO), champs par SEMANTIC KEY, liens par clés
  (`product_template` variante → produit, `category`, `variant_identity` → valeurs d'attribut,
  `supplier_offers`). Seuls les bindings KNOWN / CONFIRMED : un champ exclu, ignoré ou en question n'est
  jamais lu ; une décision du fournisseur change le plan à la synchronisation suivante. Plusieurs champs pour
  une clé : décision humaine > champ propre (pas une copie related) > stocké > clé du pack > listé > nom ;
  sur la variante, une copie related du produit n'est pas lue (customer17 : prix de variante = `lst_price`).
  Extensions CUSTOM / THIRD_PARTY confirmées = « Additional information » libellée par le vocabulaire.
  Images : champ `main_image` / `variant_image` ; vignette et grand format (≤ 1024) choisis grâce à
  `DomainPack.media_size` (le pack YAML le déduit de sa règle media), jamais par un nom de champ dans le Core.
- **CatalogProduct** (`uie_core.catalog.model`, `CATALOG_MODEL_VERSION = "1.0.0"`) : produit métier + variantes
  regroupées (attributs « Color: Red », SKU, EAN, prix, image), attributs, offres fournisseur, informations
  supplémentaires ; champs secondaires `None` si absents ; une variante unique sans attribut est repliée dans
  le produit. `source_ref` (id source) sert à relire une image, jamais exposé.
- **Synchronisation** (`CatalogSyncService`, D1 / D4 / D5) : refusée tant que l'analyse n'est pas complète
  (`CatalogNotReady` → 409), synchrone par lots de 200, plafond `UIE_CATALOG_SYNC_MAX` (10 000), idempotente
  (`content_hash`), produit disparu → `removed_at` (sauf sync partielle), échec de lecture consigné (FAILED,
  sans secret). Tables `catalog_syncs` (plan + checksum = provenance) et `catalog_products` (projection COURANTE
  mutable, colonnes de recherche + CatalogProduct en JSONB), rattachées par `instance_id` (CASCADE).
- **Lecture** (`CatalogReadService`) : filtrée par l'instance du contexte ; recherche sur nom, SKU et
  codes-barres des variantes, catégorie ; image lue À LA DEMANDE dans l'ERP d'après le plan de la sync
  (jamais stockée) ; produit d'un autre tenant = même NotFound, ni secret ni connexion.
- **API** : Supplier `POST|GET /api/systems/{id}/catalog/sync`, `GET /api/products`, `/api/products/{id}`,
  `/api/products/{id}/image`, `/api/products/{id}/variants/{vid}/image` (`?size=thumb|large`) — DTO sans id ni
  nom source ; Admin `GET /admin/instances/{id}/catalog/read-plan`.
- **Portail** : `/portal/catalog` (état Analyze → Catalog ready → Sync, liste Image / Product / SKU / Category /
  Price / Status, recherche, pages) et `/portal/catalog/[productId]` (fiche, variantes, fournisseur,
  informations supplémentaires). Les questions ouvertes se traitent encore dans l'Admin Console (V2b).
- **Données de démo** : `docker/customer-customize.py` ajoute à `odoo17-customer` un mini catalogue INVENTÉ
  et idempotent (8 produits, 16 variantes, images PNG générées, fournisseur, produit archivé).
  Mesure réelle (2026-09-29) : sync 8 produits / 16 variantes / 5 catégories / 5 images en 0,7 s ; 2e sync :
  0 changement.

## Connect my system (V2b.1) — concepts

- **Test sans enregistrement** : `InstanceService.check_connection` (rien n'est stocké) ; version DÉTECTÉE sur le
  serveur (`provider.detected_version`, Odoo : `server_serie` « 17.0 » → « 17 »), jamais choisie ; version absente
  du Registry → non prise en charge. Cause d'échec classée par le provider (`ConnectionTestResult.error_kind` :
  UNREACHABLE, AUTH_FAILED, DATABASE_NOT_FOUND, INCOMPLETE, UNEXPECTED) → phrase simple côté fournisseur
  (`uie_api.supplier.CONNECTION_MESSAGES`), jamais le message technique ; conservée dans `last_test_info`.
- **Enregistrement** : `InstanceService.connect` re-teste (le mot de passe ne vit que dans le formulaire du
  navigateur, E1), crée l'instance dans l'organisation du CONTEXTE (jamais un organization_id du client),
  environnement « production » (E4), puis enregistre le test (Connected). Aucun Analyze automatique.
- **Garde des adresses (E3)** : pour un fournisseur seulement, http / https et aucune adresse interne
  (loopback, privée, link-local, réservée) sauf `UIE_ALLOW_PRIVATE_ENDPOINTS=true` (dev : customer17 sur
  localhost:8027) ; Admin et CLI non concernés. Limite : pas de protection contre un DNS qui changerait.
- **API** : `POST /api/systems/check-connection`, `POST /api/systems` (201 ; 422 échec ; 409 nom déjà pris ;
  400 adresse refusée) ; `SupplierSystemOut` expose `server_url` et `database`, jamais d'identifiant.
- **Portail** : `/portal/systems` (cartes Nom / Odoo 17 / Connected, Open), `/portal/systems/connect` (choix
  d'Odoo, formulaire, Test connection, Save actif seulement après un test réussi sur les MÊMES valeurs),
  `/portal/systems/[id]` (« Your system is connected successfully. », version, statut, adresse, base).
- **Wizard « Connect a system »** : 4 étapes (Select system → Connection details → Test connection → Review &
  save), même backend ; systèmes connectables et versions lus dans le Registry (`GET /api/connectors`) ; un
  test ne vaut que pour les valeurs testées ; logos dans `apps/web/public/systems/<code>.svg|png` (actuellement
  des tuiles PNG recadrées depuis la maquette, à remplacer par les SVG officiels).
- **Analyze fournisseur (V2b.2)** : `CatalogAnalysisService` (uie_core.domains.catalog_analysis) orchestre le
  moteur V1 : test de connexion → découverte (toujours) → `ProductDomainService.analyze` → `enrich_with_llm`
  seulement si LLM configuré et candidats (cache V1c : 0 appel à la ré-analyse ; seules les erreurs sont
  retentées) ; aucune sync (étape V2a distincte). Page du système : Analyze, questions, Catalog analysis
  complete, Continue to catalog, Re-analyze. Proxy Next `proxyTimeout` 5 min (analyse synchrone avec LLM).
- **Supprimer un système (V2b.1a)** : `DELETE /api/systems/{id}` (tout membre, suppression définitive après saisie
  du nom) via `InstanceService.delete` : cascade complète (analyse, décisions, catalogue synchronisé), secret
  retiré après le commit, rien de modifié dans le système source.
- **Aide DEV** : bouton « Fill customer17 (DEV) » sur `/portal/systems/connect` (pré-remplit seulement) ; valeurs
  `UIE_DEV_ODOO_*` dans `apps/web/.env.local` (ignoré), servies par la route Next `/dev/connect-fill` (404 hors
  `next dev` / hors requête locale) ; absent des builds de production ; jamais lu depuis le SecretStore.

## Imports de fichiers (V2c.1) — concepts

    UPLOAD → profil (forme seulement) → FileStore │ ANALYZE (système prêt) → MATCHED / REVIEW / UNMATCHED → READY

- **Aucun nouveau moteur, aucun LLM** : les CIBLES = plan de lecture V2a de l'instance (`import_targets` : clés
  KNOWN / CONFIRMED du produit PRIMARY et de l'offre SUPPLIER_INFO, extensions confirmées ; un champ ignoré n'est
  jamais une cible) ; en-tête → semantic key = matcher V1b (`propose_for_name`, extrait de `_Matcher.score` sans
  changement de comportement) ; règle propre aux en-têtes courts : tous les mots de l'en-tête dans le libellé /
  un alias d'une cible → 0.70 (REVIEW, jamais MATCHED). MATCHED = ≥ 0.90 + cible présente + type compatible ;
  REVIEW = LOW_CONFIDENCE, TYPE_MISMATCH, NOT_IN_SYSTEM (sens reconnu, pas de champ sur ce système),
  TARGET_TAKEN (une cible par colonne, la plus sûre la garde) ; UNMATCHED sinon. Une référence (catégorie,
  unité) accepte une colonne texte : sa résolution relève de l'import réel (V2c.2).
- **Lecture** (`uie_core.imports.profile`, Polars + fastexcel, INGEST seulement) : première feuille d'un .xlsx,
  CSV UTF-8 ou cp1252, séparateur détecté ; types détectés sur les valeurs (texte, entier, décimal, oui/non, date,
  vide) ; limites 10 Mo, 20 000 lignes, 200 colonnes ; messages simples (`ImportFileError` → 400).
- **Fichier hors PostgreSQL** : contrat `FileStore` → `LocalFileStore` (`UIE_IMPORT_DIR`, défaut `var/imports`,
  ignoré par git, `<organisation>/<import>.<ext>`) ; la base ne garde que `storage_ref`, taille, sha256, forme
  (colonnes, types) : aucune valeur de cellule. Fichier supprimé avec l'import APRÈS le commit ; upload annulé →
  fichier retiré. L'original sert à l'import réel (V2c.2).
- **Tables** (migration `485793d82901`) : `catalog_imports` (organization_id DU CONTEXTE, instance_id SET NULL :
  système supprimé → l'import revient à « Choose a system ») et `catalog_import_columns` (clé, cible opaque
  `groupe:clé` + champ technique jamais exposé, confiance, statut, raison, ignored, decided_by AUTO | SUPPLIER).
- **Service** `CatalogImportService` (upload réservé à un TenantContext ; get / analyze / targets / choose_target /
  ignore_column / delete filtrés par organisation, autre tenant = même NotFound ; cible = instance de la MÊME
  organisation dont l'analyse du catalogue est complète, sinon `CatalogNotReady` → 409 ; cible déjà prise →
  `ImportConflict` → 409). Décisions propres à l'import ; une nouvelle analyse les recalcule.
- **API** `/api/imports` (POST multipart `python-multipart`, GET), `/api/imports/{id}` (GET, DELETE), `/analyze`
  `{system_id}`, `/targets`, `/columns/{position}/target` `{target_id}`, `/columns/{position}/ignore` ; DTO
  `uie_api.supplier_imports` (libellés métier, aucun nom technique, ni stockage ni empreinte).
- **Portail** : entrée « Imports » ; `/portal/imports` (liste), `/portal/imports/new` (Upload → Target system →
  Analyze compatibility ; `?import=` reprend à l'étape 2), `/portal/imports/[id]` (compteurs, table Source column /
  Target / Status, Confirm / Choose target / Ignore column, « Ready for import » + « Import to Odoo » désactivé).
- **Référence** : `domain-packs/tests/fixtures/imports/supplier_catalog.{csv,xlsx}` (10 produits INVENTÉS) sur le
  domaine complet de customer17 : 6 MATCHED, Price REVIEW (→ Sales price), Material UNMATCHED.
- **Colonnes vides** : état EMPTY (« Empty in this file »), jamais de cible, hors compteurs Matched / Review /
  Unmatched, ni ignorées ni choisissables ; réglées pour « Ready ». **Type** = celui d'au moins 95 % des valeurs ;
  les valeurs minoritaires sont comptées (`invalid_values`, migration `97651438682e`) pour l'import réel.
  **En-têtes** normalisés génériquement (parenthèses / crochets, ponctuation finale, pluriel simple du dernier
  mot en second essai). **Ignore all unmatched columns** : UNMATCHED → IGNORED (conservées).
- **Mémoire apprise (V2c.2)** : `learned_column_mappings` (migration `8c3d5f3e3355`, organisation + `header_key`
  = en-tête nettoyé et normalisé → semantic_key, usage_count). Pipeline : EMPTY → mémoire exacte (clé = cible
  valide du système ET type compatible, sinon rien) → vocabulaire / règles → REVIEW / UNMATCHED ; `decided_by`
  AUTO | LEARNED | SUPPLIER. Apprise uniquement dans `choose_target`, jamais si les règles donnent déjà ce
  résultat (`match_by_rules`), remplacée par une nouvelle décision ; rien d'Ignore / EMPTY / MATCHED auto. KPI
  figés à l'analyse (resolved_auto, resolved_learned, needs_attention) → API `analysis`, provenance `source`.
  Benchmark : décisions humaines requises 29 → 28 (1er → 2e import) ; le reste
  = colonnes ignorées (Ignore non mémorisé en V2c.2, voir V2c.2b).
- **NOT_RELEVANT appris (V2c.2b)** : même table (migration `42b6c816119f`), colonne `decision` MAPPED |
  NOT_RELEVANT (CHECK `decision_shape`) : MAPPED = semantic_key, `system_id` NULL (toute l'organisation) ;
  NOT_RELEVANT = sans clé, `system_id` = système du Registry (odoo, datev... : ni l'instance, perdue à la
  reconnexion, ni la version). Deux index uniques partiels (un mapping par en-tête ; un NOT_RELEVANT par en-tête
  ET par système). Pipeline : EMPTY → NOT_RELEVANT du système (colonne ignorée d'office, sans cible, hors
  unicité, `decided_by` LEARNED) → mapping appris → règles. Appris par `ignore_column` (sauf colonne MATCHED par
  les règles seules, `decided_by` AUTO : décision ponctuelle) et `ignore_unmatched` (déjà confirmé par
  l'utilisateur, UNMATCHED seulement). `choose_target` retire le NOT_RELEVANT du système ; ignorer un mapping
  appris crée le NOT_RELEVANT du système, le mapping reste pour les autres systèmes. KPI `resolved_ignored`
  → API `analysis.learned_ignored` ; portail : « Ignored based on your previous choices », « No action needed ».
- **Import réel (V2d.2)** : contrat d'ÉCRITURE `uie_core.imports.writer` (`CatalogWriter` / `CatalogTarget`,
  `CatalogWriterRegistry` / `CatalogWriterResolver`, symétrique de la lecture ; `find_by`, `resolve_labels`,
  `create_with_key`, `update` ; jamais de suppression ; `CatalogWriteError` classée CONNECTION / RIGHTS /
  VALIDATION / UNEXPECTED). `CatalogImportExecutionService` (`uie_core.imports.execution`) : `prepare` (V2d.1) →
  run `catalog_import_runs` identifié par `(import_id, request_key)` (même clé = même exécution ; un seul RUNNING
  par import) → relations résolues une fois (carte dans le run) → identités (`IDENTITY_KEYS = {"sku"}`, un
  `find_by`) → un item `catalog_import_run_items` par ligne, commité PENDING avec son `external_key` AVANT l'appel.
  Valeur vide / invalide jamais envoyée (une mise à jour n'écrase rien) ; non importable → SKIPPED ; SKU unique →
  UPDATE, multiple → FAILED, absent → CREATE ; relation ambiguë jamais devinée ; coupure → reprise avec la même
  clé (`/runs/{id}/resume`). `is_writable` : groupe produit, sortes value / label / link / labels ; images et offre
  fournisseur « not imported yet ». Odoo (`uie_connectors.odoo.writer`) : proxy d'écriture à liste blanche
  (lecture + create / write / load, jamais unlink ; le proxy lecture seule est inchangé), `load` + identifiant
  externe `__uie_import__.item_<clé>` (création idempotente), `complete_name` / `name` sans casse, chaîne de
  parents « A / B ». API `POST|GET /api/imports/{id}/runs`, `GET .../runs/{run_id}`, `POST .../resume`,
  `ImportOut.last_run` ; commit par ligne (`session.commit`). Portail : confirmation (clé générée à
  l'ouverture), page de résultat `/portal/imports/[id]/runs/[runId]`, statut de la dernière exécution dans la
  liste. Migration `8928c745e1c7`. Bout en bout réel sur une base Docker locale de
  test (`customer17` par défaut, choix de l'utilisateur, ou `customer17_import`), sauvegarde Odoo prise avant
  toute écriture.
- **Preview d'import (V2d.1)** : `uie_core.imports.plan` (PUR) — `build_import_plan` (décisions VALIDÉES de
  l'import × cibles COURANTES de l'instance → colonnes importées + sort de chaque colonne : IMPORTED, IGNORED,
  NO_TARGET, EMPTY, TARGET_GONE ; une décision validée reste importée tant que sa cible existe : le type DÉTECTÉ
  de la colonne ne l'annule jamais, les valeurs sont vérifiées ligne par ligne (correctif « Sales Price ») ; résumé : `rows_present` ≠ `rows_valid` ; empreinte = plan + empreinte
  du fichier), `normalize_rows`
  (`NormalizedRow.records` groupe → champ technique → valeur préparée : nombre, booléen, date, clé de sélection,
  `Ref(label)`, `Labels`, `MediaUrl` ; vide = rien à écrire ; jamais résolu, créé, lu ni téléchargé) et
  `summarize`. `CatalogImportService.prepare` (import READY sinon 409 ; fichier lu une fois) = la préparation que
  l'import réel (V2d.2) réutilisera ; `preview` la résume. Ni système cible, ni LLM, ni écriture (tests espions).
  Bloquant : champ obligatoire SANS valeur implicite = `required` du binding ET texte (le nom) ; les obligatoires
  référence / sélection / nombre / booléen ont un défaut côté système ; `required_for_catalog` = avertissement ;
  valeur invalide (nombre, booléen, date, sélection, URL d'image) = signalée, non écrite, non bloquante.
  `PlannedField.required` est hors JSON et hors checksum (plan de lecture V2a inchangé). API
  `GET /api/imports/{id}/preview` (phrases métier, aucun nom technique) ; portail `/portal/imports/[id]/preview`
  (Summary, Data to import, Issues, Not imported ; « Will be processed during import », jamais de promesse sur
  l'écriture). Benchmark : chaque nombre = comptage indépendant du CSV.
- **Couverture du catalogue cible (V2c.4)** : les CIBLES d'import ne viennent plus des clés d'affichage V2a
  (`READ_KEYS`) mais de `IMPORT_KEYS` (`uie_core.imports.matching`) = `READ_KEYS` + clés plates importables
  (`tags` en sorte `labels` = collection lue par libellés, `cost_price`, `can_be_sold`, `can_be_purchased`,
  `description_purchase`, `description_internal`, `manufacturer_reference`, `internal_reference`), passées à
  `build_read_plan(..., keys=)` / `CatalogSyncService.read_plan(id, keys)` : même choix du champ par clé, une clé
  sans vrai champ KNOWN / CONFIRMED n'est jamais une cible, affichage V2a inchangé (défaut). `main_image` = cible
  `media` acceptant une URL (texte). En-tête « Groupe / Champ » : dernier segment essayé aussi (compréhension
  seulement ; clé de mémoire = en-tête complet). Hors périmètre : variantes (Option Name / Value, regroupement
  par Handle, SKU multi-variantes, prix de variante, price_extra). Constat ouvert : le prompt 1.0.0 classe
  « aucune cible offerte » en NOT_RELEVANT (Option1 Name / Value « probably not needed ») ; correction générique
  proposée, non faite. Benchmarks (vérité terrain avant l'appel) : Shopify
  30 décisions → 5 actions, Image Src → main_image, Tags → tags (règles), MPN compris sans cible ; WooCommerce
  sans régression (Images devient une vraie cible).
- **Fallback LLM (V2c.3)** : EMPTY → NOT_RELEVANT appris → mapping appris → règles → LLM. Candidats = UNMATCHED
  + REVIEW LOW_CONFIDENCE ; contrat `uie_core.imports.proposer` (lot : en-tête, type, remplissage, ≤ 3 exemples
  masqués par `mask_example`, suggestion des règles ; cibles = celles de « Choose target ») → TARGET |
  NOT_RELEVANT | UNKNOWN validé dans le Core (cible hors de celles offertes → UNKNOWN). Fusion pure
  `apply_ai_proposals` : TARGET ≥ 0.95 (`AI_AUTO_THRESHOLD`, V2c.5b ; règles : 0.90), type compatible, cible
  libre → MATCHED `decided_by` AI ; 0.60-0.95 → REVIEW AI_SUGGESTION ; cible tenue → TARGET_TAKEN ; NOT_RELEVANT
  ≥ 0.90 (`AI_NOT_RELEVANT_THRESHOLD`) → REVIEW AI_NOT_RELEVANT (JAMAIS ignorée d'office) ; prompt 1.1.0 (V2c.5) :
  NOT_RELEVANT = donnée propre à la source (réglages, SEO, publicité), une vraie donnée produit sans cible =
  UNKNOWN ; sur une suggestion des règles, seule la même clé ≥ 0.95 la
  confirme. Le LLM n'écrit jamais la mémoire : Confirm / Choose target / Ignore / « Ignore all AI not relevant »
  (`ignore_ai_not_relevant`, confirmé) passent par V2c.2 / V2c.2b. Même fournisseur que V1c (`build_proposer`,
  `propose_columns` sur Null / Replay / Recording / OpenAI / Anthropic), prompt
  `catalog/llm/import_column_mapping.yaml` (`purpose: import_columns`, exemples inventés). Cache PAR COLONNE :
  empreinte `column_checksum` (prompt, fournisseur / modèle, contexte, cibles offertes, en-tête, type,
  remplissage, exemples masqués, suggestion des règles ; position exclue) stockée avec chaque réponse dans
  `catalog_imports.ai_result` (JSONB `none_as_null`, jamais les exemples) ; une colonne déjà analysée par la MÊME
  organisation (100 imports récents) reprend sa réponse, seules les autres partent au LLM ; échec jamais mis en
  cache (migration `4dc519d971e5`). API : `analysis.ai_matched / ai_suggested /
  ai_not_relevant`, colonne `source = ai`, `confidence_pct`, `probably_not_needed`, route
  `/ignore-ai-not-relevant` ; ni fournisseur, ni modèle, ni raison du LLM côté fournisseur.
  Benchmark WooCommerce (vérité terrain écrite
  avant l'appel, openai / gpt-5.6-sol) : 1er import 4 règles + 1 AI auto (Description) + 27 « probably not
  needed » → 2 actions humaines au lieu de 29 décisions ; 29 / 29 réponses justes ; 1 appel, 2 987 + 1 297
  tokens, 15 s ; 2e import READY sans manipulation et sans appel (cache par colonne).
- **Benchmark réel** (à ne pas « optimiser ») : export WooCommerce public
  (191 produits, 54 colonnes) sur customer17 :
  avant 3 Matched / 3 Review / 48 Unmatched ; après 4 / 2 / 27 + 21 Empty (27 Unmatched légitimes : champs de
  boutique web et réglages de thème). Vocabulaire 1.4.0 (alias `regular price` sur `list_price`, pack Odoo
  1.5.0) : 0 décision V1 modifiée sur les 4 snapshots de mesure (`temp/measure_v1_vocabulary.py`).

## Comptes et isolation multi-tenant (V1d) — concepts

Plan validé le 2026-09-28. Principe : **même Core, contexte explicite, DTO différents**.

- **Modèle** (migration `4f8732b8e3a3`, aucun secret, aucun compte) : `organizations` (supplier : name, slug
  UNIQUE, status), `users` (email normalisé UNIQUE, hash argon2id jamais exposé, `platform_role` NONE | ADMIN),
  `organization_memberships` (OWNER | MEMBER), `user_sessions` (sha256 du jeton seulement, expiration,
  révocation, organisation sélectionnée). `instances.organization_id` NOT NULL (FK RESTRICT) = SEULE racine
  d'ownership : snapshots, diffs, domaine, bindings en dérivent par `instance_id` (aucune dénormalisation).
  Nom d'instance unique PAR organisation. Organisation structurelle « GO45 Development »
  (`GO45_DEV_ORG_ID = 00000000-0000-4000-8000-000000000045`) : propriétaire des instances d'avant V1d.
- **Contextes** (`uie_core.identity`, sans framework web) : `TenantContext(user, organisation, rôle)` FILTRE ;
  `AdminContext(user)` voit tout ; `SystemContext(raison)` pour CLI, tests, worker. Paramètre OBLIGATOIRE du
  constructeur de chaque service (sans défaut → `TypeError`). Un seul point de filtrage des instances
  (`InstanceService._query`, `ProductDomainService.instance`) : l'instance d'un autre tenant lève le MÊME
  `NotFound` qu'un id inexistant, AVANT tout accès au SecretStore ou au réseau. Connaissance plateforme
  (Registry standard, packs, vocabulaire, prompts) : lecture pour tous, écriture Admin / System seulement
  (`PermissionDenied` → 403). `OrganizationService` (`uie_core.identity.organizations`, import direct : il
  dépend du Registry) : plateforme seulement, y compris en lecture.
- **Auth** (`/auth/login | logout | me | context`) : cookie `uie_session` HttpOnly, SameSite=Lax, Secure si
  `UIE_ENV=prod`, durée `UIE_SESSION_TTL_HOURS` (12 h). Échecs de login indiscernables (vérification argon2
  factice). Un seul membership → organisation sélectionnée automatiquement ; sinon `POST /auth/context`
  (membership vérifié). Membership, organisation ACTIVE et utilisateur ACTIVE revérifiés à CHAQUE requête.
- **Routes** : `/admin/*` → `require_admin` (AdminContext ; 401 sans session, 403 sans rôle ADMIN) ;
  `/api/*` → `require_tenant` (TenantContext). `uie_api.deps` construit chaque service pour les deux
  contextes (`Registry`, `Instances`, `ProductDomains`, `SemanticEnrichment` / `Tenant*`) : aucune route ne
  construit de contexte. Admin des suppliers : `/admin/organizations` (list / create / get, `/users` avec
  mot de passe initial ou utilisateur existant, `/instances` create, `/instances/{id}/attach`).
- **Supplier API** `/api/*` (`uie_api.supplier` = DTO PROPRES, jamais ceux de l'Admin) : systèmes (« Odoo 17 »,
  connexion, état du catalogue), test de connexion, analyse, résumé, questions, réponses, confirm / change /
  ignore / reopen, vocabulaire, Ask AI. Aucune donnée technique (snapshot, checksum, baseline, diff, origine,
  rule_id, versions, fournisseur ni modèle LLM, endpoint) : test sur toutes les réponses. Réservé à l'Admin :
  discovery, diff, snapshots, domaine technique, exclusions avec rule_id, bindings bruts, include.
- **Console** : la console appelle l'API par le proxy de même origine `/uie-api/*` (`next.config.ts`,
  `UIE_API_URL`, défaut `http://127.0.0.1:8000`) : le cookie suit, que la console soit ouverte sur localhost
  ou 127.0.0.1 (deux sites différents pour un cookie). `NEXT_PUBLIC_UIE_API_URL` (URL absolue) reste possible
  (CORS avec credentials). `AuthGate` : sans session → `/login?next=` ; console réservée aux admins, portail
  `/portal` aux membres (la sécurité reste côté API). Écrans : `/login`, `/admin/suppliers`,
  `/admin/suppliers/[id]` (Add user, Create / Attach instance), colonne Supplier dans `/instances`.
  **Portail** `/portal`, `/portal/systems`, `/portal/catalog` : « Coming soon » (choix d'organisation si
  plusieurs) ; les écrans supplier sont reportés, l'API `/api/*` est prête.
- **Démarrage** : `uv run uie db upgrade` puis `uv run uie admin bootstrap` (admin initial, idempotent ;
  `UIE_INITIAL_ADMIN_EMAIL` / `UIE_INITIAL_ADMIN_PASSWORD` ou saisie masquée, ≥ 12 caractères). Sans
  bootstrap, personne ne peut se connecter à la console.
- **Tests d'isolation** : Core (`test_tenant_context_core.py`), Supplier API (`test_supplier_api.py`), HTTP de
  bout en bout (`test_tenant_isolation_api.py`) : TOUTES les opérations du schéma OpenAPI balayées (/admin →
  403 tenant / 401 anonyme ; /api → 401 anonyme / 403 admin sans organisation), accès croisés A ↔ B sans
  lecture de secret ni connexion (espions), révocation en cours de session. Les tests API existants
  s'authentifient en admin par `dependency_overrides[require_admin]`.
- Non-régression customer17 après la migration : 147 / 272 / 0 / 0, 18 décisions, aucun appel LLM.

## Instances client (étapes 7-8) — concepts

- **Instance** = installation réelle rattachée à une interface standard (system/product/version/interface),
  avec environnement, connexion (`instance_connections`) et capabilities. Pas de rôle SOURCE/TARGET figé :
  le sens d'usage découle des capabilities.
- **Capabilities** (`instance_capabilities`) : `VERIFIED` = opération réellement exercée EN LECTURE SEULE
  (Odoo : search_read / search + search_count / read multi-ids / fields_get + ir.model) ; `DECLARED` =
  annoncée par le connecteur, jamais exercée (CREATE/UPDATE/DELETE/BULK_WRITE en V1 : aucune écriture sur
  une instance, `ReadOnlyObjectProxy` refuse toute méthode hors liste blanche) ; `UNAVAILABLE` ; `UNKNOWN`.
- **Secrets** : `SecretStore` (contrat) → `FernetSecretStore` (clé `UIE_SECRET_KEY`, `uie secret gen-key`).
  La base ne contient que `secret_ref` (`fernet:v1:<chiffré>`, remplaçable par `vault:...`) ; jamais le clair,
  ni dans l'API (`has_secret` seulement, erreurs 422 sans `input`), les logs, les snapshots ou les erreurs
  (`redact()`). Tests : recherche du mot de passe dans TOUTES les tables, les logs et les réponses.
- **Classification** (`Origin` : STANDARD / CUSTOM / THIRD_PARTY / UNKNOWN) produite par le provider ;
  le Core construit `InstanceDiffSummary` avec des règles génériques (custom / third-party = ADDED classés ;
  modules standard absents = seulement si démontré ; éléments manquants = REMOVED non expliqués ;
  risques de compatibilité = champs STANDARD modifiés).
- **Règles Odoo** (`OdooSchemaDiscoveryProvider.classify`) : CUSTOM = préfixe `x_`, module `__custom__` ou
  `state=manual` ; STANDARD = au moins un module dans `installed_modules` de la baseline ; THIRD_PARTY =
  aucun ; UNKNOWN = pas de module. Pour un élément AJOUTÉ : THIRD_PARTY si un de ses modules est hors
  baseline, ou s'il pointe vers une entité elle-même ajoutée par une extension (Odoo attribue les champs
  hérités d'un mixin étendu, ex. `rating_ids` de `rating`, au module du modèle hôte `account`, `stock`...).
  Module absent = présent dans `installed_modules` standard et absent de ceux de l'instance.
- **Suppression** (`InstanceService.delete`, `DELETE /admin/instances/{id}`, `uie instance delete`, bouton
  avec confirmation par saisie du nom) : une instruction DELETE, PostgreSQL cascade (FK ON DELETE CASCADE)
  vers connexion, capabilities, snapshots d'instance et diffs ; le snapshot standard (FK RESTRICT, sens
  instance → standard) n'est jamais supprimé ni modifié. Le secret est retiré du SecretStore APRÈS le commit
  (événement `after_commit`, oublié en cas de rollback). Règles FK vérifiées par un test.
- Tests hors ligne : `uie_connectors.odoo.testing.FakeOdooServer` sert un dump JSON en XML-RPC et
  enregistre chaque appel (preuve qu'aucune écriture n'est tentée).

## Domaine PARTNER — Odoo res.partner → Partner CDM → DATEV Debitoren/Kreditoren (2026-10-02)

PARTNER seulement : ni PRODUCT vers DATEV, ni
Buchungsstapel, ni OAuth / API DATEV, ni écriture dans Odoo, ni envoi automatique vers DATEV.

    Odoo ─(lecture seule)─► bindings PARTNER ─► Partner CDM 1.0 ─► binding cible (YAML + Registry v5) ─► EXTF

- **Partner CDM** (`uie_cdm.partner`, JSON Schema publié `packages/cdm/schemas/partner-1.0.0.json`, test de
  fraîcheur ; regénérer : `uv run python -m uie_cdm.partner`) : kind ORGANIZATION / PERSON / UNKNOWN, nom, nom de
  personne JAMAIS découpé (prénom / nom seulement si la source les sépare), rôles {CUSTOMER, SUPPLIER},
  identifiants (customer_number, supplier_number, partner_reference, company_registry), adresses, TVA, contact,
  banques (IBAN validé mod 97), langue ISO 639-1. Aucune notion Odoo ni DATEV ; `mapping_view(role)` = vue plate.
- **Domaine PARTNER** = même moteur que PRODUCT : `DomainKey.PARTNER`, `PartnerDomainService` ; définitions,
  domain snapshots et lectures filtrés par domaine (migration `df6c9ad04557` : unicité par domaine) ; packs
  `domain-packs/partner/` (vocabulaire 1.0.0, pack Odoo 1.0.0 : res.partner PRIMARY, res.partner.bank
  BANK_ACCOUNT, res.bank BANK, res.country COUNTRY ; règles P01-P10 génériques ; champs protégés). Découverte
  générique `<domaine>/<système>/*.yaml` (`targets/` et `llm/` ne sont pas des packs). Mesure : customer17 = une
  seule question (`x_customer_no`, proposée `customer_number` par le vocabulaire, JAMAIS codée), Odoo 17 / 18 / 19
  standard = 0 question ; PRODUCT : checksums des règles et du domaine résolu identiques.
- **Lecture** (`uie_core.partners`) : plan par rôle et semantic key (`PARTNER_KEYS`, même `choose_fields` que le
  catalogue) ; analyse PARTNER complète exigée (`PartnerNotReady` → 409) ; partenaires actifs SANS parent ; rôles
  par `customer_rank` / `supplier_rank` > 0 ; pays, langue, IBAN jamais devinés (omis + signalés) ; **aucune donnée
  de partenaire stockée** (lecture à la demande, plafond `UIE_PARTNER_READ_MAX`).
- **Mapping générique** (`uie_core.mapping`) : `TargetBinding` (données) résolu contre le Registry (position inconnue
  → refus) ; transformations fermées (`when`, `fallback`, `extract`, `map`, emplacements répétés avec `require`,
  `required_unless`, en-tête, paramètres du profil) ; validation par les MÉTADONNÉES du Registry (longueur, type,
  encodage, motif sur la forme sérialisée par l'écrivain). Identité / obligatoire invalide → enregistrement
  BLOQUÉ ; facultatif invalide → omis + AVERTISSEMENT ; jamais de troncature. Aucun nom de système ni de colonne.
- **Binding DATEV** `domain-packs/partner/targets/datev_debitoren_kreditoren.yaml` : un enregistrement par rôle,
  champs de nom du seul type d'adressat (2 / 4 / 6 ; personne : nom complet en 4, rien en 5), Adressattyp, USt-IdNr
  (9 + 10), adresse, contact, 5 banques avec IBAN, Kunden-/Lief.-Nr., Sprache, règle D02 (Land manquant →
  avertissement, jamais « DE » inventé). En-tête : constantes et métadonnées du snapshot, profil.
- **Écrivain EXTF** (`uie_connectors.datev.extf`, `@RecordFileWriterRegistry.register`) : Text entre guillemets
  sauf motif sans guillemets, nombres / dates sans, virgule décimale, TTMMJJJJ, `;`, CRLF, CP1252 (UTF-8 BOM en
  option) ; en-tête identique en forme à celui du fichier d'exemple officiel (test local).
- **Export** (`uie_core.exports`, migration `50e0889b3404`) : `export_profiles` (organisation × format, valeurs
  validées par le binding et les motifs d'en-tête ; plages de comptes saisies, longueur = Sachkontenlänge + 1,
  sans chevauchement, **aucune valeur par défaut**) ; `export_account_assignments` (numéro par profil × système ×
  référence source × rôle, unique par profil, jamais réutilisé : système supprimé → instance_id NULL, numéro gardé ;
  reprise du numéro de la source seulement si `reuse_partner_numbers` et conforme) ; `export_runs` (journal :
  compteurs, sha256, taille ; jamais le contenu). Aperçu = lecture seule (« New » = serait attribué) ; génération
  = lignes bloquées non écrites, numéros réservés pour les lignes écrites.
- **API** : `/api/systems/{id}/partners/analyze | analysis | questions | answers | fields/.../confirm | change |
  ignore | reopen`, `/api/partners/vocabulary` ; `/api/exports/targets`, `/{system}/{product}/profile` (GET / PUT,
  422 + `errors` par paramètre), `/preview?system_id=`, `POST /files` (fichier en pièce jointe), `/runs`. DTO
  `uie_api.supplier_exports` sans donnée technique.
- **Portail** : page d'un système → carte « Customers & suppliers » (mêmes cartes de question, `domain="partners"`)
  → « Continue to export » ; `/portal/exports` (1. Export settings, 2. Preview and download, Generated files).
- Tests réels (spec v5 locale) sautés si elle est absente : `test_partner_datev_mapping.py`, `test_datev_extf.py`,
  `test_partner_export_api.py` (bout en bout : 7 enregistrements, 4 prêts, 2 avertissements, 1 bloqué).

## Domaine ACCOUNTING — Odoo account.move → Accounting CDM → fichier DATEV Buchungsstapel (phases 1-5)

Décision D2 : modèle brut, BU-Schlüssel configuré explicitement, jamais déduit. Phase 4 =
préparation validée (aperçu) ; phase 5 = fichier EXTF Buchungsstapel téléchargé. Jamais d'envoi
à DATEV (API / OAuth), jamais d'écriture dans Odoo. D11 = idempotence par pièce. Travail DATEV
GELÉ après D11 (2026-10-06) : prochain format cible = Lexware Office (non commencé).

- **Instance de test accounting17** (customer17 n'est JAMAIS modifié) : service compose `odoo17-accounting`
  (profil `accounting`, port 8037, même serveur `odoo-db`), base créée par `docker/accounting17-setup.py`
  (refuse `--db customer17`) : `account` + `l10n_de`, plan `de_skr03`, EUR, 6 partenaires et 14 pièces inventées
  (factures, avoirs, achats, partenaire client ET fournisseur, brouillon, annulée, deux taxes sur une ligne,
  compte 8200, février) ; chaque pièce a une condition de paiement standard (xmlid `account_payment_term_*` :
  30 jours, B 15, D 45) : sans condition, Odoo 17 prend la date de CRÉATION pour échéance (phase 3.1, corrigé par
  le script, circuit brouillon → même état). Dump `docs/dumps/odoo17_accounting17.json` ; extrait de pièces
  pour les tests `packages/connectors/tests/fixtures/odoo17_accounting17_records.json`
  (`extract_accounting17.py`).
  Discovery : +38 champs THIRD_PARTY (l10n_din5008_*, `account.tax.l10n_de_datev_code`, audit trail) → 4
  questions ACCOUNTING sur accounting17 (0 sur Odoo standard).
- **Conditions de lecture génériques** (`uie_core.catalog.Condition`) : `op` `in` | `eq` (1 valeur) | `between`
  (2 bornes incluses, début ≤ fin comparées selon `data_type`) ; traduites par le connecteur (`=`, `>=` / `<=`).
- **Lecture** (`uie_core.accounting`) : plan par rôle (DOCUMENT, DOCUMENT_LINE, LEDGER_ACCOUNT, TAX, CURRENCY) et
  semantic key (`ACCOUNTING_KEYS`, même `choose_fields`) ; analyse complète exigée (`AccountingNotReady` → 409) ;
  `read(instance, company, period)` : société EXPLICITE (`eq`), mois de la date comptable (`between`), types
  parmi les clés du value_map ; statuts / types / sortes de ligne via les value_maps (hors liste = signalé) ;
  partenaire = identité source du Partner CDM (même instance) ; compte = code SOURCE ; constructions non prises
  en charge (plusieurs taxes, taxe non pourcentage ou TTC, déséquilibre...) GARDÉES et signalées ; `exportable` =
  comptabilisée sans anomalie. Rien n'est stocké ; plafond `UIE_ACCOUNTING_READ_MAX`.
- **API** : `/api/systems/{id}/accounting/analyze | analysis | questions | answers | fields/.../confirm | change |
  ignore | reopen`, `/api/accounting/vocabulary`, `GET .../accounting/companies`,
  `GET .../accounting/documents?company_id=&period=AAAA-MM` (DTO `uie_api.supplier_accounting`, sans référence
  interne ; `balance` = somme signée des lignes du CDM). **Portail** : page d'un système → carte « Accounting »
  (Experimental) : questions, puis Read accounting (société, mois, compteurs « Eligible » = comptabilisée et prise
  en charge, pas une validation DATEV ; pièces ; détail : dates, échéance, lignes Line / Account / Debit / Credit,
  solde). Débit / Crédit = VUE du montant signé (`apps/web/src/lib/accounting/presentation.mjs`, `node --test`),
  jamais un second calcul comptable côté React. Mise en page : `@container` (largeur de la CARTE, la page système
  est étroite), tableaux `table-fixed` qui passent à la ligne (un tableau en largeur auto s'élargissait sur le nom
  de partenaire le plus long et poussait Débit / Crédit hors de la carte) ; détail empilé sous `@xl`.
- **Écritures (phase 4, Core générique)** : `uie_core.accounting.bookings.gross_bookings` — modèle
  `gross_by_account_and_tax` (déclaré par le binding) : une écriture par (pièce, compte de contrepartie, taxe),
  montant TTC, sens du compte du tiers (`partner_side`) opposé au signe de la contrepartie ; lignes TAX / OPEN_ITEM
  = contrôles (taxe du groupe = lignes TAX, total des groupes = partie du tiers) ; incohérence, ligne à plusieurs
  taxes, ligne d'arrondi → aucune écriture prête (jamais corrigé ni arrondi). Provenance : positions des lignes.
- **Préparation cible** (`uie_core.accounting.target.AccountingTargetService`, générique) : binding résolu contre
  la VERSION choisie du Registry (binding : `versions: ["13"]`, l'en-tête officiel n'admet que 13) ; réglages
  partagés lus dans le profil d'un autre format (`inherit_profile` : Berater, Mandant, WJ-Beginn,
  Sachkontenlänge, Sachkontenrahmen, Herkunft du profil Debitoren/Kreditoren) + réglage propre `currency` (WKZ) ;
  compte du tiers = numéro attribué par l'export Debitoren/Kreditoren (`partner_account`, lecture seule ; absent →
  BLOQUÉE « export your customers & suppliers first ») ; correspondances (`lookups` ACCOUNT → Gegenkonto, TAX →
  BU-Schlüssel ou « No tax key ») : seules les décisions CONFIRMÉES sont utilisées, table
  `export_value_mappings` (migration `3f292744a370`, portée organisation × format × instance × sorte × référence
  source ; NULL = « aucune valeur ») ; suggestion déterministe `IDENTICAL_CODE` (même numéro si longueur =
  Sachkontenlänge et motif du Registry), jamais appliquée seule ; aucune suggestion de clé de taxe (D4).
  `l10n_de_datev_code` (Odoo) NON utilisé : codes 1 chiffre (« 3 », « 9 »…) refusés par le motif v13 `"\d{4}"`,
  texte libre non contrôlé, champ ignoré par le fournisseur en phase 3. Statuts : éligibilité SOURCE (phase 3)
  inchangée, statut CIBLE par pièce READY / WARNING / BLOCKED (une écriture bloquée bloque la pièce) ; brouillon /
  annulée = non préparées. Contrôles : devise = WKZ du dossier, date dans l'exercice (WJ-Beginn), date de pièce
  d'un autre mois → avertissement, Registry (motifs, longueurs, décimales : jamais d'arrondi silencieux).
  Moteur : `FieldMapping.date_format` (Belegdatum TTMM), `TargetRef.display` (nom du système dans les messages :
  le Core ne nomme aucun système, test `test_accounting_target_boundary.py`).
- **API phase 4** : `GET /api/accounting/targets`, `/api/systems/{id}/accounting/targets/{system}/{product}/settings`
  (GET / PUT), `/preparation?company_id=&period=&version=`, `PUT | DELETE …/mappings/{account|tax}/{ref}` (DTO
  `uie_api.supplier_accounting_target`). **Portail** : carte Accounting → Read accounting → « Prepare for DATEV »
  (format, version, réglages, correspondances Confirm / Enter / No tax key / Remove, pièces et écritures :
  Umsatz, Soll/Haben, Konto, Gegenkonto, BU-Schlüssel, Belegdatum, Belegfeld 1, Buchungstext, lignes d'origine,
  cellules telles qu'écrites). Présentation pure `apps/web/src/lib/accounting/preparation.mjs` (`node --test`).
- **Fichier (phase 5)** : `AccountingTargetService.file_preview | generate | runs`. `_serialize` ne consomme QUE la
  préparation de phase 4 (`PreparedBooking.values`, position → valeur validée) : aucune décision comptable
  (testé sur une préparation construite à la main). Statuts écrits : READY et WARNING (`EXPORTABLE`) ; BLOCKED,
  brouillon, annulée jamais écrits, raison conservée (`ExcludedDocument`). Contrôle final de CHAQUE valeur et de
  l'en-tête contre le Registry avec l'encodage du fichier : un refus → `ExportInvalid` (422), jamais de ligne
  omise ; rien d'exportable → `ExportInvalid`. En-tête = `header` du binding (1.1.0) : constantes, métadonnées du
  snapshot, réglages propres et HÉRITÉS (`header_values(..., params=)`), contexte de l'export (`context:
  period_start | period_end` → 15 / 16 Datum von / bis, `template` → 17 Bezeichnung « Buchungen MM/AAAA » : le
  motif officiel n'admet pas le tiret). Réglages de fichier : `lock_bookings` (21 Festschreibung, choix explicite
  sans défaut, `required_for_file` : exigé pour le fichier, pas pour l'aperçu de phase 4) et `encoding`.
  Aperçu = téléchargement : `content_digest` = empreinte du contenu rendu SANS valeur générée (`now=None`) ; seul
  « Erzeugt am » (006) et le nom du fichier sont volatils ; `generate(expected_digest=)` refuse si le contenu a
  changé (`ExportChanged`, 409). Aperçu : rien n'est enregistré. Génération : `export_runs` (+ `period`,
  `details` JSONB : société, version, compteurs, pièce → rangs de ses lignes ; migration `7a1c4e9b2d55`), jamais
  de montant ni de contenu ; `previous_exports` avertit qu'une période a déjà été exportée (DATEV ne dédoublonne
  pas). Nom : `DATEV_buchungsstapel_<société>_<AAAA-MM>_<horodatage>.csv`. API : `GET …/file-preview`,
  `POST | GET …/files`. Portail : section « DATEV Buchungsstapel export » (Preview DATEV file, Download DATEV file,
  fichiers générés) ; choix à deux valeurs = boutons radio (pas de liste déroulante).
- **Idempotence d'export par pièce (D11, générique)** : `uie_core.exports.documents` — `DocumentExportLedger`,
  aucun nom de système ni de format (test de frontière : ni datev, ni lexware, ni odoo…). Identité =
  `DocumentScope(organisation, instance source, domaine, système cible, format cible)` + référence source OPAQUE
  (jamais le numéro lisible). Empreinte = `DocumentContent.units` fourni par l'appelant : pour ACCOUNTING, chaque
  écriture préparée, position → texte EXACT écrit (`_content`) ; rien de volatil (horodatage, nom de fichier, rang,
  run). États `DocumentExportState` : NEW (écrite), ALREADY_EXPORTED (jamais réécrite par défaut),
  CHANGED_SINCE_EXPORT (bloquée) — axe DISTINCT du statut cible READY / WARNING. Tables (migration
  `139e224e09c5`) : `export_document_records` (IMMUABLE, listener `before_update` : une ligne par pièce écrite
  et par génération ; `origin` EXPORTED | RELEASED | RE_EXPORTED | ADOPTED ; `field_digests` = empreinte de
  chaque valeur pour dire QUELS champs ont changé, jamais les valeurs) et `export_document_releases` (décision
  humaine : autorise le nouvel export d'une pièce modifiée pour SON empreinte actuelle ; un nouveau changement
  rebloque). Classification AVANT la sélection (`_file` → `ledger.classify` → `_serialize(classes=)`) ; aperçu
  sans effet ; `generate` écrit le journal ET les lignes de pièces dans la même transaction ; rien d'écrit si la
  génération échoue ; plus rien de nouveau → `ExportInvalid` « No new document to export ». Doublon volontaire :
  `include_exported` + `confirm_duplicates` (jamais par défaut, audité dans `details`). Contenu des fichiers non
  conservé (pas de re-téléchargement d'un ancien fichier). Fichiers d'AVANT le suivi (phase 5) : jamais
  d'empreinte fabriquée ; pièces signalées (`legacy_*`), adoption explicite `adopt_legacy_run` acceptée
  seulement si `content_digest` du fichier = contenu actuel. API : `file-preview?include_exported=`,
  `POST …/files` (`include_exported`, `confirm_duplicates`), `POST | DELETE …/documents/{ref}/release`,
  `POST …/files/{id}/adopt`. Réutilisable tel quel par un autre format cible (autre `DocumentScope`, unités
  fournies par sa préparation).

## Lexware Office — phase 0 : étude (2026-10-06), RIEN n'est implémenté

Sources officielles lues : `https://developers.lexware.io/docs/`,
journal des changements au 11.08.2026 (copies hors git). Terminologie : « Lexware
Office », code de système `lexware` (plus jamais « lexoffice »). DATEV reste GELÉ.

- **Pas une API comptable au sens DATEV** : ni écriture de journal, ni compte, ni Soll / Haben. La comptabilité
  se nourrit par des PIÈCES (`vouchers` : salesinvoice, salescreditnote, purchaseinvoice, purchasecreditnote)
  regroupées par **catégorie** (`posting-categories`, propres à l'organisation) **et taux de TVA** ; numéro de
  pièce libre. L'Accounting CDM suffit (même regroupement que `gross_bookings`) ; compte → catégorie = décision
  explicite. Jamais de facture Odoo recréée en `invoices` : Lexware attribue son propre numéro.
- **API** : `https://api.lexware.io/v1/…`, clé d'API privée (`Authorization: Bearer`, 24 mois, permissions
  limitables, OAuth non nécessaire) ; 2 requêtes / seconde pour toute l'API (429, parfois 500) ; EUR seulement ;
  validation = **406** ; verrouillage optimiste par `version` (409) ; AUCUN `POST` idempotent, aucune référence
  externe ; un 504 peut cacher une création réussie ; pas de suppression de contact ni de pièce ; statuts non
  modifiables par l'API ; sondage interdit (webhooks, signature `X-Lxo-Signature`). Test de connexion =
  `GET /v1/profile` (le « ping » n'est pas dans la référence). Pas d'environnement de test : compte de test de
  30 jours.
- **Aucune spécification lisible par machine** (pas d'OpenAPI ; la collection Postman n'est pas un schéma) →
  spec maintenue `uie-lexware-api/1` + `SchemaLoader` dans `uie_connectors/lexware/` (à faire).
- **Registry prévu** : `lexware / public-api / v1 / rest [api] @1.0.x` — `v1` = version de ROUTE, la révision du
  contrat = `snapshot_version`, date du journal des changements dans `source`. Capacités par entité
  (READ / CREATE / UPDATE / DELETE) ; FINALIZE / DOWNLOAD / UPLOAD et cycle de vie dans les métadonnées de
  l'entité. Aucun changement du Core.
- **D11** : classement réutilisable tel quel (`target_system = "lexware"`) ; l'enregistrement d'une écriture par
  API demande des ajouts GÉNÉRIQUES (lien source ↔ identifiant cible + version, éléments d'exécution commités
  PENDING avant l'appel, journal sans fichier, état « issue inconnue »). Succès partiel : une ligne D11 par
  ressource réellement créée, jamais « lot exporté ».
- **Recommandation** : phase 1 = PARTNER → `contacts` (1a lecture seule + aperçu, 1b écriture sur compte de
  test), puis comptabilité → `vouchers`. Limites des contacts : numéros client / fournisseur attribués par
  Lexware, aucun compte bancaire, une seule adresse / e-mail / téléphone par type en écriture.
- **Ouvert** (décisions utilisateur) : licence (spec dans git, logo Lexware, usage multi-clients), compte de
  test. Use cases `partner.export.lexware`, `accounting.export.lexware`, `product.export.lexware` =
  `coming_soon`.

## Lexware Office — phase 1a : cible générique PARTNER → contact, lecture seule et aperçu (2026-10-06)

**Principe d'universalité (règle dure)** : UIE n'a AUCUNE source
privilégiée ; on ne construit jamais « Odoo → X », mais « n'importe quelle source → CDM → cible X ». La cible
Lexware ne dépend d'aucune source : tests d'architecture (`test_partner_lexware_preview.py`) interdisant tout
import d'un autre connecteur, du CDM ou d'un domaine dans `uie_connectors/lexware/`, et les mots d'une source
(`odoo`, `res.partner`, `xmlrpc`…) dans ce paquet, le binding et le contrat. Aucune migration, aucune écriture.

- **Formulaire de connexion déclaré par le connecteur** (`uie_core.instances.discovery`) : `ConnectionField`
  (rôle ENDPOINT | CONFIG | SECRET, `key`), `SchemaDiscoveryProvider.connection_fields`, `fixed_endpoint` /
  `endpoint()`, `connection_input(values)` (champ obligatoire vide OU champ non déclaré → `ConnectionFieldsError`),
  `connection_summary(result)` (faits sûrs). `InstanceService.connectors()`, `check_connection_values`,
  `connect_values`, `connection_facts`. API : `GET /api/connectors` → `fields` ; `POST /api/systems[/check-connection]`
  = `{system, fields, [name]}` (toutes les valeurs en `SecretStr`). Portail : `connect/wizard-steps.tsx` parcourt
  `connector.fields` ; logique pure `lib/connect/form.mjs` ; un test échoue si la page compare un code de système
  ou écrit un champ de connexion. **Jamais de `if système == …` dans une page.**
- **Rôles d'un système calculés, jamais figés** : `SupplierSystemOut.provides` (domain packs :
  `DomainPackRegistry.domains_for`) et `receives` (bindings `delivery: api`) ; la page d'un système affiche ses
  cartes d'après eux. `server_url` n'est rendu que si le connecteur a un champ ENDPOINT.
- **Connecteur** `uie_connectors/lexware/` : `client.py` (adresse fixe `https://api.lexware.io`, Bearer, 30 s,
  `ALLOWED_METHODS = {"GET"}` en 1a → `ReadOnlyViolation` avant tout appel ; `UIE_LEXWARE_API_URL` = DEV / tests
  seulement), `ratelimit.py` (2 requêtes / s, seau de capacité 1, par empreinte de clé, en mémoire du processus),
  429 : `Retry-After` plafonné à 30 s sinon 1 / 2 / 4 s, 3 essais ; `provider.py` (champ `api_key`, test =
  `GET /v1/profile`, version détectée `v1`, société + utilisateur conservés, jamais l'e-mail) ; `testing.py`
  (`FakeLexwareServer`, données inventées). Erreurs génériques de TOUT connecteur d'API : `uie_connectors/errors.py`
  (Authentication / Authorization / Validation / NotFound / Conflict / RateLimit / RemoteServer / Network /
  RemoteTimeout) : statut, trace, NOMS des champs ; jamais le corps brut.
- **Registry** : `lexware / public-api / v1 / rest [api]` ; `v1` = version de ROUTE, révision du contrat =
  `snapshot_version` (+ `contract_revision` dans `source`). Contrat MINIMAL commité
  `lexware/contract/public_api_v1.yaml` (`uie-lexware-api/1` : profile, contacts, countries ; noms, types,
  longueurs, valeurs admises, URL de la page officielle ; AUCUN texte de la documentation, le modèle refuse toute
  clé libre). Champ = chemin JSON (`technical_name` sans indice, `metadata.json_path`), métadonnées `presence`,
  `protocol`, `required_group`, `enum`, `length`, `position`. `uv run uie registry import lexware`. Aucun logo
  Lexware (licence) : `SystemLogo` n'affiche un logo que s'il est listé, sinon une tuile neutre avec le nom.
- **Moteur de mapping (ajouts génériques, inactifs pour DATEV)** : `TargetBinding.delivery` (`file` | `api` ;
  `TargetBindingRegistry.for_model(modèle, delivery="file")`), `omitted` (donnée écartée ET signalée),
  `TargetRule.when`, contrôle `enum` et obligation conditionnelle `required_group` lus dans le Registry,
  `ValueFormat` / `PlainValueFormat`, `RecordRequestBuilder` (JSON imbriqué par `json_path`). Vue du Partner CDM :
  `roles.CUSTOMER` / `roles.SUPPLIER`.
- **Binding** `domain-packs/partner/targets/lexware_contacts.yaml` (`records: single`, `delivery: api`) : un
  contact par partenaire avec ses rôles ; société → `company`, personne → `person` ; **nom de famille jamais
  déduit du nom complet (BLOQUÉ)** ; pays obligatoire dès qu'une adresse part, jamais inventé ; e-mail et
  téléphone → `business`, mobile, fax ; numéros client / fournisseur jamais envoyés (attribués par la cible) ;
  comptes bancaires, site web écartés avec avertissement.
- **Aperçu** : `uie_core.partners.target` — `prepare_partners` (PURE : Partner CDM + binding résolu → requête,
  statut, contenu D11) et `PartnerTargetService` (`targets`, `preview` : n'appelle jamais la cible, ne déchiffre
  pas la clé, n'écrit rien). D11 réutilisé tel quel (`domain="partner"`). API
  `GET /api/systems/{id}/partners/targets[/{target}/preview]` ; page
  `/portal/systems/[systemId]/partners/[targetId]` ; use case `partner.lexware.prepare` (experimental).
- **Limites connues** : aucune validation sur la vraie API (pas de compte de test) ; avec une source Odoo toutes
  les PERSONNES sont bloquées (un seul champ de nom) ; `DocumentScope` n'a pas l'instance cible et
  `export_document_records` exige une génération de fichier → à ajuster en 1b avec les tables d'envoi
  (`target_resource_links`, `target_send_runs`, `target_send_items`) ; lecture des partenaires liée à l'analyse
  sémantique (à assouplir pour une source à schéma fixe) ; console admin et CLI `instance add` encore de forme
  Odoo. Sur cette machine, `localhost` peut passer par IPv6 dont le relais Docker bloque 30 s : préférer
  `127.0.0.1` (ex. `UIE_TEST_DATABASE_URL`).

## Lexware Office — articles (domaine PRODUCT) : aperçu 1a (2026-10-16) et envoi 1b (2026-10-19)

- **1a** : Product CDM 1.0.0 (`uie_cdm.product`), lecture générique `uie_core.products.ProductReadService` (plan de
  lecture du catalogue), binding `domain-packs/catalog/targets/lexware_articles.yaml` (`delivery: api`, EUR, un
  article par unité vendable, n° = SKU obligatoire), décisions explicites `PRICE_BASIS` / `TAX_RATE`
  (`export_value_mappings`), aperçu `ProductTargetService.preview` (n'appelle jamais la cible).
- **1b — envoi** : MÊME mécanisme générique que les contacts (`DeliveryService`, `RecordSender`) ; aucune nouvelle
  table. `ProductTargetService` : `send` (confirmation explicite + `request_key`), `resume`, `runs`, `run`,
  `matches`, `link`, `unlink`, `resolve_unknown` ; l'aperçu et l'envoi partagent la même préparation (`_prepare`).
  `LexwareRecordSender` : `articles` dans `VERSIONED_CREATE` (POST `version: 0`), doublon par `articleNumber`
  (`NUMBERED`, filtre de liste, égalité exacte → NEEDS_MATCH, jamais de fusion), PUT = relecture + version + rien
  d'effacé, prix non directeur relu retiré (`DERIVED_PRICE`), dates en lecture seule. Issue inconnue (504, 5xx,
  réseau) = UNKNOWN, jamais retentée ; D11 par instance cible (déjà envoyé inchangé = SKIPPED UNCHANGED).
- **API** `/api/systems/{id}/products/targets/{target}/sends[/{run}[/resume | /items/{item}/resolve]]`, `/matches`,
  `/links` (DTO d'envoi partagés avec les partenaires) ; `deps.get_record_senders` surchargeable dans les tests.
- **Portail** : page produits → sélection, « Send selected », confirmation explicite, résultats, envois précédents ;
  composants d'envoi PARTAGÉS par les domaines `components/target-send.tsx` (hooks injectés). Use case
  `product.export.lexware` experimental. **Aucun envoi réel sans accord explicite de l'utilisateur** (un article à
  la fois ; produit de test : accounting17 `UIE-LEX-TEST-001`, EUR, NET, 19 %).

## Overview / catalogue des use cases (2026-10-03)

- **Source de vérité unique** : `apps/web/src/lib/use-cases/catalog.mjs` (JavaScript + JSDoc, `// @ts-check` :
  `pnpm typecheck` refuse une audience, un statut ou un domaine invalides). Une définition = id, domaine,
  audiences (`admin`, `supplier`, les deux), statut (`available`, `coming_soon`, `experimental`, `deprecated`),
  section, titre, résumé, objectif, flux, systèmes (`systems: { source, target }` → logos de la carte, flèche si
  cible ; codes `SYSTEM_NAMES` : odoo, datev, file, go45 = use case interne à la plateforme ; obligatoire),
  démo (instance, durée : métadonnées seulement), étapes, résultat
  attendu, prérequis, point de départ, pages couvertes, référence technique.
- **Règles de présentation** (fonctions pures du même fichier) : `listUseCases(audience)` (dépréciés masqués, tri par
  section puis ordre), `listOtherUseCases`, `canStart` (disponible ou expérimental ET point de départ),
  `validateCatalog`. Couche de présentation / navigation : aucune logique métier, aucune intégration exécutée.
- **Pages** : `/overview` (admin, accueil de la console ; section « In the supplier portal » issue du même
  catalogue, non démarrable depuis la console) ; `/portal` (fournisseur, « What would you like to do? »). Composant
  `components/use-case-overview.tsx` (cartes, flux, « How to demo »). Style des cartes (référence
  `captures/cards.PNG`) : sans barre en haut ni icône,
  cartes numérotées, sans le flux des étapes (`flow` reste une donnée du catalogue), action en lien
  discret « ▷ … » (pas un bouton). Aucune page n'a de lien de retour en haut (« ← Back to … », décision du
  2026-10-19 : la navigation du header suffit) ; seuls restent les boutons « Back » d'un parcours (wizard, étapes).
- **Audiences = réalité du code** : PARTNER (analyse, export DATEV) et les imports n'existent que dans le portail →
  `supplier` ; Registry, Compare, instances, analyse technique, fournisseurs → `admin`. ACCOUNTING : `accounting.read`
  `accounting.datev.prepare` et `accounting.export.datev` (fichier, phase 5) `experimental` ;
  `accounting.datev.submit` (envoi direct à DATEV) `coming_soon` (passer à `available` avec un point de départ
  suffit à le rendre démarrable, test d'acceptation).
- **Garde-fous** : `catalog.test.mjs` (`pnpm test`, lancé aussi par `tests/test_web_use_cases.py`) : ids uniques,
  enums valides, `available` ⇒ point de départ existant et navigable, `coming_soon` / `deprecated` non démarrables,
  use case fournisseur dans le portail et admin hors du portail, filtrage admin / fournisseur / les deux, et
  **couverture** : chaque page de `src/app` est citée par un use case ou listée dans `UNTRACKED_ROUTES`.
- **Capabilities** (`/portal/capabilities`, onglet du header du portail, 2026-10-19) : tableau lecture seule
  Source | Target | Use case | Status, filtres Source et Target ; une ligne = un use case pour UNE direction ; les deux
  directions de chaque paire de systèmes des flux (Odoo, Lexware Office, DATEV), une direction sans use case = une
  ligne « Not implemented » (aucun use case inventé). Généré par `lib/use-cases/capabilities.mjs` depuis le catalogue :
  ✓ Available, ✓ Experimental (in test), — Not implemented (coming_soon, deprecated, aucun use case). Ni « n/a », ni
  statistiques, ni matrice ; aucun statut ni système en dur dans la page (test) ; fichiers et plateforme hors tableau.
  Titre de ce tableau : « System integrations ». En dessous, « System capabilities » (System | Role | Capability |
  Status, filtres System et Role, `systemCapabilityRows`) : use cases SANS couple source → cible (connexion,
  analyse, consultation, import d'un fichier, lecture), rôle = côté où le catalogue place le système ; plus les
  systèmes cibles servis par des FORMATS du Registry (`GET /api/exports/targets`, `/api/accounting/targets`, version
  publiée seulement : DATEV) : use cases qui les visent dans le même domaine, rôle Target, UNE ligne par use case
  (jamais par source), formats affichés. Sans format au Registry, aucune ligne ajoutée.
- Registry : le niveau « produit » de la hiérarchie est affiché « Format » dans `/systems` (masqué quand il est
  identique au système) ; présentation seulement, persistance inchangée.

## AI Migration Wizard (POC, 2026-10-19)

Page `/portal/wizard` (onglet « AI Wizard » du portail, use case
`migration.wizard` experimental). Une question à la fois (radios, Other libre, Back / Continue, progression,
réponses gardées en `sessionStorage`), puis structure cible + Excel d'exemple. Aucun envoi vers un système.

- **Core** `uie_core.wizard` (sans état : le client renvoie ses réponses) : `MigrationWizardService(session, ctx,
  proposer)` — `next(answers)` → question | fin, `propose(answers)` → `TargetProposal`, `verify` (rejoué pour
  l'Excel : aucun « vérifié » ne vient du client), `render_excel` (xlsxwriter : feuilles Data, Fields, Read me).
  Registry d'abord : topic `target_system` → systèmes du Registry en tête (source `registry`), doublons de l'IA
  retirés ; topic `version` d'un système du Registry → ses versions (valeur `registry:<système>:<produit>:<v>`) ;
  structure confrontée au snapshot (entité + champs : type, obligatoire, libellé du Registry) → `verified` /
  `partial` / `ai` + avertissement « not verified against an official template ». Réponse du LLM validée
  (`question_from_raw`, `proposal_from_raw`, longueurs bornées) ; seule une réponse VALIDE est mise en cache
  (cache du processus, 256 entrées, clé = réponses + faits du Registry + identité du proposer).
- **Contrat** `MigrationWizardProposer` (`wizard_question`, `wizard_target` → `WizardRaw`) ; implémenté par
  `OpenAIProposer` (même transport `_complete`, json_schema strict ; prompt `uie_ai/wizard_prompt.py`
  1.1.0 : modifier = incrémenter `WIZARD_PROMPT_VERSION`). Même configuration que V1c (`UIE_LLM_PROVIDER`,
  `OPENAI_MODEL`) ; sans proposer compatible → 409 « not configured ». Données envoyées : réponses + noms /
  versions du Registry, jamais de clé ni de donnée d'un système connecté.
- **API** `POST /api/wizard/next | proposal | excel` (DTO `uie_api.supplier_wizard`, `require_tenant`) ;
  erreurs : `WizardUnavailable` → 409, `WizardError` → 502. Usage (tokens, coût si `UIE_LLM_PRICE_*`)
  journalisé par le logger `uie.wizard`, jamais exposé au fournisseur.
- **Web** : logique pure `lib/wizard/flow.mjs` (`node --test`) ; aucune question, aucun choix ni système en dur
  dans la page (test). Scénario de référence réel : `temp/wizard_sap_scenario.py`.
- **Catalogue des systèmes (2026-10-19)** : systèmes CONNUS = lignes du Registry existant (`systems.status =
  known`, ni produit, ni version, ni snapshot), écrites par `RegistryService.register_known_system` (idempotent ;
  un système avec schéma n'est jamais modifié ; un premier snapshot publié le fait passer en `supported`) depuis
  `uie_connectors/known_systems.yaml` (16 systèmes : sevdesk, weclapp, SAP Business One, Lexware Warenwirtschaft,
  JTL-Wawi, Billbee, Xentral, easybill, Haufe X360, Dynamics 365 Finance, SAP S/4HANA, Business Central, NetSuite,
  QuickBooks Online, Shopify, Xero) : `uv run uie registry known-systems`. Première question = systèmes du Registry
  SANS appel au LLM (schéma publié d'abord, valeur `registry:<code>`, puis connus `known:<code>`), « Other system »
  libre. Trois statuts jamais confondus, RECALCULÉS côté serveur : Verified schema (schéma publié), Known system
  (identifié, aucun schéma supposé), AI proposal (LLM) ; + Your answer (saisie libre). Un système connu n'a ni
  versions ni structure vérifiées (versions et champs : propositions de l'IA). Prompt 1.3.0 ; la liste des
  systèmes n'est plus envoyée au LLM une fois la cible choisie. Page : « Search system » + affichage progressif
  (9 choix, « Show all ») au-delà de 8 choix, sections Verified schema → Known systems → AI proposals.
- **Simple / Expert (2026-10-20)** : bascule sur la page, mêmes réponses (`mode` envoyé avec chaque question :
  `simple` = langage courant, aucun nom technique, 3 à 5 questions visées ; prompt 1.4.0). Simple : résumé des
  choix (sans champ technique ni détail du Registry), puis fichier du client → correspondances → fichier préparé ;
  Expert : structure complète (configuration, champs, types, statuts) + la même section fichier (noms techniques
  affichés). Fichier (`uie_core.wizard.mapping`, SANS stockage : renvoyé à chaque étape, lecteur `read_table` des
  imports) : nom exact (en-tête = nom ou libellé d'un champ, normalisé) → certain ; autres colonnes non vides →
  LLM par le contrat des imports (`wizard_columns` → `propose_columns`, prompt générique WIZARD_COLUMNS_PROMPT,
  exemples MASQUÉS `mask_example`, clés limitées aux champs de la structure) : ≥ 0,90 AI match, 0,60-0,90 à
  confirmer (l'utilisateur confirme ou change), NOT_RELEVANT ≥ 0,90 « probably not needed » (jamais ignorée
  d'office) ; cache du processus par lot. Fichier préparé (xlsx : Data, Mapping, Read me) = colonnes de la
  structure, valeurs COPIÉES sans conversion, champs obligatoires sans colonne signalés ; jamais « ready to
  import ». API `POST /api/wizard/file/mapping | preview | prepared` (multipart : `file` + `payload` JSON :
  answers, proposal, decisions) ; `MappingError` → 422, fichier illisible → 400. Vérification réelle :
  `temp/wizard_simple_check.py`.
- **Complete missing information (2026-10-20)** : après l'aperçu, une question par champ obligatoire encore vide
  (`uie_core.wizard.missing`, route `POST /api/wizard/file/missing`) : 1. valeurs admises du Registry
  (sélection d'un schéma publié, `TargetField.allowed`) → choix VÉRIFIÉS ; 2. booléen → Yes / No ; 3. référence
  (`TargetField.reference` : champ référence du Registry, ou colonne « champ/attribut » dont le champ de base
  existe) → saisie libre SEULEMENT, aucun identifiant proposé ; 4. autres → formulation et suggestions du LLM
  (`wizard_missing`, un appel pour ces champs, suggestions filtrées par type / longueur, jamais « vérifiées »),
  sinon question par défaut. Valeur commune ou par ligne (≤ 50 lignes). Validation (`resolve_fills`) : type,
  longueur, valeurs admises ; valeur non vérifiable = `confirmed` exigé (sinon 422) + avertissement conservé
  (aperçu, « Read me »). Les valeurs complétées ne remplissent que les cellules vides. « Obligatoire à
  compléter » = le schéma du Registry ET la structure d'import (un obligatoire du schéma a souvent une valeur par
  défaut : signalé en note, pas demandé) ; prompt 1.5.0 (« required » = refusé sans, ou valeur par défaut
  trompeuse : type, unité, devise). Test réel : `temp/wizard_missing_check.py` (Odoo 18, 5 produits).
- **Guided Resolution (2026-10-20)** : un appel `wizard_missing` (prompt 2.0.0) reçoit les réponses DÉJÀ données
  et, par champ, sa sorte et ses valeurs admises : formulation simple (aucun nom technique en Simple), aide, valeur
  DÉDUITE des réponses (acceptée seulement parmi les valeurs admises de la version ; jamais pour une référence),
  explication (réponse métier → valeur technique), réponses métier d'une référence (`hints`, jamais un
  identifiant). Champs facultatifs à valeurs connues proposés seulement s'ils se déduisent (`optional`).
  Références : `TargetField.relation` (entité liée, Registry) → `ReferenceFinder` (`uie_core.wizard.lookup`) lit
  les valeurs RÉELLES d'un système connecté de l'organisation (même système et version, dernière connexion OK)
  par le contrat `ReferenceLookup` (Odoo : `uie_connectors/odoo/reference.py`, lecture seule, identifiants
  externes lus dans `ir.model.data`) ; ces valeurs sont vérifiées (aucune confirmation). Sinon : réponse métier
  (`Fill.hint`) ou « I don't know » (`Fill.unknown`) → rien d'écrit, avertissement conservé. Prompt de structure
  1.6.0 (référence avec valeur par défaut raisonnable = facultative). Test réel : `temp/wizard_guided_check.py`.

## Parcours Steuerberater (« Prepare data for DATEV ») — feuille de route

Détail et décisions : **`docs/ROADMAP_STEUERBERATER.md`** (à lire avant tout nouveau domaine DATEV). En bref :
- Démo 1 validée (Debitoren/Kreditoren 5, Buchungsstapel 13 depuis Excel, `/portal/prepare-datev`) ; étiquettes
  `demo-1-base` (état initial, jamais déplacée) et `demo-1`.
- Un module par public = une CONFIGURATION (`lib/wizard/entries.mjs`, catalogue des use cases), jamais une copie
  des composants du mode Simple.
- Domaine 2 (TVA dans les écritures, 2026-10-26) : nature « Leistungsart » (question si absente, jamais
  devinée), zone lue dans les listes de partenaires exportées (`eu_members`, état `non_eu`), comptes dans la
  table de données `domain-packs/accounting/datev/skr03_vat_accounts.yaml` (à valider par le Steuerberater),
  refus avec motif (0 % national, TVA étrangère, UE sans USt-IdNr, importation) ; détail dans la feuille de route.
- Domaine 3 (livre de caisse, 2026-10-27) : type « Cash book » (colonnes de caisse, sans IA), écritures SANS
  partenaire (Accounting CDM 1.3.0 `CASH_RECEIPT` / `CASH_PAYMENT`), Konto = compte propre déclaré par le binding
  (`own_account`, réglage `cash_account`), comptes dans `domain-packs/accounting/datev/skr03_cash_accounts.yaml`
  (à valider par le Steuerberater), contrôles du solde (recalcul, négatif en tête avec décision), carte, ordre ;
  un montant reconnu seulement par l'IA est « à confirmer ».
- Ordre : factures d'achat → TVA dans les écritures (§13b, UE, hors UE) → caisse ; point d'étape ; puis banque
  (CAMT, MT940), paie (rien avant la documentation officielle, protection des données des salariés d'abord).
- Hors périmètre : déclarations fiscales (bilan, E-Bilanz, KSt, GewSt).
- Méthode : fichier réaliste + vérité terrain jamais retouchée ; mesurer puis corriger (un commit par point,
  avec test) ; chemin fichier → tableau de source → CDM → binding DATEV → EXTF → journal ; Simple = rien de ce
  qui se lit ou se déduit, ≤ 3 questions présélectionnées, aucune correction silencieuse, aucun numéro de
  conseiller ou de mandant inventé ; test manuel de l'utilisateur et non-régression par groupes à chaque domaine.

## DATEV D1 — Registry des formats EXTF (2026-10-02)

Registry seulement : ni OAuth, ni sandbox,
ni Mandant, ni domaine PARTNER, ni export, ni connecteur d'exécution ; aucune migration.

    datev ─┬─ buchungsstapel (Buchungsstapel, catégorie 21)       ─ 10 │ 11 │ 12 │ 13 ─ extf [file] @1.0.0
           └─ debitoren-kreditoren (Debitoren/Kreditoren, cat. 16) ─ 5                ─ extf [file] @1.0.0

- **Produit = format, version = version de format**, interface `extf` (type `file`, en-tête EXTF 700).
  Entités : `header` (31 champs de la ligne d'en-tête) et l'entité du format (`buchungsstapel`,
  `debitoren_kreditoren`). Champ : `technical_name` = numéro DATEV sur 3 chiffres (`125`, les formats n'ajoutent
  qu'en fin : un renommage = CHANGED), `label` = libellé de la définition XML (canonique pour toutes les
  versions), `native_type` = type DATEV (Betrag → decimal, Zahl → integer / decimal, Datum → date, Text et Konto →
  string), `required` = Necessary ; métadonnées `position`, `length`, `decimals`, `pattern` (portail, version
  courante seulement), `importable`, `exportable`, `source_field_id`, `aliases` (libellés du portail, des fichiers
  d'exemple, LabelAlias). Capabilities : BULK_WRITE seulement.
- **Sources officielles, LOCALES uniquement** (licence de redistribution non établie) : contenu public du DATEV
  Developer Portal (`https://developer.datev.de/mediator/strapi/file-formats/slug/datev-format`), programme de
  contrôle DATEV (définitions `Format_*.xml`), fichiers d'exemple. **Jamais dans git** : ni XML, ni exemples, ni
  specs complètes générées (`var/datev/specs/`, `temp/` ignorés). Le dépôt ne contient que le modèle de spec
  (`uie-datev-format/1`, `datev/spec.py`), le générateur, le loader et des fixtures INVENTÉES
  (`packages/connectors/tests/fixtures/datev/`).
- **Générateur** (`uie_connectors.datev.spec_builder`) : définition XML choisie par son CONTENU (nom exact +
  version ; absente ou en double → refus), position = OrdinalNumber + 1, en-tête et motifs depuis les tableaux
  allemands du portail (HTML parfois mal formé : cellules découpées sur les balises ouvrantes), alias des
  fichiers d'exemple (UTF-8 avec BOM, sinon cp1252), provenance (fichiers, SHA-256, date du portail), rapport de
  contrôles croisés (nombres XML / portail / exemple, libellés, append-only, ajouts / modifications entre versions).
- **Registry générique** : `publish_snapshot` prend `metadata.system_name` / `product_name` à la création du
  système / produit ; `snapshot_label` nomme le produit s'il diffère du système (« datev buchungsstapel 13
  @1.0.0 », Odoo inchangé) ; Compare compare aussi les métadonnées `length` / `decimals` quand elles existent
  (Odoo : résultat identique 17 → 18 → 19, vérifié) ; CLI `--spec`, `--all`, `--product` ; page Compare : sélecteur
  de format quand le système a plusieurs produits (`?from=datev:buchungsstapel:12`) ; `/systems` : versions triées
  numériquement, carte nommée par le format.
- **Mesure** (Compare réel) : 12 → 13 = champ 125 ajouté (Abw. Skontokonto) ; 11 → 12 = 123, 124 ajoutés, 40, 41
  modifiés (libellés « (Bestimmung) ») ; 10 → 11 = 122 ajouté (le changelog annonce 121-122 : la définition v10
  contient déjà 121). Tests réels (`test_real_specs_*`) sautés si les specs locales sont absentes.

## Limites connues du loader Odoo

- **AbstractModel avec ACL en Odoo 17/18** : `ir.model.abstract` n'existe qu'à partir d'Odoo 19 ; avant,
  le loader infère `abstract` = ni `write_date` ni ACL (`metadata.kind_inferred`). Les rares AbstractModel
  qui ont des ACL restent classés `entity` (sur Odoo 19 de référence : 3/106, `publisher_warranty.contract`,
  `res.users.apikeys.show`, `stock.avco.report`). Aucun faux positif mesuré.
- **Selection related sans valeurs** : `ir.model.fields.selection` ne contient pas les valeurs des champs
  selection `related` (ex. `product.template.cost_method` en 17). Leur `metadata.selection` est absent,
  d'où des « selection: None → [...] » dans les compare quand le champ cesse d'être related.
- **Instance Diff — éléments standard ajoutés** : un élément ajouté dont TOUS les modules sont standard et qui
  ne référence pas d'entité tierce reste classé STANDARD (non démontrable autrement) ; aucun cas observé sur
  `odoo17-customer`.
- **Champs custom propagés** : un champ `x_` créé sur `res.partner` apparaît aussi sur `res.users`
  (héritage `_inherits`) ; les deux sont comptés comme champs custom.

## Limites connues de V1d

- **Rate limit du login en mémoire du process** (5 échecs par email, 20 par IP, fenêtre de 15 min) :
  inefficace avec plusieurs workers ou instances de l'API, remis à zéro au redémarrage ; à remplacer par un
  stockage partagé avant la production. Derrière le proxy Next, toutes les requêtes arrivent de 127.0.0.1 :
  la limite par IP est commune à tous les utilisateurs (sans effet en dev ; en production, IP réelle via un
  en-tête de proxy de confiance).
- Pas encore de changement de mot de passe, de réinitialisation, ni de désactivation depuis la console
  (statuts modifiables en base seulement). Les écrans du portail supplier restent à construire.

Ajustements d'exécution acceptés : CLI = premier entrypoint du Core ; spike queue avant le worker.

## Règles de code

- Type hints partout, Pydantic v2 (`extra="forbid"`, `frozen=True` pour les modèles de snapshot).
- Toute nouvelle table passe par une migration Alembic autogénérée puis relue.
- Tout nouveau système = un `SchemaLoader` enregistré via `@LoaderRegistry.register`, avec un dump
  JSON de test dans `packages/connectors/tests/fixtures/`. Rien à modifier dans `uie_core`.
- Tests pytest sur un vrai PostgreSQL (fixture `session` = transaction annulée à la fin, `conftest.py` racine,
  partagée par Core, connecteurs et API ; API testée avec `TestClient` + `dependency_overrides`).
- Secrets : jamais en clair en base, dans les logs, l'API, les snapshots ou les erreurs (`SecretStore`,
  `secret_ref`, `redact()`), jamais en argument de ligne de commande.
- Tout nouveau système côté instances = un `SchemaDiscoveryProvider` enregistré via
  `@DiscoveryProviderRegistry.register` dans `uie_connectors`. Rien à modifier dans `InstanceService`.
- Tout nouveau système côté catalogue réel = un `CatalogReader` enregistré via `@CatalogReaderRegistry.register`
  (lecture seule, aucune sémantique) ; le plan de lecture vient des semantic bindings. Rien à modifier dans
  `uie_core.catalog` ni dans le portail.
- Tout domaine / système côté catalogue = un fichier `domain-packs/catalog/<système>/*.yaml` (découvert
  automatiquement). Rien à modifier dans `uie_core` ; incrémenter `definition_version` à chaque modification.
- Racines de composition (API, CLI) : importer `uie_connectors` et `uie_domain_packs` ; le Core jamais.
- Tout service du Core reçoit un contexte explicite au constructeur (V1d) : `TenantContext` / `AdminContext`
  depuis l'API (`uie_api.deps`), `SystemContext("cli")` en CLI. Toute lecture d'instance passe par le point de
  filtrage unique ; ressource d'un autre tenant = même `NotFound` qu'un id inexistant.
- Toute route `/admin/*` utilise les services Admin de `uie_api.deps`, toute route `/api/*` les services
  `Tenant*` et des DTO de `uie_api.supplier` (jamais un DTO Admin) ; le balayage OpenAPI de
  `test_tenant_isolation_api.py` vérifie chaque nouvelle route.
- **Use cases (Overview)** : tout use case visible par un utilisateur, nouveau ou modifié de manière notable, met à
  jour sa définition centrale dans `apps/web/src/lib/use-cases/catalog.mjs`. Les pages Overview admin (`/overview`) et
  fournisseur (`/portal`) sont générées depuis ces définitions et ne tiennent JAMAIS de liste de workflows en dur.
  Vaut pour tous les domaines (PRODUCT, PARTNER, ACCOUNTING…), systèmes, cibles, imports et exports à venir. Toute
  nouvelle page de `apps/web/src/app` doit être citée par un use case (`routes`) ou listée dans `UNTRACKED_ROUTES`
  avec sa raison : sinon `pnpm test` / `uv run pytest` échouent.
- **Universalité** : aucune source privilégiée. Une cible ne lit que le CDM (binding = données) et le contrat du
  Registry ; un connecteur de cible n'importe ni une source, ni le CDM, ni un domaine. Tout nouveau système
  déclare son formulaire de connexion (`connection_fields`) : jamais de champ ni de condition de système dans une
  page. Toute nouvelle cible par API = un binding `delivery: api` + un `RecordRequestBuilder` enregistré.
- Commits en français ou anglais, un sujet par commit.
- Ne pas ajouter FastAPI, Redis, Kafka ou une dépendance IA sans que l'étape du plan l'exige
  (V1c : `anthropic`, `openai` dans `uie_ai` uniquement ; le Core n'en dépend jamais).
