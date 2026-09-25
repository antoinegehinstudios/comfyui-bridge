# Les réconciliants : une techno, un élément

Antoine, 2026-09-25 : « si chaque nouveauté promue par Héraldiste oblige TOUS les
workflows tenus par maestro à se mettre à jour indépendamment, c'est trop
d'effort et voué au pourrissement ; seul cet élément réconciliant se met à jour,
une fois, pour que tout workflow obtienne la nouveauté ». Puis : « lance le
chantier de standardisation et réconciliation unique par techno ».

Un réconciliant est le SEUL élément de la passerelle qui lit une source
extérieure. Les pratiques reconnues le nomment : un modèle canonique
(*Canonical Data Model*, Enterprise Integration Patterns) tenu par une couche
anticorruption (*Anti-Corruption Layer*, Domain-Driven Design) face à une
source qui publie une langue documentée (JSON Schema, JSON-LD, versionnage
sémantique).

| Réconciliant | Techno | Ce qu'il apporte | Ses emplacements |
|---|---|---|---|
| `charte` | Héraldiste | les champs `charte`, `logo` (et `logo_ou` pour une vidéo) ; les étapes `contrainte` (au début), `conformite` (après la livraison), `constat_de_la_charte` (avant le contrôle) ; sept PROMESSES | `consigne.*`, `a_poser`, `texture`, `texte.*`, `textes.*`, `references.N.*`, `logo.zone_calme` |
| `analyse` | Iconographe | l'étape `analyse` | `reperes`, `ancres`, `empreinte` |
| `culture` | Iconologue (après `analyse`) | l'étape `culture` | `culture`, `ancres`, `cle` |

## La prise de charte est de toute création

Antoine, 2026-09-25 : « j'ai demandé un template : la prise de charte doit y être présente, et à son poids
exactement comme c'est défini par le template ; les spécificités propres au workflow sont portées ailleurs,
mais le template délivre les choses qu'il porte, sans mentir, sans faux paramètre que le workflow ne sait
pas tenir ».

Le socle (`gabarits/socle.json`) EXIGE le réconciliant `charte` (`reconciliants_requis`). La charte promet
sept choses à toute création qui la prend, et c'est le réconciliant qui dit lesquelles, une fois :

| Promesse | Tenue par l'un de ces emplacements |
|---|---|
| `consigne` : les mots de style et les couleurs de la charte dans la consigne du modèle | `consigne.positif`, `consigne.style_en`, `consigne.couleurs_en` |
| `interdits` : les interdits de la charte au négatif du modèle | `consigne.negatif` |
| `logo` : le logo de la charte, posé tel quel | `a_poser` |
| `texture` : la texture de la charte, fondue sur la création | `texture` |
| `police` : la police des titres de la charte sur les textes posés | `texte.police` |
| `couleurs_du_texte` : la couleur et le fond des titres sur les textes posés | `texte.couleur`, `texte.fond` |
| `references` : le fond et les images de référence de la charte, montrés au modèle | `references.*` |

Chaque mode TIENT une promesse (il lit l'un de ses emplacements, lui-même ou par sa technique) ou la
DÉCLINE avec sa raison : `"reconciliants": {"charte": {…, "sans": {"references": "Z-Image ne prend aucune
image de référence…"}}}`. Ni l'un ni l'autre, ou les deux, est refusé au chargement
(`reconciliant.juger_les_promesses`). Une promesse déclinée est DITE : la passerelle la publie sous le champ
« Charte » (`manques_par_valeur`, verbe « Ne tient pas »), maestro l'écrit avant qu'on choisisse une charte.
Aucun faux champ : un réglage que le mode ne sait pas tenir n'est pas exposé (`logo_ou` n'existe que pour une
vidéo, qui a un carton final).

