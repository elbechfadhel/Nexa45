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
2. **TVA dans les écritures** : autoliquidation §13b, achats UE, pays hors UE. Codé (2026-10-26), test manuel
   à faire ; voir « Domaine 2 » ci-dessous.
3. **Caisse (Kassenbuch)**. Codé (2026-10-27), test manuel à faire ; voir « Domaine 3 » ci-dessous.
4. **Banque** : relevé CAMT.053 et export CSV de la banque, rapprochement avec les factures exportées. Codé
   (2026-10-27), test manuel à faire ; voir « Domaine 4 » ci-dessous. MT940 : plus tard.
4b. **Escompte (Skonto)** : domaine À PART (compte d'escompte, correction de la TVA, à valider par le
   Steuerberater). D'ici là, un paiement avec escompte n'est jamais rapproché ni exporté (motif affiché).
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

## Domaine 2 — la TVA dans les écritures (2026-10-26)

Décisions D1-D7 validées par l'utilisateur ; mesure avant / après : `temp/RESULTAT_DOMAINE2_MESURE.md`,
`temp/RESULTAT_DOMAINE2.md`.

- **Nature de l'opération** : colonne « Leistungsart » (Ware / Dienstleistung…), Accounting CDM 1.2.0
  (`DocumentLine.supply_kind`). Vide, inconnue ou absente (démo 1 comprise) : UNE question par fichier
  « goods or services? », jamais présélectionnée ; jamais déduite d'une catégorie ni d'une remarque.
- **Zone du partenaire** : lue dans la liste des clients / fournisseurs exportée (état TVA retenu à l'export) ;
  pays de l'UE = liste explicite `eu_members` du tableau de source PARTNER ; hors UE = état `non_eu` (numéro
  « CHE-… MWST » jamais traité comme une USt-IdNr invalide, jamais écrit en EU-UStID).
- **Comptes** : table de DONNÉES `domain-packs/accounting/datev/skr03_vat_accounts.yaml`, « à valider par le
  Steuerberater », présélection seulement : vente UE 8125 (Ware) / 8336 (service), hors UE 8120 / 8338 ; achat
  UE 3425 / 3123, hors UE service 3125 (comptes automatiques, aucune clé de taxe).
- **Refus avec motif, jamais de question « exporter quand même »** : vente nationale à 0 % ; client ou
  fournisseur pas encore exporté ; USt-IdNr invalide ou absente d'un partenaire de l'UE ; marchandise achetée
  hors UE (TVA à l'importation) ; TVA étrangère facturée par un fournisseur étranger (taux > 0 %, règle simple,
  aucune table des taux étrangers).
- **Adresses étrangères vers DATEV** : la Postleitzahl de Debitoren/Kreditoren 5 n'admet que des chiffres (spec
  locale) : un code postal NL, PL, IE est laissé vide avec avertissement. Caractères hors cp1252 : plus tard.

## Domaine 3 — le livre de caisse (2026-10-27)

Décisions D1-D9 validées par l'utilisateur ; mesure avant / après : `temp/RESULTAT_DOMAINE3_MESURE.md`,
`temp/RESULTAT_DOMAINE3.md`.

- **Type « Cash book »** : reconnu par ses colonnes (Einnahme, Ausgabe, Kassenbestand, Buchungstext, Kategorie),
  sans IA ; colonnes propres à chaque type de fichier (`TableColumn.data_types`).
- **Écritures sans partenaire** : Accounting CDM 1.3.0, `CASH_RECEIPT` / `CASH_PAYMENT` ; recette = S, dépense =
  H sur le compte de la caisse ; aucun Debitor ni Kreditor. Le binding déclare le compte propre (`own_account`) :
  réglage `cash_account` du dossier, présélectionné à 1000, une caisse par fichier.
