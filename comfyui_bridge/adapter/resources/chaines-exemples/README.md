# Chaînes d'exemple — workflow of workflows

Copies de référence de ce qui vit, sur l'hôte, dans `_data/chaines/` et
`_data/reconciliation.local.json` (dossier non versionné : il décrit UNE
machine, ses modèles, ses chemins). Elles sont là pour que le savoir de
l'enchaînement survive à la machine ; pour les employer ailleurs, les déposer
dans `_data/chaines/` et déclarer l'entrée correspondante.

| fichier | ce que c'est |
|---|---|
| `video-revelation.json` | « Révéler une image », le seul flux publié de sa catégorie : révélation cinématique (au moins la durée demandée, jusqu'à 79 s) → contrôle des quatre temps sur le récit écrit par le nœud (hook nommé et vu à 2,5 s, retenue, climax gardé pour la fin et tenu, étapes qui se suivent) → queue de 50 images → fermeture « la page se referme » puis appel final à l'encre (facultatif) → recollage → contrôle de durée et de poids. Menus `fond` et `encre`, champ `cta` |
| `video-prolongement.json` | 17 dernières images d'une vidéo → prolongement par le modèle → mesure du raccord → recollage sans le chevauchement → contrôle |

Une chaîne se déclare comme une entrée ordinaire du catalogue, avec `chaine`
au lieu de `workflow` + `bindings` :

```jsonc
"video-revelation": {
  "kind": "video",
  "chaine": "…/_data/chaines/video-revelation.json",
  "titre": "Révéler une image", "categorie": "reveler-une-image", "ordre": 1,
  "aides": { "cta": "Une phrase courte, écrite à l'encre sur la page refermée. Vide : aucun appel." }
}
```

Les renvois s'écrivent `$champ` (une valeur exposée) ou `$etape.cle` (un
résultat d'étape précédente) ; un renvoi vers l'aval est refusé à la lecture.
Une étape `rendre` peut régler une entrée du graphe qu'elle vise,
`"inputs": {"61.fond": "$fond"}` — l'entrée doit exister en littéral dans le
graphe. Une étape `verifier` lit sous `$etape.recit.<clé>` tout artefact
`.json` que le run a rapporté : c'est ainsi que les temps sont contrôlés sur
ce que le nœud a mesuré, avant de dépenser la fermeture.
Les workflows sont cités par leur NOM au catalogue : ces fichiers ne portent
aucun chemin de cette machine.
