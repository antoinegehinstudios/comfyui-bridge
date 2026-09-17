# Montages d'exemple — vidéo longue dirigée, et texte → vidéo

Copies de référence de ce qui vit, sur l'hôte, dans `_data/workflows/`,
`_data/blocs/` et `_data/reconciliation.local.json` (dossier non versionné :
il décrit UNE machine, ses modèles, ses chemins). Elles sont là pour que le
savoir du montage survive à la machine ; pour les employer ailleurs, les
déposer dans `_data/` et adapter les noms de modèles.

| fichier | ce que c'est |
|---|---|
| `video-h3-texte.json` | MiniMax H3 **sans image de départ** : le premier bloc naît de la seule consigne écrite (le nœud sans `first_frame` est un texte → vidéo), les suivants sont le maillon `blocs/segment-h3-texte.json`, qui reprend la dernière image native du précédent et LIVRE chaque morceau à la cadence (FILM ×5 puis une image sur N, phase calculée sur la grille globale) et à la taille (lanczos, jusqu'à 1080 de petit côté) demandées. Blocs de 124 images (5,2 s à 24 i/s, la borne basse de la plage d'entraînement du modèle), rendu natif à 0,6 mégapixel dans la proportion demandée, boucle comptée en secondes natives. C'est la chaîne `video-depuis-un-texte` qui le publie |
| `video-longue-stylee-h3.json` | MiniMax H3, blocs de 8 s, ancre = dernière image (mesuré : coutures invisibles, mouvement qui décroît) |
| `video-longue-stylee-h3-guide.json` | même chose, guide de 22 images (`MiniMaxH3AddGuide`, cœur ComfyUI ≥ 0.34) |
| `video-longue-stylee-ltx.json` | LTX-2.3, blocs chaînés par recouvrement |
| `video-longue-relais-stylee.json` | LTX-2.3 une passe, fenêtres de contexte + relais de prompt |
| `blocs/segment-h3*.json` | les maillons réutilisables (`utiliser`) |
| `reconciliation.extrait.json` | les entrées de catalogue correspondantes (liaisons `$commun.style`…) |

Tous dépendent du paquet de nœuds `comfyui-direction-de-style` (menus
`style_graphique` / `style_narratif`).
