# Universal Integration / Interface Engine

Monorepo V1 — Modular Monolith Python ≥ 3.12 (voir `docs/` et les documents Vision, Décisions,
Socle Technique V2 et System Knowledge Registry).

**État actuel** : System Knowledge Registry (Odoo 17 / 18 / 19), API Admin et console web,
instances client (test de connexion, découverte, Instance Diff), **Product Domain V1a**
(définition standard du catalogue produit, domaine effectif d'une instance, snapshots immutables) et
**Semantic Enrichment V1b / V1b.1** (règles déterministes + réponses du supplier ; page « Product Catalog
Analysis ») et **V1c** : proposition sémantique par LLM, explicite (« Ask AI »), toujours confirmée par un
humain. DATEV et le mapping viennent ensuite.

```
Registry            → WHAT EXISTS?
Product Domain      → WHAT BELONGS TO THE CATALOG?
Semantic Enrichment → WHAT DOES IT MEAN?   (V1b / V1c)
Mapping             → WHERE DOES IT GO?    (futur)
```

## Structure

```
apps/cli/            uie — Admin CLI (premier entrypoint du Core)
apps/api/            uie_api — API Admin FastAPI (Registry, instances, Product Domain)
apps/web/            console Next.js (Systems, Schema Browser, Version Compare, Instances)
apps/worker/         worker queue (à venir)
packages/core/       uie_core : registry/, instances/, domains/ (Product Domain), db/ (Alembic)
packages/connectors/ uie_connectors : odoo/ (metadata XML-RPC + JSON, loader, discovery provider)
domain-packs/        uie_domain_packs : catalog/vocabulary.yaml, catalog/odoo/product_domain.yaml + adaptateur
packages/ai/         uie_ai : proposers LLM (OpenAI, Anthropic, Replay, Null) du contrat SemanticProposer
packages/cdm/        Canonical Data Model (à venir)
schemas/ docs/ tests/ docker/
```

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) et Docker. Sous Windows, installer uv avec
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

Le conteneur PostgreSQL est exposé sur le port **5433** (pour ne pas entrer en conflit avec un
PostgreSQL local sur 5432). La configuration est lue depuis `.env` (chargé automatiquement,
cherché depuis le répertoire courant) ; une variable déjà définie dans l'environnement est prioritaire.

```bash
# 1. Dépendances (uv gère Python 3.12 et le workspace)
uv sync

# 2. PostgreSQL du Registry
docker compose -f docker/docker-compose.yml up -d postgres
cp .env.example .env            # UIE_DATABASE_URL=postgresql+psycopg://uie:uie@localhost:5433/uie
# ou, sans .env : export UIE_DATABASE_URL=postgresql+psycopg://uie:uie@localhost:5433/uie

# 3. Migrations
uv run uie db upgrade

# 4. Premier import hors-ligne (dumps d'exemple) puis exploration
uv run uie registry import odoo --json packages/connectors/tests/fixtures/odoo17_ref.json
uv run uie registry import odoo --json packages/connectors/tests/fixtures/odoo18_ref.json
uv run uie registry systems
uv run uie registry show odoo 17
uv run uie registry show odoo 17 res.partner
uv run uie registry fields odoo 18 --search price
uv run uie registry compare odoo 17 odoo 18
```

Sous Windows (PowerShell), seules les étapes de configuration changent ; les commandes `uv run uie ...`
sont identiques :

```powershell
uv sync
docker compose -f docker/docker-compose.yml up -d postgres
Copy-Item .env.example .env
# ou, sans .env : $env:UIE_DATABASE_URL = "postgresql+psycopg://uie:uie@localhost:5433/uie"
uv run uie db upgrade
uv run uie registry import odoo --json packages/connectors/tests/fixtures/odoo17_ref.json
```

## Alimenter le Registry avec de vraies instances Odoo de référence

```bash
# Conteneurs Odoo 17/18/19 avec les modules "standard" (base, contacts, product, uom,
# sale_management, purchase, stock, account) — première initialisation : quelques minutes
docker compose -f docker/docker-compose.yml --profile odoo up -d

# Introspection directe (XML-RPC) et publication d'un snapshot immutable
uv run uie registry import odoo --url http://localhost:8017 --db ref17 -u admin -p admin
uv run uie registry import odoo --url http://localhost:8018 --db ref18 -u admin -p admin
uv run uie registry import odoo --url http://localhost:8019 --db ref19 -u admin -p admin

# Ou : dump reproductible (JSON) puis import hors-ligne
uv run uie registry dump odoo --url http://localhost:8017 --db ref17 -u admin -p admin -o odoo17.json
uv run uie registry import odoo --json odoo17.json
```

