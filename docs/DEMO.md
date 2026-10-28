# Démo locale « Prepare data for DATEV »

Mode démo sans Docker et sans mode développement : site construit en production (`next build` puis
`next start`), API sans rechargement automatique, base PostgreSQL locale. Le profil de construction `demo`
masque tout ce qui n'est pas validé : les use cases experimental et coming soon, et Smart export. Il garde
« Prepare data for DATEV », validé par le test manuel des domaines 1 à 4.

Le mode développement (`pnpm dev`, `uie api serve --reload`) n'est pas modifié : sans la variable
`NEXT_PUBLIC_UIE_PROFILE=demo`, la console affiche tout.

## Avant la démo (15 minutes avant)

1. **Fermer les autres programmes** :
   - le mode développement : `pnpm dev` et `uie api serve`. Ils prennent les ports 3000 et 8000, et le script
     refuse de démarrer tant que ces ports sont pris ;
   - l'IDE, Docker Desktop, les onglets inutiles.

   Le build a besoin d'environ 1,4 Go de mémoire. La démo lancée tient ensuite dans environ 300 Mo.
2. **Lancer le script** (PowerShell, à la racine du dépôt) :

       powershell -ExecutionPolicy Bypass -File scripts\demo-start.ps1

   Le script fait, dans l'ordre :
   - il vérifie PostgreSQL et met la base `nexa` à jour (`uie db upgrade`) ;
   - il construit le site, ce qui prend environ 40 s ;
   - il lance l'API, puis le site ;
   - il prépare le dossier de démo : `uie demo prepare --org go45-development --answers temp/demo_answers.yaml --yes` ;
   - il vérifie la page et affiche l'adresse à ouvrir.

   Options :
   - `-SkipBuild` réutilise le dernier build démo s'il est à jour (même commit, rien de modifié dans `apps/web`) ;
   - `-SkipPrepare` ne remet pas le dossier à zéro ;
   - `-Org`, `-Answers`, `-ApiPort` et `-WebPort` changent les valeurs par défaut.
3. **Vérifier** :
   - la dernière ligne indique `Démo prête : http://localhost:3000/portal/prepare-datev` ;
   - la préparation affiche « 8 fichier(s) exporté(s) ; 29 numéro(s) de compte réservé(s) ; 83 facture(s)
     au registre des factures ouvertes » ;
   - ouvrir l'adresse et se connecter avec le compte de démo. Le header montre « Prepare data for DATEV,
     Overview, My systems, Data », sans Smart export ;
   - **faire un premier dépôt de fichier hors audience**, puis revenir en arrière. Le premier appel de l'API
     charge ses modules et prend environ 4 s ; les suivants prennent moins d'une seconde.

Journaux : `var\demo\api.log`, `api.err.log`, `web.log` et `web.err.log`.

## Pendant la démo : ordre des fichiers

Le dossier est déjà rempli par le script : les 8 fichiers ci-dessous sont exportés. Le registre des
factures ouvertes connaît donc les factures dont les paiements arrivent dans les relevés.

| # | Fichier (préparé par le script) | Format DATEV |
|---|---|---|
| 1 | `Kundenliste_Mandant.xlsx` | Debitoren/Kreditoren 5 |
| 2 | `Lieferantenliste_Mandant.xlsx` | Debitoren/Kreditoren 5 |
| 3 | `files/Kundenliste_Ausland.xlsx` | Debitoren/Kreditoren 5 |
| 4 | `files/Lieferantenliste_Ausland.xlsx` | Debitoren/Kreditoren 5 |
| 5 | `Rechnungsausgang_Q3_2026.xlsx` | Buchungsstapel 13 (38 écritures) |
| 6 | `Rechnungseingang_Q3_2026.xlsx` | Buchungsstapel 13 (32 écritures ; Druckkosten → 4930) |
| 7 | `files/Rechnungsausgang_Ausland_Q4_2026.xlsx` | Buchungsstapel 13 (7 écritures, Goods) |
| 8 | `files/Rechnungseingang_Ausland_Q4_2026.xlsx` | Buchungsstapel 13 (6 écritures, Goods) |

Montrés en direct, dans cet ordre (fichiers dans `temp/`) :

1. un des fichiers ci-dessus, si tu veux montrer la page d'un export de liste ou de factures. Il est déjà
   exporté : la page le dit, et rien n'est écrit deux fois ;
2. `Kontoauszug_Oktober_2026.camt053.xml`, le relevé CAMT : 17 écritures, paiements rapprochés par numéro de
   facture et montant, solde contrôlé ;
3. `Umsaetze_November_2026.csv`, l'export CSV de la banque : 6 écritures, un mouvement déjà exporté jamais
   compté deux fois ;
4. le même relevé une seconde fois : « N bank movements already exported on <date> ».

## En cas de problème

| Situation | Que faire |
|---|---|
| « Le port 3000 (ou 8000) est déjà pris » | Fermer le mode développement, puis relancer le script |
| « PostgreSQL ne répond pas » | Démarrer le service PostgreSQL local (`services.msc`, postgresql-x64-18), puis relancer |
| « uie demo prepare a échoué » | Le message nomme la question sans réponse : ajouter la clé dans `temp/demo_answers.yaml`. Rien n'est enregistré. Les serveurs restent lancés : relancer seulement `uv run uie demo prepare --org go45-development --answers temp/demo_answers.yaml --yes` |
| Page blanche ou erreur pendant la démo | `scripts\demo-stop.ps1`, puis `scripts\demo-start.ps1 -SkipBuild` (environ 15 s sans build) |
| Dossier à remettre à l'état initial (après un export en direct) | `uv run uie demo prepare --org go45-development --answers temp/demo_answers.yaml --yes` (les serveurs peuvent rester lancés) |
| Rien ne répond, machine saturée | Passer à la **vidéo de secours**, puis redémarrer à froid : `demo-stop`, fermer les programmes, `demo-start` |

**Vidéo de secours** : enregistrer le parcours complet la veille, sur cette même démo. Garder la vidéo en
local, hors du dépôt, et l'ouvrir dans un lecteur avant la démo.

## Après la démo

    powershell -ExecutionPolicy Bypass -File scripts\demo-stop.ps1

Le script arrête l'API et le site enregistrés dans `var\demo\pids.json`, et rien d'autre. Si ce fichier
manque, il indique seulement ce qui écoute encore sur les ports : une API lancée à la main a la même ligne
de commande et ne doit pas être arrêtée par erreur. Ensuite, `pnpm dev` peut être relancé normalement : le
mode développement écrit dans `.next/dev` et ne touche pas au build de démo.

À savoir :
- le script doit être lancé dans sa propre fenêtre PowerShell. S'il est appelé dans un pipeline (`| Tee-Object`,
  `Start-Process -Wait`), l'appelant attend les serveurs lancés et ne rend pas la main ;
- la démo écrit dans la base de `.env` (`nexa`). Une autre base se choisit par `UIE_DATABASE_URL` avant le
  script ;
- un `pnpm build` normal, sans profil, remplace le build démo. Le script le détecte et reconstruit.
