# Chaînes d'exemple — workflow of workflows

Copies de référence de ce qui vit, sur l'hôte, dans `_data/chaines/` et
`_data/reconciliation.local.json` (dossier non versionné : il décrit UNE
machine, ses modèles, ses chemins). Elles sont là pour que le savoir de
l'enchaînement survive à la machine ; pour les employer ailleurs, les déposer
dans `_data/chaines/` et déclarer l'entrée correspondante.

| fichier | ce que c'est |
|---|---|
| `video-revelation-podcast.json` | révélation cinématique (50 s) → queue de 50 images → fermeture « la page se referme » (11 s) → recollage → contrôle de durée et de poids |
| `video-revelation-poussee.json` | révélation au mode CHOISI (`options_depuis` : le catalogue remplit la liste) → poussée caméra sur la même image → recollage → contrôle |
| `video-prolongement.json` | 17 dernières images d'une vidéo → prolongement par le modèle → mesure du raccord → recollage sans le chevauchement → contrôle |

Une chaîne se déclare comme une entrée ordinaire du catalogue, avec `chaine`
au lieu de `workflow` + `bindings` :

```jsonc
"video-revelation-podcast": {
  "kind": "video",
  "chaine": "…/_data/chaines/video-revelation-podcast.json",
  "titre": "Révélation pour podcast", "categorie": "reveler-une-image", "ordre": 1
}
```

Les renvois s'écrivent `$champ` (une valeur exposée) ou `$etape.cle` (un
résultat d'étape précédente) ; un renvoi vers l'aval est refusé à la lecture.
Les workflows sont cités par leur NOM au catalogue : ces fichiers ne portent
aucun chemin de cette machine.