Un nouvel import au même contenu ne crée pas de snapshot ; un contenu différent crée `1.0.1`, etc.
Les snapshots ne sont jamais modifiés (checksum + provenance dans `schema_snapshots.source`).

## API Admin et console web

Prérequis supplémentaires : Node.js ≥ 20.9 et pnpm ≥ 10 (`corepack enable`).

```bash
# Une fois : compte administrateur plateforme (idempotent ; la migration ne crée aucun compte)
uv run uie admin bootstrap        # UIE_INITIAL_ADMIN_EMAIL / UIE_INITIAL_ADMIN_PASSWORD, ou saisie masquée

# Terminal 1 — API (FastAPI) : http://127.0.0.1:8000/docs
uv run uie api serve --port 8000

# Terminal 2 — console web (Next.js) : http://localhost:3000
pnpm install          # une fois, à la racine (workspace pnpm)
pnpm dev              # = pnpm --filter web dev, port 3000
```

Pages : `/systems` (arbre System → Product → Version → Interface → snapshots),
`/snapshots/<id>` (Schema Browser : entités, champs, relations) et
`/compare?from=odoo:17&to=odoo:19&entity=product.template` (Version Compare).
Connexion obligatoire (`/login`) : la console technique est réservée aux administrateurs plateforme ;
`/admin/suppliers` gère les fournisseurs (organisations), leurs utilisateurs et leurs instances ; un
utilisateur fournisseur arrive sur le portail `/portal` (écrans à venir). Comptes en CLI : `uie org ...`,
`uie user ...`.
La console appelle l'API par le proxy de même origine `/uie-api/*` (cookie de session HttpOnly) ; la cible
se change avec `UIE_API_URL` (défaut `http://127.0.0.1:8000`, relu au démarrage de `pnpm dev`).

Le client TypeScript est généré depuis l'OpenAPI du backend ; après toute modification de l'API :

```bash
pnpm gen:api          # uie api openapi -o apps/web/openapi.json + openapi-typescript
```

Un test pytest échoue si `apps/web/openapi.json` n'est plus à jour. Autres commandes :
`pnpm typecheck`, `pnpm lint`, `pnpm build`.

## Instances client (test de connexion, découverte, Instance Diff)

```bash
uv run uie secret gen-key            # copier la valeur dans .env : UIE_SECRET_KEY=...
docker compose -f docker/docker-compose.yml --profile customer up -d odoo17-customer   # Odoo 17 client (:8027)
uv run uie instance add -n customer17 -s odoo -v 17 --url http://localhost:8027 --db customer17 -u admin
uv run uie instance test customer17           # connexion réelle + capabilities (lecture seule)
uv run uie instance discover customer17       # snapshot d'instance n°1 (immutable) + diff
uv run python docker/customer-customize.py    # ajoute res.partner.x_customer_no et
                                              # product.template.x_supplier_ref (idempotent)
uv run uie instance discover customer17       # snapshot n°2
uv run uie instance diff customer17 --details # Standard Odoo 17 → instance
```

Le mot de passe n'est jamais passé en argument : invite masquée, `--password-stdin` ou
`UIE_INSTANCE_PASSWORD`. Il est chiffré (Fernet) ; la base ne contient qu'un `secret_ref`.
Console web : http://localhost:3000/instances.

## Domaine PRODUCT (Product Domain V1a)

La connaissance « qu'est-ce qui appartient au catalogue produit » est de la donnée : un domain pack
YAML par système (`domain-packs/catalog/<système>/product_domain.yaml`), résolu contre un snapshot.
L'analyse travaille uniquement sur des snapshots persistés : elle ne contacte jamais l'instance,
n'appelle aucun LLM et n'applique aucune règle sémantique (V1b).

```bash
uv run uie product-domain show-standard --system odoo --version 17 [--fields]
uv run uie product-domain analyze --instance customer17      # domaine effectif du dernier snapshot
uv run uie product-domain show --instance customer17
uv run uie product-domain fields --instance customer17 --group media
uv run uie product-domain extensions --instance customer17   # CUSTOM / THIRD_PARTY du scope
uv run uie product-domain missing --instance customer17      # standard absent de l'instance
uv run uie product-domain history --instance customer17
```

