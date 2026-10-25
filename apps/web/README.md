# apps/web — console d'administration UIE

Next.js 16 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query. Voir le README racine
(section « API Admin et console web ») pour le démarrage : `pnpm dev` (port 3000) avec l'API sur :8000 (proxy `/uie-api`, connexion `/login`).

- `src/lib/api/schema.d.ts` est généré (`pnpm gen:api`) depuis `openapi.json` : ne pas l'éditer.
- Aucune logique métier ni de comparaison ici : tout vient de l'API Admin.
- Next 16 a des changements incompatibles avec les versions antérieures : lire `AGENTS.md`.

## Aide DEV : « Fill customer17 (DEV) » (tests manuels V2b.1)

Sur `/portal/systems/connect`, avec `pnpm dev` seulement, un bouton pré-remplit le formulaire Odoo avec une
instance LOCALE de test. Il ne teste ni n'enregistre rien. Valeurs lues côté serveur (route `/dev/connect-fill`,
404 hors `next dev` et hors requête locale), jamais dans le code ni dans le bundle ; à mettre dans
`apps/web/.env.local` (ignoré par git) :

```
UIE_DEV_ODOO_NAME=customer17
UIE_DEV_ODOO_URL=http://localhost:8027
UIE_DEV_ODOO_DB=customer17
UIE_DEV_ODOO_USERNAME=admin
UIE_DEV_ODOO_PASSWORD=<mot de passe de l'instance Docker locale>
```

Sans ces variables, le bouton est désactivé ; il n'existe pas dans un build de production.
