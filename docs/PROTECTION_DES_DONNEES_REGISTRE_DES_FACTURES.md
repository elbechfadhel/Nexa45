# Registre des factures ouvertes et identité des partenaires — protection des données

Fiche pour la documentation de protection des données (RGPD / DSGVO) de Nexa45. Parcours concerné : « Prepare
data for DATEV » (source FICHIER, dossiers des cabinets). Décision D4 du domaine 4 (banque), 2026-10-27.

## Pourquoi ces données sont gardées

Pour préparer un relevé bancaire, Nexa45 rapproche chaque paiement d'une facture déjà exportée pour le même dossier.
Ce rapprochement est fait par le numéro de facture écrit dans le motif du virement (Verwendungszweck) et par le
montant. Pour y arriver, Nexa45 doit savoir, après l'export des factures :
- quelles factures ont été exportées ;
- à quel montant ;
- pour quel partenaire (client ou fournisseur) ;
- ce qu'il en reste à payer.

Avant le domaine 4, Nexa45 ne gardait d'une facture exportée qu'une **empreinte** (aucun montant, aucun
partenaire), ce qui rendait le rapprochement impossible. Le registre ci-dessous est le minimum nécessaire. C'est
l'équivalent du livre des postes ouverts (Offene-Posten-Buch) que tient toute comptabilité.

## Ce qui est gardé

### Registre des factures ouvertes (`export_open_items`)
Une ligne par facture écrite dans un fichier DATEV, pour un dossier :

| Champ | Contenu | Pourquoi |
|---|---|---|
| organisation, dossier (`client_key`), format cible | portée | une facture n'est jamais vue d'un autre dossier ni d'un autre cabinet |
| `document_ref` | référence technique de la facture dans le fichier source | identifier la facture sans ambiguïté |
| `number` | numéro de la facture | le retrouver dans le motif d'un virement ; Belegfeld 1 du paiement (lettrage DATEV) |
| `role` | client ou fournisseur | un numéro de facture d'achat n'est unique que chez son fournisseur |
| `partner_ref` | numéro du client ou du fournisseur dans la source | relier la facture au compte attribué |
| `partner_name` | nom écrit sur la facture | reconnaître le payeur d'un virement |
| `amount`, `currency` | montant brut signé (avoir négatif), devise | paiement exact, partiel ou différent |
| `document_date` | date de la facture | ordre des factures d'un même partenaire |
| `export_run_id` | fichier qui a écrit la facture | traçabilité |

Il n'y a ni ligne de facture, ni article, ni adresse, ni taux de TVA, ni remarque.

### Règlements (`export_open_item_settlements`)
Une ligne par paiement rapproché et écrit dans un fichier : référence technique du mouvement bancaire, montant
réglé, date. Le montant ouvert d'une facture est son montant moins ses règlements.

### Identité du partenaire (`export_account_assignments.partner_name`, `iban`)
À l'export de la liste des clients ou des fournisseurs, Nexa45 garde déjà le compte attribué et l'état du numéro
de TVA. Il y ajoute :
- le **nom** du partenaire ;
- son **IBAN**, seulement s'il est valide (contrôle mod 97).

Ces deux données servent à reconnaître le payeur d'un relevé, par exemple pour départager deux factures « 1001 »
de deux fournisseurs différents (décision D5). L'IBAN n'est recopié nulle part ailleurs : le registre des
factures le lit à cet endroit.

## Ce qui n'est PAS gardé

- Le contenu des fichiers, que ce soient ceux du client ou les fichiers DATEV produits.
- Les mouvements bancaires eux-mêmes. D'un relevé, seule une empreinte de chaque mouvement écrit est gardée
  (journal d'export), avec, pour un paiement rapproché, son règlement ci-dessus.
- La contrepartie d'un mouvement non rapproché : ni nom, ni IBAN, ni motif.
- Aucune donnée n'est envoyée à un modèle d'IA pour le rapprochement : il se fait par des règles.

## Durée et suppression

- Les données restent tant que le dossier existe. Elles servent au rapprochement des relevés suivants.
- Elles sont supprimées :
  - avec la remise à zéro du dossier (CLI : état d'export d'un dossier, `reset_file_exports`) ;
  - avec l'organisation (suppression en cascade).
- Une facture n'est inscrite qu'une fois. Un nouvel export du même fichier n'ajoute rien.

## Accès

- Les données sont filtrées par organisation et par dossier (contexte du cabinet connecté) : un dossier n'en voit
  jamais un autre.
- Elles ne sont exposées par aucune API sous forme de liste. Elles ne servent qu'à la préparation d'un relevé
  bancaire du même dossier.
