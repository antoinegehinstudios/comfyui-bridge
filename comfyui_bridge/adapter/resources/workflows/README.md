# Dossier de workflows (auto-ingestion)

Dépose ici un workflow **exporté de ComfyUI en format API** (`*.json`).

Dans ComfyUI : ouvre ton workflow → menu **Workflow → Export (API)** (active
« Dev mode » dans Settings si l'option n'apparaît pas) → enregistre le `.json`
dans ce dossier.

Au (re)démarrage du service, chaque fichier ici est **auto-découvert et
auto-lié** : le nom du fichier devient le nom du workflow appelable, les
bindings (prompt, dimensions, frames, seed…) sont déduits du graphe. Aucun code,
aucun binding à écrire à la main.

Vérifie le mapping déduit avec `POST /v1/preview` (ou `GET /v1/workflows/<nom>`)
avant de t'en servir en production. Pour un contrôle fin, déclare plutôt une
entrée explicite dans `../reconciliation.json` (elle a priorité sur ce dossier).

Tu peux aussi importer sans passer par le disque : `POST /v1/workflows`
(`{"name": "...", "workflow": <graphe API>}`) ou la CLI
`python -m comfyui_bridge add-workflow <fichier.json>`.