Console web : `/instances/<id>/product-domain` (lien « Product domain » sur la page de l'instance) —
vue technique en lecture seule : Analyze, historique, entités par rôle, médias, extensions, missing standard,
champs par groupe avec filtres `?group=&origin=&relevance=` (filtrage fait par l'API).

### Semantic Enrichment (V1b) : que signifie chaque champ ?

`analyze` enrichit le domaine par règles (données du pack : `semantics.rules`, vocabulaire
`domain-packs/catalog/vocabulary.yaml`) : chaque champ est identifié (semantic_key), exclu (avec une raison
contestable) ou gardé « possible » ; seules les extensions ambiguës deviennent des questions. Les réponses
du supplier sont conservées d'une analyse à l'autre (question reposée si le champ change).

```bash
uv run uie product-domain analyze --instance customer17      # domaine effectif + enrichissement
uv run uie product-domain summary --instance customer17      # compteurs, structure, complétude
uv run uie product-domain questions --instance customer17
uv run uie product-domain exclusions --instance customer17 --extensions
uv run uie product-domain confirm --instance customer17 -e product.template -f x_supplier_ref
uv run uie product-domain change  --instance customer17 -e product.template -f x_pack_code -k packaging_code
uv run uie product-domain ignore  --instance customer17 -e product.template -f x_notes_internal
uv run uie product-domain include --instance customer17 -e product.template -f project_id
```

Console web : `/instances/<id>/catalog` (lien « Product Catalog Analysis » sur la page de l'instance) —
page supplier : questions Confirm / Change / Ignore, revue des exclusions automatiques, état final
« Catalog analysis complete ». La vue technique `/instances/<id>/product-domain` reste la vue « Advanced ».

### Proposition sémantique par LLM (V1c) : « Ask AI »

Le LLM aide à comprendre un champ ; il ne décide jamais de le retirer du catalogue.
`Rules → LLM semantic proposal → Human confirmation when needed`. Il ne répond que MATCH (une clé du
vocabulaire proposée pour ce champ) ou NO_CONFIDENT_MATCH ; chaque réponse devient une question au supplier.
Il n'est appelé QUE sur demande (bouton « Ask AI », `enrich-ai`), jamais par une analyse ni une page.

```bash
# .env : UIE_LLM_PROVIDER=openai + OPENAI_API_KEY + OPENAI_MODEL (voir .env.example)
uv run uie product-domain ai-status --instance customer17     # fournisseur, candidats, appels, tokens
uv run uie product-domain enrich-ai --instance customer17     # Ask AI (métadonnées uniquement)
uv run uie product-domain enrich-ai --instance customer17 \
    --replay-file domain-packs/catalog/llm/replay/customer17_v1c.json   # démo hors-ligne
```

Mesure sur customer17 : règles seules 430 champs → 15 ambiguïtés ; règles + LLM → 5 propositions,
10 sans proposition fiable ; humain → 0.

Référence : `docs/PRODUCT_DOMAIN_V1_IMPLEMENTATION_PLAN.docx`.

## Tests

La base de test `uie_test` est créée si absente ; valeur par défaut ci-dessous, à surcharger au besoin.

```bash
export UIE_TEST_DATABASE_URL=postgresql+psycopg://uie:uie@localhost:5433/uie_test
uv run pytest
uv run ruff check .
```

```powershell
$env:UIE_TEST_DATABASE_URL = "postgresql+psycopg://uie:uie@localhost:5433/uie_test"
uv run pytest
uv run ruff check .
```

## Principes appliqués (rappel)

- Aucune notion Odoo/DATEV dans le modèle du Registry : tout ce qui est spécifique vit dans le
  Schema Loader du système et dans les colonnes JSONB `metadata`.
- Le Core (`uie_core`) ne dépend ni de FastAPI ni de la CLI ; la CLI n'est qu'un entrypoint.
- `compare_snapshots()` est une fonction pure : elle sert au Version Compare (17 → 18) et au
  futur Instance Diff (standard → instance client), déjà couvert par un test.
- Ajouter un système = ajouter un `SchemaLoader` dans `uie_connectors` et l'enregistrer via
  `@LoaderRegistry.register`. Rien à changer dans le Core.
- Décrire le catalogue d'un système = ajouter `domain-packs/catalog/<système>/product_domain.yaml`
  (découvert et enregistré automatiquement). Aucun nom d'entité ou de champ dans `uie_core`.
- Le Core ne dépend d'aucun fournisseur LLM : il ne connaît que le contrat `SemanticProposer` ;
  les implémentations vivent dans `uie_ai` et sont choisies par les racines de composition (API, CLI).
