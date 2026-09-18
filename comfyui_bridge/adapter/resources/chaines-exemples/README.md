# Chaînes d'exemple — workflow of workflows

Copies de référence de ce qui vit, sur l'hôte, dans `_data/chaines/` et
`_data/reconciliation.local.json` (dossier non versionné : il décrit UNE
machine, ses modèles, ses chemins). Les TECHNIQUES qui tiennent les rôles d'une
chaîne ont leurs propres copies, dans `../techniques-exemples/`. Elles sont là pour que le savoir de
l'enchaînement survive à la machine ; pour les employer ailleurs, les déposer
dans `_data/chaines/` et déclarer l'entrée correspondante.

| fichier | ce que c'est |
|---|---|
| `video-revelation.json` | « Révéler une image », le seul flux publié de sa catégorie, en onze étapes qui portent les noms du travail : `analyse` (le savoir de l'image, lu au carnet posé à côté d'elle, ou relevé puis écrit) → `culture` (ce que l'œuvre raconte, lu ou relevé) → `intention` (le plan, dans la structure de récit demandée) → `plan_valide` (le plan tient-il — ses dix contrôles ET ce que la technique choisie exige de lui — avant la moindre seconde de rendu) → `deroulement` (la peinture, qui reçoit relevé et plan tels quels) → `plan_tenu` (ce que la peinture a MESURÉ contre ce que le plan promettait — un CONSTAT, écrit et jamais bloquant : l'utilisateur juge) → `raccord` → `conclusion` (0 s = aucune) → `appel` → `montage` → `controle`. Menus `style_narratif`, `fond`, `encre`, `negatif`, `ambiance`, champ `cta` |
| `video-prolongement.json` | 17 dernières images d'une vidéo → prolongement par le modèle → mesure du raccord → recollage sans le chevauchement → contrôle |
| `video-depuis-un-texte.json` | « Écrire une vidéo » : trois étapes, `rendu` → `livraison` → `constat`. Le rendu vise un MONTAGE (`video-h3-texte`, copie dans `../montages-exemples/`) dont la boucle de blocs tient la durée demandée et dont chaque bloc livre à la cadence et à la taille demandées ; la livraison recolle une seule fois (et finit l'agrandissement au-delà de 1080 de petit côté, en flux) ; la chaîne tient le CONTRAT du formulaire — six champs, bornes, rubriques, aides —, ne livre que la vidéo montée, et CONSTATE (sans jamais refuser) durée, cadence, largeur, hauteur et poids livrés face à la demande. Son entrée de catalogue est dans `../montages-exemples/reconciliation.extrait.json`, à côté du montage |

Une chaîne se déclare comme une entrée ordinaire du catalogue, avec `chaine`
au lieu de `workflow` + `bindings` :

```jsonc
"video-revelation": {
  "kind": "video",
  "chaine": "…/_data/chaines/video-revelation.json",
  "titre": "Révéler une image", "categorie": "reveler-une-image", "ordre": 1,
  "aides": { "cta": "Une phrase courte, écrite à l'encre sur les dernières secondes. Vide : aucun appel." }
}
```

Les renvois s'écrivent `$champ` (une valeur exposée) ou `$etape.cle` (un
résultat d'étape précédente) ; un renvoi vers l'aval est refusé à la lecture.
Une étape `rendre` peut régler une entrée du graphe qu'elle vise,
`"inputs": {"61.fond": "$fond"}` — l'entrée doit exister en littéral dans le
graphe. Une étape `verifier` lit sous `$etape.recit.<clé>` tout artefact
`.json` que le run a rapporté : c'est ainsi que les temps sont contrôlés sur
ce que le nœud a mesuré, avant de dépenser la suite. Une étape qui ne livre
QUE des nombres (documenter une image, écrire un plan) réussit : son artefact
`.json` est son livrable.

Un champ COMBO peut dire d'où vient sa liste au lieu de la recopier :
`"options_depuis": {"catalogue": {…}}` (les entrées publiées du catalogue) ou
`"options_depuis": {"menu": "style_narratif"}` (les valeurs d'un menu déclaré,
lui-même souvent la projection d'un fichier que tient un paquet de nœuds).
Recopiée, une liste vieillit au premier ajout.

Ce qui est AGNOSTIQUE est appelé, jamais ancré : la documentation d'une image
(`image-iconographe`, tenue par le service Iconographe et sa bibliothèque), la
structure du récit (le catalogue des structures narratives) et l'appel final
(`video-appel-final`) valent pour n'importe quel flux — la chaîne les appelle, un
autre flux le peut aussi, et la documentation déjà écrite n'est pas repayée.

Les workflows sont cités par leur NOM au catalogue : ces fichiers ne portent
aucun chemin de cette machine.
