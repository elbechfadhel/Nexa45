# Transliate — Corporate Design Pack (Light mode)

Style extrait tel quel du frontend Transliate (`frontend/tailwind.config.ts`, `frontend/src/index.css`, markup des pages).

Ouvrir **`index.html`** dans un navigateur pour voir le style guide.

| Fichier | Rôle |
|---|---|
| `transliate.css` | Tokens (CSS variables), base et classes de composants (`.btn`, `.card`, `.input`, `.badge`, `.pill`, `.tbl`, `.panel`, `.stat-card`, `.nav-pill`, `.menu`, `.modal-*`…). Sans dépendance. |
| `tailwind-preset.js` | Preset Tailwind v3 : `brand-*`, `cta`, polices, `rounded-card/btn/panel`, `shadow-card/modal/panel`. |
| `tokens.ts` | Mêmes valeurs en TS (graphiques, inline styles, PDF, e-mails). |
| `react/index.tsx` | `Logo`, `Button`, `Input`, `Select`, `Textarea`, `Field`, `Badge`, `StatusPill`, `Card`, `KpiCard`, `StatCard`, `Modal`. |
| `logos/` | Logo SVG / PNG @2x, icône SVG. |

## Intégration rapide

```tsx
// main.tsx
import './design-pack/transliate.css';

// composant
import { Button, Badge } from './design-pack/react';
<Button variant="primary">Valider</Button>
<Badge color="green">Terminée</Badge>
```

**Avec Tailwind v3 :** ajouter `presets: [require('./design-pack/tailwind-preset.js')]` (ou `import`) dans `tailwind.config`, et importer `transliate.css` **en tête** du CSS principal, avant les directives `@tailwind`.

**Notes**
- `react/index.tsx` importe le logo SVG comme URL : c'est natif avec Vite et Next. Sans bundler compatible, passez par `public/`.
- Polices : DM Sans + DM Mono sont chargées via `@import` Google Fonts en tête de `transliate.css`. Pour un hébergement local (RGPD), remplacez cet import par des `@font-face`.
- Les classes génériques (`.btn`, `.card`, `.input`…) peuvent entrer en conflit avec Bootstrap ou une autre lib CSS dans le même projet.
