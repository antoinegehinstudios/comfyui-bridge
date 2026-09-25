# Le gabarit « création » — comment on ajoute un cas de création

> **Depuis le 2026-09-25**, `creation` ÉTEND le socle de toute création publiée
> (`resources/gabarits/socle.json`), et la couche charte n'est plus écrite dans la
> chaîne : elle vient du RÉCONCILIANT `charte` (`resources/reconciliants/charte.json`,
> mode d'emploi `resources/reconciliants/LIRE-MOI.md`). La chaîne le branche —
> `"reconciliants": {"charte": {"media": "image", "prompt": "$prompt",
> "livrable": "$livraison.livrable", …}}` — et ne lit que ses emplacements
> (`$charte.consigne.style_en`, `$charte.a_poser`, `$charte.texture`…). Les étapes
> `contrainte`, `conformite` et `constat_de_la_charte`, et les champs `charte` et
> `logo`, sont posés par lui au chargement : le tableau ci-dessous décrit la chaîne
> DÉPLIÉE, celle qui s'exécute. La charte y promet sept choses : un nouveau cas de création tient
> chacune (en lisant l'un de ses emplacements) ou la décline avec sa raison, dans `"sans"` — voir
> `resources/reconciliants/LIRE-MOI.md`.

Antoine, 2026-09-24 : « standardise par un template un cas de création, en
manifestant le template à suivre ». Ce fichier est le mode d'emploi ; le patron
lui-même est `resources/gabarits/creation.json`, et il est **vérifié** : une
chaîne qui écrit `"gabarit": "creation"` et s'en écarte est refusée à la lecture
du catalogue, en nommant chaque écart. Le cas de référence est
`image-creation.json` (« Créer une image ») ; `image-visuel-social.json` en est
la variante à message.

## Ce qu'un cas de création porte, dans l'ordre

| Étape | Genre | Ce qu'elle fait | Sous |
|---|---|---|---|
| `contrainte` | `rendre` | lit la CHARTE chez Héraldiste (graphe `image-heraldiste`) : mots de style, palette en anglais, interdits, police et couleurs de titre, logo, texture — un `sinon` rend les mêmes clés à vide | `charte` ≠ aucune |
| `direction` | `rendre` | compose la consigne (`DirectionDImage`) : le style enveloppe le sujet, cadrage / lumière / palette / ambiance ajoutent leurs mots, ce que la charte impose prend sa place ; le récit dit mot pour mot ce que chaque paramètre a mis ; la taille native de rendu | — |
| `rendu` | `rendre` | le RÔLE `image`, tenu par la technique choisie (`_data/techniques/*.json`) — jamais un graphe nommé | — |
| `livraison` | `composer` | l'image à la taille exacte demandée ; textes, logo, texture avec les clés du recollage | — |
| `conformite` | `rendre` | le constat d'Héraldiste sur l'IMAGE livrée (graphe `video-heraldiste-conformite`, `1.video` = le chemin, `1.images` = 1) | `charte` ≠ aucune |
| `constat` | `constater` | taille tenue, pas d'agrandissement, sujet dans la consigne | — |
| *(libre)* | `constater` | un constat de plus, propre au cas (le message d'un visuel social) | — |
| `constat_de_la_charte` | `constater` | appliquée, palette dans la consigne et tenue, logo posé / intact / à sa taille, règles mesurables, et ce que le modèle ne sait pas honorer | `charte` ≠ aucune |
| `controle` | `verifier` | le fichier pèse — le seul refus | — |

Le livrable est `$livraison.livrable`.

## Les champs

1. **`charte` en premier**, rubrique `charte` : ce qu'elle impose pèse sur tout
   ce qui suit. Ses aides disent ce qui est imposé et ce qui ne peut pas l'être.
2. Les champs qu'elle **impose** portent `impose_par: {"champ": "charte", "sauf": ["aucune"]}`
   — au moins `palette` ; pour un visuel à message : `police`, `couleur_texte`,
   `bandeau`. Un lanceur les GRISE (la valeur imposée se lit au récit) et ne les
   envoie pas ; il ne les cache pas. `selon` est autre chose : un réglage qui
   n'existe pas sous une autre technique (`cfg`, `negatif` sous la rapide).
3. Un champ **`technique`** (`options_depuis: {"techniques": true}`) : les
   techniques de ce plan sont celles qui tiennent le rôle `image`.
4. Chaque champ porte `categorie` (vocabulaire `categories_de_champs` de la
   réconciliation : `charte`, `sujet`, `texte`, `style`, `technique`, `format`,
   `avance`) et `aide`.
5. **Aucun fantôme** : un réglage exposé pèse — le récit de la direction cite ses
   mots (`mots_par_parametre`) ; ce qu'une technique ne lit pas n'est pas exposé
   sous elle ; ce qu'une charte supplante est imposé et dit.

## Ce qu'il faut écrire, et où

| Quoi | Où |
|---|---|
| la chaîne, avec `"gabarit": "creation"` | `_data/chaines/<nom>.json` + sa copie `resources/chaines-exemples/<nom>.json` |
| sa vitrine | `_data/reconciliation.local.json` : `kind: image`, `chaine`, `titre`, `description`, `categorie`, `ordre` |
| un style ou un réglage de plus | les catalogues `styles/*.json` du paquet direction-de-style — une entrée éprouvée par un rendu |
| une technique de plus | `_data/techniques/<nom>.json` (+ copie) : rôle `image`, ses réglages, `negatif.applique` |
| le témoin | `tests/test_socle_<nom>.py` : le plan, les champs, les constats, la copie jumelle, ce qui existe sur ce poste |
| la preuve | un vrai run par mode (demandé = constaté), images regardées |

Relancer la passerelle après toute édition de chaîne, de technique ou de
réconciliation (lues au démarrage) ; le moteur après tout changement de nœud.
