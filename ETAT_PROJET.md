# État des lieux — Universal Integration Engine (Nexa45)

*Établi le 2026-10-22 à partir du code présent dans le dépôt (branche `master`, 197 commits) et de la base de
développement `nexa`. Les chiffres sont des comptages faits sur le code, pas des estimations.*

---

## 1. Objectif du projet et fonctionnalités prévues

**Objectif.** Une couche d'intégration indépendante des ERP : des systèmes sources sont lus, leurs données passent
par un modèle canonique (CDM), puis sont préparées pour un système cible (fichier ou API). Odoo, DATEV et Lexware
ne sont pas le produit mais des connecteurs du moteur. Le cœur de la démarche est un **System Knowledge Registry**
qui connaît d'avance les schémas des systèmes supportés (entités, champs, types, obligations, valeurs admises).

Chaîne visée : `Source → Connecteur → Registry → domaine sémantique → CDM → binding cible → validation → fichier /
API cible`.

**Fonctionnalités présentes dans le code** (catalogue des use cases, `apps/web/src/lib/use-cases/catalog.mjs`,
22 entrées) :

| Statut | Use cases |
|---|---|
| `available` (12) | Explorer le Registry, comparer des versions, gérer les instances, analyse technique du catalogue, gérer les fournisseurs, connecter son système, « My systems », analyser / parcourir / importer son catalogue (fichier → Odoo), analyser clients & fournisseurs, export clients & fournisseurs vers DATEV |
| `experimental` (9) | AI Migration Wizard ; produits → Lexware Office (aperçu, envoi) ; clients & fournisseurs → Lexware Office (aperçu, envoi) ; lecture comptable ; préparation et fichier DATEV Buchungsstapel ; pièces comptables → Lexware Office |
| `coming_soon` (1) | Envoi direct de la comptabilité à DATEV (`accounting.datev.submit`) |

