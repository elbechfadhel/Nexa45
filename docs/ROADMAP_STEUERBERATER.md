# Feuille de route Steuerberater (cabinets d'expertise comptable allemands)

Décisions de l'utilisateur, enregistrées le 2026-10-26. Ce fichier fait foi pour la suite du parcours DATEV ;
`CLAUDE.md` en résume les règles.

## Contexte

- **Démo 1** pour un Steuerberater : les clients (DATEV Debitoren/Kreditoren 5) et les factures de sortie
  (DATEV Buchungsstapel 13), depuis des fichiers Excel, sur la page « Prepare data for DATEV »
  (`/portal/prepare-datev`). La démo est testée et validée.
- **Décision** : préparer d'autres domaines DATEV avant de montrer la démo.
- **Jalons Git** :
  - `demo-1-base` = état initial poussé sur le dépôt distant (ne jamais le déplacer) ;
  - `demo-1` = version de la démo 1, posée après le test manuel de l'utilisateur.

## Modules par objectif

- Chaque public a son point d'entrée : une page, un titre, des cibles. Le premier module est « Prepare data for DATEV ».
- Il n'y a qu'un seul moteur. Un module est une CONFIGURATION, jamais une copie des composants. Cette configuration est faite de :
  - `apps/web/src/lib/wizard/entries.mjs` : titre, introduction, intitulé du choix, systèmes et groupes de cibles, choix du format d'après le fichier ;
  - `apps/web/src/lib/use-cases/catalog.mjs` : le catalogue des use cases.

## Ordre des domaines

1. **Factures d'achat (Eingangsrechnungen)** : Kreditoren, comptes de charges, TVA déductible.
2. **TVA dans les écritures** : autoliquidation §13b, achats UE, pays hors UE.
3. **Caisse (Kassenbuch)**.
4. **Banque** : formats CAMT et MT940, rapprochement avec les factures.
5. **Paie (LODAS / Lohn und Gehalt)** :
   - rien avant la documentation officielle du format d'import ;
   - commencer par les données variables ;
   - protection des données des salariés définie avant tout code.

**Point d'étape** après les domaines 1 à 3.

## Hors périmètre

Nexa45 ne produit pas les déclarations fiscales (bilan, E-Bilanz, Körperschaftsteuer, Gewerbesteuer) : c'est
le métier du Steuerberater. Nexa45 fournit des données propres en amont.

## Méthode pour chaque domaine

- Un fichier de test réaliste avec sa vérité terrain, écrite indépendamment. On ne la modifie jamais pour faire
  passer un test.
- Mesurer d'abord sans corriger, puis corriger point par point : un commit par point, avec test.
- Réutiliser le chemin existant : fichier → tableau de source → CDM → binding DATEV → EXTF → journal (D11).
- Mode Simple :
  - ne jamais demander ce qui peut être lu ou déduit ;
  - 3 questions au maximum, présélectionnées quand une valeur sûre existe ;
  - jamais de correction ni de conversion en silence ;
  - jamais de numéro de conseiller (Beraternummer) ni de mandant (Mandantennummer) inventé.
- Test manuel dans le navigateur par l'utilisateur à la fin de chaque domaine.
- Suite de non-régression par groupes après chaque domaine : Core, connecteurs, domain-packs, `apps/api/tests`, `tests/`, web.
  - Si la mémoire de la machine est juste, découper domain-packs et `apps/api/tests` en lots courts, lancés au premier plan.