Au 2026-09-25 : Créer une image décline la police, les couleurs du texte et les références ; le visuel pour
les réseaux décline les références ; Écrire une vidéo tient tout ; Révéler une image décline la consigne, les
interdits, les couleurs du texte et les références. Révéler une image tient le logo et la texture par son
montage, et la police par son appel final, c'est-à-dire par sa technique.

## Les techniques lisent les emplacements

Une technique (`_data/techniques/*.json`) lit la source comme la chaîne : `"61.markers_json":
"$analyse.reperes"`, `"7.police": "$charte.texte.police"`. Elle est réécrite POUR la chaîne qui l'emploie
(`core.chaine.techniques_pour`, à la lecture comme à l'exécution). Une technique qui lirait le récit d'une
étape de réconciliant (`$analyse.recit.x`, `$contrainte.recit.x`) est refusée, comme une chaîne.

## Comment une chaîne s'en sert

```json
"reconciliants": {"charte": {"media": "image", "prompt": "$prompt", "livrable": "$livraison.livrable"}},
…
"1.style_impose": "$charte.consigne.style_en",
"images": "$charte.a_poser"
```

Elle **branche** le réconciliant, avec ce qu'il demande (ses `branchements`,
chacun avec son aide ; `requis` pour ceux qu'il exige). Ensuite, elle ne lit que
ses **emplacements**, `$<rôle>.<emplacement>`. Au chargement, le réconciliant est
**déplié** (`core/reconciliant.py`) : ses champs en tête, ses étapes à leur place,
ses emplacements réécrits en renvois. La chaîne qui s'exécute est une chaîne
ordinaire.

Ce qui est **refusé** au chargement, en le nommant :
- une chaîne qui rend le graphe d'une source (`image-heraldiste`…) ;
- une chaîne qui tire ses choix du menu d'une source (`charte`, `logo_charte`) ;
- une chaîne qui lit le récit d'une étape du réconciliant (`$contrainte.recit.x`) ;
- un emplacement inconnu, un branchement absent, inconnu, ou hors de ses valeurs ;
- une chaîne qui redéclare un champ ou une étape du réconciliant ;
- deux réconciliants pour une même techno.

## Une nouveauté de la source

1. **Elle entre dans un rôle existant** (un fichier de plus à poser, un mot de
   plus pour la consigne) : on la câble dans le réconciliant, une fois. Toute
   chaîne qui le branche l'a au chargement suivant. Si le nœud de la source doit
   la relayer, le nœud fait partie de l'élément : on le met à jour lui aussi.
2. **Elle demande un rôle neuf** : c'est un emplacement de plus. Chaque mode
   publie ceux qu'il ne prend pas (`presentation.reconciliants.<rôle>.non_pris`
   sur `/v1/workflows`).
3. **Tant qu'elle n'est pas câblée, elle est DITE**. Pour chaque charte,
   Héraldiste publie ses faits (colonne `faits` de son menu). Ceux que le
   réconciliant ne lit pas (`source.faits.lus`) sont publiés sur chaque choix
   (`choix[].non_servis`), et maestro les écrit sous le champ. Aujourd'hui, ce
   sont les mascottes, les éléments graphiques et la typographie du texte courant.
4. **La preuve.** `tests/test_reconciliants_sans_casse.py` compare chaque mode
   déplié à sa chaîne d'avant et déclare chaque écart, avec sa raison. Modifier
   un réconciliant, c'est y déclarer ce que chaque mode y gagne. Puis un vrai run.

## Le langage d'un réconciliant

Voir `core/reconciliant.py` : `"@nom"` (ce qui est branché — absent, l'entrée
disparaît), `{"@": "nom", "sinon": v}`, `"@si"` / `"@sauf"` (présence
conditionnelle), `{"@selon": "nom", "<valeur>": x, "@autre": y}` (variantes :
une image n'a pas de carton final). Les places d'étape sont `debut`,
`apres_livraison` et `avant_controle`. Le fichier porte `version`
(majeure.mineure.correctif) et `source` (`techno`, `graphes`, `menus`, `faits`).