**Prévu mais absent du code** : worker / file d'attente (`apps/worker` n'existe pas), contrats d'exécution
`IntegrationRecord` / `Envelope` (`uie_core/contracts` ne contient qu'un docstring « phase suivante »), mapping
templates, sens DATEV → Odoo, instance DATEV de test.

---

## 2. Stack technique

| Couche | Technologies (versions déclarées) |
|---|---|
| Langage back | Python ≥ 3.12, workspace **uv** (7 paquets) |
| HTTP | FastAPI ≥ 0.115, Uvicorn, python-multipart |
| CLI | Typer ≥ 0.12 + Rich |
| Données | **PostgreSQL** (dev : PostgreSQL 18 local, base `nexa` ; Docker 16 en 5433 historiquement), SQLAlchemy ≥ 2.0.35, Alembic (20 migrations, 46 tables), psycopg 3 |
| Modèles | Pydantic ≥ 2.9 (`extra="forbid"`, modèles gelés) |
| Fichiers | Polars ≥ 1.20 + fastexcel (lecture), xlsxwriter (écriture Excel) |
| Sécurité | cryptography (Fernet pour les secrets), argon2-cffi (mots de passe) |
| Connecteurs | httpx, PyYAML, XML-RPC (bibliothèque standard) pour Odoo |
| IA | `openai` ≥ 3.19, `anthropic` ≥ 1.8 (paquet `uie_ai` seulement) |
| Front | **Next.js 16.3.6** (App Router, Turbopack), React 19.2.8, TypeScript 5, Tailwind CSS 4, shadcn + `@base-ui/react`, TanStack Query 5, `openapi-fetch` + types générés par `openapi-typescript` |
| Outillage | ruff (lignes de 110), pytest (+ pytest-asyncio), ESLint 9, `node --test`, pnpm, Docker Compose |

Volume : ~44 000 lignes Python hors tests (dont `uie_core` ~28 700, `uie_api` ~7 500), ~23 000 lignes TypeScript /
JavaScript côté web (29 pages), 165 fichiers de tests Python (793 fonctions de test), 10 fichiers de tests web.

---

## 3. Architecture et structure des dossiers

**Modular monolith.** Un seul Core testable sans FastAPI ; CLI et API sont des points d'entrée qui appellent les
mêmes services. Chaque service reçoit un contexte explicite (`TenantContext`, `AdminContext`, `SystemContext`).

```
apps/
  api/        uie_api   — FastAPI : /auth, /admin (console), /api (portail fournisseur), 170 routes
  cli/        uie_cli   — commande `uie` (registry, instance, product-domain, partner, export, org, user…)
  web/        console Next.js : /admin, /systems, /compare, /instances…, portail /portal/*
packages/
  core/       uie_core  — registry, instances, domains (sémantique, enrichissement), identity, catalog,
                          imports, partners, products, accounting, mapping, exports, delivery, sources,
                          wizard, db (modèles + migrations), contracts (vide)
  connectors/ uie_connectors — odoo/, datev/, lexware/, known_systems.yaml, errors.py
  cdm/        uie_cdm   — Partner 1.0.0, Product 1.0.0 (+ JSON Schema publiés)
  ai/         uie_ai    — proposers LLM : Null, Replay, Recording, OpenAI, Anthropic ; prompts du Wizard
domain-packs/ données YAML : catalog/, partner/, accounting/ (vocabulaires, packs Odoo, bindings cibles
              DATEV / Lexware, sources Lexware, prompts LLM) + adaptateur Python
docker/       docker-compose (PostgreSQL, Odoo 17/18/19 de référence, instances customer17 / accounting17)
              + scripts de données de démo
docs/dumps/   dumps de schémas Odoo reproductibles
tests/        tests transverses (use cases web, script accounting17)
```

**Principes vérifiés par des tests d'architecture** : aucun nom de système dans `uie_core` (analyse AST), aucun
import de fournisseur LLM dans le Core, connecteur cible sans dépendance vers une source ni vers le CDM, toute route
`/admin` et `/api` balayée par le test d'isolation multi-tenant, toute page web rattachée à un use case.

---

## 4. Intégrations externes

| Système | Ce que fait le code | Mode |
|---|---|---|
| **Odoo 17 / 18 / 19** | Introspection du schéma (`ir.model`, `ir.model.fields`, `fields_get`) → Registry ; découverte d'instance client + diff standard / réel ; lecture du catalogue, des partenaires et des pièces comptables ; lecture des valeurs de référence (Wizard) ; **écriture** du catalogue importé (`writer.py` : `load` + identifiant externe, jamais de suppression) | XML-RPC, proxy à liste blanche |
| **DATEV** | Registry des formats EXTF (Buchungsstapel 10-13, Debitoren/Kreditoren 5) générés depuis des sources officielles **locales** ; écrivain de fichiers EXTF (CP1252, CRLF) ; exports clients / fournisseurs et Buchungsstapel avec idempotence par pièce (D11) | Fichiers seulement, aucun appel API |
| **Lexware Office** | Contrat API minimal versionné dans git (`lexware/contract/public_api_v1.yaml`) → Registry ; client HTTP (Bearer, 2 req/s, reprises sur 429) ; lecture (source) de contacts et de pièces ; envoi (cible) de contacts, articles et pièces, avec suivi des envois et des issues inconnues | API REST `api.lexware.io/v1` |
| **OpenAI / Anthropic** | Propositions sémantiques (champs), correspondance des colonnes d'import, questions et structure du Wizard ; mode Replay pour les tests | Opt-in par `UIE_LLM_PROVIDER` |
| **Fichiers** | Import .xlsx / .csv (≤ 10 Mo, 20 000 lignes) ; exports CSV EXTF ; fichiers Excel préparés par le Wizard | Stockage local `var/imports` |

Registry de `nexa` : 3 systèmes « supported » (odoo, datev, lexware) et 16 systèmes « known » sans schéma
(SAP Business One, S/4HANA, Business Central, Dynamics 365 Finance, NetSuite, Shopify, Xero, sevdesk, weclapp…).

---

## 5. Ce qui fonctionne déjà

Constaté par les tests exécutés lors de cette session (sous-ensembles, pas la suite complète) et par l'usage :

- **Registry** : import Odoo 17 / 18 / 19 (dumps), DATEV, Lexware ; navigation et comparaison de versions (API +
  console).
- **Comptes et multi-tenant** : login par cookie, organisations, rôles, isolation vérifiée sur toutes les routes
  (test de balayage OpenAPI, vert).
- **Instances** : connexion, test de connexion, découverte, diff, suppression en cascade ; secrets chiffrés.
- **Domaine PRODUCT** : analyse sémantique (règles + LLM optionnel), décisions humaines, synchronisation et
  consultation du catalogue réel.
- **Imports de catalogue** : profil du fichier, correspondances (mémoire apprise, règles, LLM), aperçu, import
  réel idempotent dans Odoo.
- **PARTNER** : analyse, Partner CDM, export Debitoren/Kreditoren (fichier EXTF).
- **ACCOUNTING** : lecture des pièces Odoo, préparation et fichier Buchungsstapel avec idempotence par pièce.
- **Lexware Office** : aperçus et envois (contacts, articles, pièces) avec un faux serveur de test.
- **AI Migration Wizard** : questionnaire, structure cible vérifiée par le Registry, correspondance du fichier du
  client, contrôles (codes-barres, prix, références), questions prévisibles posées avant le fichier, fichier
  préparé.
- Résultats du jour : 137 tests Python (Wizard, API, isolation, fraîcheur OpenAPI, use cases), 94 tests web,
  ruff, typecheck et lint verts.

---

## 6. Incomplet, cassé ou à faire

Le code ne contient **aucun** marqueur `TODO` / `FIXME` / `HACK` ; l'inachevé se lit ailleurs :

- **Non implémenté** : worker et file d'attente ; contrats `IntegrationRecord` / `Envelope` (`uie_core/contracts`
  vide) ; envoi direct à DATEV (`coming_soon`) ; sens DATEV → Odoo ; mapping templates ; variantes de produits et
  images dans l'import du catalogue.
