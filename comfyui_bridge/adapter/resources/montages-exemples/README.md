# Montages d'exemple — vidéo longue dirigée

Copies de référence de ce qui vit, sur l'hôte, dans `_data/workflows/`,
`_data/blocs/` et `_data/reconciliation.local.json` (dossier non versionné :
il décrit UNE machine, ses modèles, ses chemins). Elles sont là pour que le
savoir du montage survive à la machine ; pour les employer ailleurs, les
déposer dans `_data/` et adapter les noms de modèles.

| fichier | ce que c'est |
|---|---|
| `video-longue-stylee-h3.json` | MiniMax H3, blocs de 8 s, ancre = dernière image (mesuré : coutures invisibles, mouvement qui décroît) |
| `video-longue-stylee-h3-guide.json` | même chose, guide de 22 images (`MiniMaxH3AddGuide`, cœur ComfyUI ≥ 0.34) |
| `video-longue-stylee-ltx.json` | LTX-2.3, blocs chaînés par recouvrement |
| `video-longue-relais-stylee.json` | LTX-2.3 une passe, fenêtres de contexte + relais de prompt |
| `blocs/segment-h3*.json` | les maillons réutilisables (`utiliser`) |
| `reconciliation.extrait.json` | les entrées de catalogue correspondantes (liaisons `$commun.style`…) |

Tous dépendent du paquet de nœuds `comfyui-direction-de-style` (menus
`style_graphique` / `style_narratif`).