- **Comptes** : table de DONNÉES `domain-packs/accounting/datev/skr03_cash_accounts.yaml`, « à valider par le
  Steuerberater », présélection seulement :
  - caisse 1000, Geldtransit 1360 ;
  - Barverkauf 8400 / 8300 (automatiques), Bürobedarf 4930 (clé 0009), Porto 4910 ;
  - Bankeinzahlung / Bankabhebung 1360, sans TVA ;
  - Privatentnahme : sans TVA, compte jamais présélectionné (forme juridique) ; Bewirtung : absente (question).
  Les réponses sont mémorisées pour le dossier.
- **Contrôles** :
  - ligne d'ouverture (mots de données) = point de départ du solde ; sans elle, solde non contrôlé, et c'est dit ;
  - solde recalculé comparé à Kassenbestand (écart signalé, jamais corrigé) ;
  - solde négatif = anomalie grave EN TÊTE ; seule l'écriture qui le fait passer sous zéro attend une décision
    non présélectionnée ;
  - ordre chronologique en information ;
  - paiement par carte (mots de données dans le Buchungstext, jamais la remarque) jamais exporté ;
  - recette et dépense sur une ligne, pièce absente, TVA vide hors catégorie sans TVA : refus avec motif.
- **Buchungstext DATEV** : texte de la ligne + numéro de pièce.
- **IA** : dans « Prepare data for DATEV », une colonne de MONTANT reconnue seulement par l'IA est « à
  confirmer », jamais retenue d'office.

## Domaine 4 — la banque (2026-10-27)

Décisions D1-D9 validées par l'utilisateur ; mesure avant / après : `temp/RESULTAT_DOMAINE4_MESURE.md`,
`temp/RESULTAT_DOMAINE4.md`.

- **Lecture** : CAMT.053 lu par `defusedxml` (DTD refusée), mouvements comptabilisés (BOOK) seulement, une ligne
  par transaction d'un lot ; même table que l'export CSV de la banque (`uie_core.sources.camt`). Type « Bank
  statement » reconnu par ses colonnes, sans IA. Format bancaire déclaré : dates « TT.MM.JJ » complétées (20xx,
  jour d'abord) et listées en transformation, jamais en silence.
- **Écritures** : Accounting CDM 1.4.0, `BANK_RECEIPT` / `BANK_PAYMENT`, partenaire facultatif,
  `DocumentLine.settles` (facture réglée). Konto = compte bancaire du dossier (réglage `bank_account`,
  présélectionné à 1200, mémorisé par l'IBAN du relevé) ; paiement rapproché : Gegenkonto = compte du client ou
  du fournisseur, Belegfeld 1 = numéro de la facture réglée.
- **Registre des factures ouvertes** (D4) : factures écrites et règlements exportés du dossier, champs minimaux,
  supprimés avec le dossier ; fiche de protection des données :
  `docs/PROTECTION_DES_DONNEES_REGISTRE_DES_FACTURES.md`. Nom et IBAN du partenaire gardés avec son numéro de
  compte (D5).
- **Rapprochement par règles** : numéro de facture (mot entier) ET montant exact ou somme exacte ; numéro partagé
  départagé par l'IBAN puis le nom ; paiement partiel rapproché et signalé ; montant différent, escompte, payeur
  inconnu : jamais rapprochés (motif). Numéro inconnu mais même partenaire et même montant : « à confirmer ».
  Jamais par le montant seul.
- **Hors factures** : catégories de DONNÉES (frais 4970, dépôt d'espèces 1360, avance de TVA 1780 : table
  `skr03_bank_accounts.yaml`, à valider) ; loyer : compte demandé par bailleur, réponse reprise par l'IBAN ;
  versement des paiements par carte : jamais exporté.
- **Contrôles** : solde d'ouverture + mouvements = solde de clôture (CAMT) ; écart = anomalie grave ; export sans
  soldes : « not checkable », dit. Doublons (D8) : références de la banque différentes = deux mouvements ; sans
  référence, doublon écrit une fois sauf « Export both » ; mouvement déjà exporté par un relevé précédent :
  jamais deux fois.

