# Extension ComfyUI — envoi vers le pipeline (sans dépendance)

Alternative « zéro dépendance » à l'extraction headless : un bouton **→ Pipeline**
dans ComfyUI qui convertit le workflow ouvert (`graphToPrompt` natif, gère les
subgraphs) et le POST au bridge, qui l'auto-lie.

## Installation

1. Copier [`cortex_pipeline_sync.js`](cortex_pipeline_sync.js) dans le dossier des
   extensions front de ton install ComfyUI, par exemple :
   `…/ComfyUI/web/extensions/cortex/cortex_pipeline_sync.js`
   (ou `custom_nodes/<mon-node>/web/cortex_pipeline_sync.js`).
2. Si le bridge n'écoute pas sur `http://127.0.0.1:8000`, éditer `BRIDGE_URL` en
   tête du fichier.
3. Recharger ComfyUI (F5).

## Usage

Ouvre un workflow dans ComfyUI → clique **→ Pipeline** (en bas à droite) → donne
un nom → il est envoyé, auto-lié, et devient appelable par le pipeline. Vérifie
le mapping déduit avec `GET /v1/workflows/<nom>` ou `POST /v1/preview`.

Le bridge autorise les requêtes cross-origin depuis `localhost` (CORS) pour que
l'extension puisse poster.

> Cette voie ne nécessite ni Playwright ni Chromium. La voie **headless un-clic**
> (bouton *Extraire* dans la vue de gestion du bridge) fait la même conversion
> côté serveur, sans rien installer dans ComfyUI.
