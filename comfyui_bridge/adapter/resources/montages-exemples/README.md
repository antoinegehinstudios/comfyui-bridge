# Montages d'exemple — vidéo longue dirigée, et texte → vidéo

Copies de référence de ce qui vit, sur l'hôte, dans `_data/workflows/`,
`_data/blocs/` et `_data/reconciliation.local.json` (dossier non versionné :
il décrit UNE machine, ses modèles, ses chemins). Elles sont là pour que le
savoir du montage survive à la machine ; pour les employer ailleurs, les
déposer dans `_data/` et adapter les noms de modèles.

| fichier | ce que c'est |
|---|---|
| `video-h3-texte.json` | MiniMax H3 **sans image de départ**, DIRIGÉ : `DirectionDeStyle` dans le commun, `DirectionDuBloc` dans l'amorce et le maillon (chaque bloc joue son temps du récit) ; **un run du moteur par tour** (`un_run_par_tour`) — au tour 0 l'amorce (`si tour == 0`), ensuite `segment-h3-texte` (rendu seul), puis `livrer` (cadence FILM, RealESRGAN ×2, lanczos, écriture en trois tiers) ; le relais `derniere_image` (SaveImage en queue du run, LoadImage au début du suivant) porte la dernière image native d'un run à l'autre, et `blocs` nomme `livrer` pour les rangs. Boucle comptée en secondes natives (`deja` = la première image), `blocs_total` calculé par le commun. Mesuré le 2026-09-18 : envoyé entier, le montage tenait ses modèles et tous ses blocs dans un seul prompt, sans point de contrôle — le moteur est mort au bloc 3. C'est la chaîne `video-depuis-un-texte` qui le publie |
| `blocs/livrer.json`, `blocs/segment-h3-texte.json` | LIVRER un bloc rendu en trois tiers de 41 images natives, chacun attendant l'écriture du précédent (ancre du tier + nouvelles → FILM ×5 → une image sur N à phase globale → agrandissement ×2 commutable → lanczos → VHS, morceau 3·rang + tier ; offre `ecrit` et `derniere_image`) ; le maillon de rendu dirigé qui offre `ancre`, `nouvelles`, `derniere_image` |
| `blocs/jalon.json`, `blocs/jalon-guide.json` | le JALON, pour une boucle rendue d'un seul tenant : posé en tête du corps d'une boucle, il attend l'ancre ET les fichiers écrits du tour précédent (`ecrit`), et rend l'ancre sous le même nom — le moteur ne commence pas un tour avant d'avoir écrit le précédent (mesuré sans lui : 47 Go pour deux blocs à 30 i/s, le bloc 2 échantillonné avant l'écriture du bloc 1). Tous les maillons H3 et toutes les amorces offrent `ecrit` |
| `video-longue-stylee-h3.json` | MiniMax H3, blocs de 8 s, ancre = dernière image (mesuré : coutures invisibles, mouvement qui décroît) |
| `video-longue-stylee-h3-guide.json` | même chose, guide de 22 images (`MiniMaxH3AddGuide`, cœur ComfyUI ≥ 0.34) |
| `video-longue-stylee-ltx.json` | LTX-2.3, blocs chaînés par recouvrement |
| `video-longue-relais-stylee.json` | LTX-2.3 une passe, fenêtres de contexte + relais de prompt |
| `blocs/segment-h3*.json` | les maillons réutilisables (`utiliser`) |
| `reconciliation.extrait.json` | les entrées de catalogue correspondantes (liaisons `$commun.style`…) |

Tous dépendent du paquet de nœuds `comfyui-direction-de-style` (menus
`style_graphique` / `style_narratif`).