- **Expérimental** (9 use cases) : tout ce qui touche Lexware Office, la comptabilité et le Wizard.
- **Jamais validé sur la vraie API Lexware** : le premier envoi réel est en pause (clé d'API sans permission
  « articles », HTTP 403). Les envois sont testés contre `FakeLexwareServer`.
- **Tests conditionnels** : environ 14 fichiers de tests contiennent des marqueurs `skip` / `skipif` liés à la
  spécification DATEV locale (`var/datev/specs/extf_700`, hors git pour raison de licence) ou à ses fichiers
  d'exemple : ces tests sont sautés sur toute autre machine.
- **Logos des systèmes** : tuiles PNG recadrées (`apps/web/public/systems/`), à remplacer par les fichiers
  officiels.
- **Fonctions de compte manquantes** : ni changement ni réinitialisation de mot de passe, ni désactivation depuis la
  console.
- **Travail non commité** : 43 entrées dans `git status` (Wizard : templates, contrôles, questions avant le
  fichier ; migration `5b2e9c4d1a77`…).

---

## 7. Problèmes et dettes techniques repérés

1. **Aucun dépôt distant ni CI** : `git remote` est vide, 197 commits locaux seulement, pas de `.github/`. Risque de
   perte et aucune exécution automatique des tests.
2. **Base de test mal choisie** : `conftest.py` lit `UIE_TEST_DATABASE_URL` *avant* le chargement de `.env` ; sans
   variable exportée, les tests visent PostgreSQL Docker (port 5433) et non `nexa_test` (5432).
3. **État en mémoire du processus** : limitation des tentatives de login, limiteur de débit Lexware, cache du Wizard
   (`OrderedDict`, 256 entrées). Remis à zéro au redémarrage, faux avec plusieurs workers ; derrière le proxy Next,
   toutes les requêtes arrivent de 127.0.0.1 (limite par IP commune).
4. **Opérations longues synchrones** (analyse avec LLM, synchronisation, envois) sans worker ; le proxy Next a été
   porté à 5 minutes pour éviter les 500. La comparaison de schémas recharge deux snapshots complets (~2 s).
5. **Gros fichiers** : `portal/wizard/page.tsx` (2 316 lignes), `accounting/target.py` (1 688),
   `supplier-hooks.ts` (1 072), `wizard/service.py` (1 013). Difficiles à relire et à tester.
6. **Documentation qui dérive** : `CLAUDE.md` annonce 695 tests (793 fonctions aujourd'hui) et un portail « Coming
   soon » alors que les écrans existent.
7. **Petites incohérences** : deux fonctions `cn` coexistent (paquet npm `cn` dans 12 fichiers, `@/lib/utils` dans
   28) ; `mypy` déclaré en dépendance de dev sans configuration ni usage ; dépendance de dev `httpx2` utilisée par un
   seul test.
8. **Dépendances d'environnement** : Docker Desktop instable sur la machine de dev (arrêts en code 255) alors que les
   instances Odoo de test en dépendent ; manque de mémoire observé (serveurs de dev arrêtés, suite complète de tests
   à lancer en plusieurs groupes).
9. **Données sous licence** : rien de DATEV brut dans git (voulu), ce qui rend une partie du produit non
   reproductible hors de la machine de dev.

---

## 8. Prochaines étapes logiques

1. **Sécuriser le dépôt** : créer un dépôt distant, pousser, ajouter une CI (ruff, pytest par groupes, typecheck,
   lint, `pnpm test`) avec PostgreSQL de service.
2. **Corriger `conftest.py`** pour charger `.env` avant de lire `UIE_TEST_DATABASE_URL`, puis relancer la suite
   complète sur `nexa_test`.
3. **Commiter le travail en cours du Wizard** par sujets (templates, contrôles avant import, questions avant le
   fichier), après une passe de test manuel dans le portail.
4. **Valider Lexware Office en réel** : clé avec permissions articles / contacts / vouchers, un enregistrement à la
   fois, puis faire passer les use cases de `experimental` à `available`.
5. **Spike de file d'attente** (Dramatiq / Celery / arq) puis `apps/worker` pour sortir les analyses, synchronisations
   et envois du cycle requête / réponse ; déplacer les états en mémoire vers PostgreSQL.
6. **Contrats d'exécution** (`IntegrationRecord`, `Envelope`, Identity Registry) pour unifier imports, exports et
   envois aujourd'hui spécifiques à chaque domaine.
7. **Découper les gros fichiers** (page du Wizard en composants, `accounting/target.py`).
8. **Compléter le catalogue** : variantes et images à l'import ; puis DATEV → Odoo et l'envoi direct à DATEV si la
   licence et l'accès API sont réglés.
9. **Mettre `CLAUDE.md` à jour** (nombre de tests, état du portail, base `nexa`).
