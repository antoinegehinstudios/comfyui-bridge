# ComfyUI Bridge

[![tests](https://github.com/antoinegehinstudios/comfyui-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/antoinegehinstudios/comfyui-bridge/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![RFC 7807](https://img.shields.io/badge/erreurs-RFC%207807-informational)](https://datatracker.ietf.org/doc/html/rfc7807)
[![Licence Apache 2.0](https://img.shields.io/badge/licence-Apache%202.0-green)](LICENSE)

Service backend (FastAPI) + adaptateur CLI qui pilotent **ComfyUI de façon
découplée**. On envoie une *intention de rendu* déclarative (prompt, contraintes
multiples, durée, format, FPS) ; le service la résout, la **réconcilie avec les
problèmes déjà rencontrés** sur cet hôte (*Hermes*, mode local), pilote ComfyUI,
et **livre le média produit** — ou l'erreur, enregistrée pour ne pas la revivre.
Toute erreur sort au format **RFC 7807** (`application/problem+json`).

Conçu comme un *socle* autonome, prêt à être intégré dans une architecture de
type **Cortex** (à la manière d'`agent-run-service`).

## Principe : architecture hexagonale

La logique métier de haut niveau **ignore tout de ComfyUI**. Elle ne parle qu'en
intentions et en *ports*. Tout ce qui est spécifique à ComfyUI (ids de nœuds,
noms d'entrées, graphe du workflow) vit derrière un adaptateur, piloté par un
**fichier de réconciliation externe** (`adapter/resources/reconciliation.json`).

```
                 REST (FastAPI)          CLI opérateur
                        \                    /
                         \                  /
                    ┌─────────────────────────────┐
   intention  ───▶  │   core / Orchestrator        │   ◀── aucune mention
                    │   (domaine pur, agnostique)   │       de ComfyUI ici
                    └───────┬───────────┬───────────┘
                       port │           │ port
              RenderBackend │           │ Reconciler + ExecutionJournal
                            ▼           ▼
                 ┌────────────────┐   ┌──────────────────────┐
                 │ adapter        │   │ hermes               │
                 │ ComfyUIHttpBack│   │ HermesReconciler     │
                 │ + reconciliation│  │ + ProblemRegistry    │
                 │ + injector      │  │   (SQLite local)     │
                 └───────┬────────┘   └──────────────────────┘
                         ▼
              API HTTP ComfyUI (ou CLI `comfy run`)
```

Preuve du découplage : **deux points d'entrée** (HTTP et CLI) attaquent le
**même** `Orchestrator`, sans dupliquer une seule règle métier.

## Arborescence

```
comfyui_bridge/
  config.py              # réglages (env), frozen dataclass
  container.py           # composition root (câble les implémentations)
  core/                  # DOMAINE PUR — ne dépend de rien d'externe
    intention.py         #   RenderIntent, Constraint (déclaratif)
    plan.py              #   ExecutionPlan, Reconciliation, Artifact
    ports.py             #   RenderBackend / Reconciler / ExecutionJournal
    orchestrator.py      #   le flux de bout en bout
    jobs.py              #   registre de jobs (async → 202 + polling)
    cost.py, problems.py, errors.py
  adapter/               # SEUL endroit qui connaît ComfyUI
    catalog.py           #   charge le fichier de réconciliation
    autobind.py          #   déduit les bindings d'un graphe exporté
    injector.py          #   injecte les params dans le graphe JSON
    comfy_http.py        #   pilote l'API HTTP ComfyUI (réel)
    comfy_cli.py         #   pilote la CLI (ou manifest en dry-run)
    extractor.py         #   extraction un-clic (UI→API, headless)
    resources/reconciliation.json, workflows/
  browser/               # navigateur temporaire RÉUTILISABLE (générique)
  hermes/                # réconciliation contre les problèmes connus
    registry.py          #   registre local des problèmes (SQLite scopé)
    reconciler.py        #   accepte / refuse en citant le passé
  api/                   # surface FastAPI (RFC 7807)
    main.py, schemas.py, problems.py
  cli.py, __main__.py    # adaptateur CLI opérateur
tests/                   # test_injector, test_reconciler, test_api
scripts/smoke.py         # test de bout en bout SANS dépendance
```

## Ce qu'il faut pour que ça tourne

**Un serveur ComfyUI** — c'est lui qui calcule ; la passerelle ne fait que le
piloter par son API HTTP. Elle s'attache à celui qui répond déjà (ComfyUI
Desktop, un service…) ; pour qu'elle le *lance* elle-même, déclarez son chemin
dans `_data/engines.local.json` (voir plus bas). Les **modèles** dont un workflow
a besoin doivent être installés dans ComfyUI : la passerelle ne les télécharge
pas, elle relaie ce que ComfyUI répond quand il en manque un.

**Python 3.10 ou plus.**

```bash
pip install -r requirements.txt
```

| paquet | rôle | son absence |
|---|---|---|
| `fastapi`, `uvicorn`, `pydantic` | l'API REST et la console | rien ne démarre |
| `pillow` | l'**élément neutre** envoyé quand aucun média n'est fourni ; la mesure des images livrées | le workflow tournerait sur le contenu qu'il embarque — la fuite même que ce mécanisme empêche |
| `websocket-client` | la progression réelle, telle que ComfyUI la diffuse | fonctionne, mais se rabat sur l'interrogation de l'historique : plus d'avancement pas à pas, et il le dit |
| `playwright` *(optionnel)* | l'extraction **un-clic** d'un workflow sauvé, par le `graphToPrompt` de ComfyUI | l'import se fait à la main : *Workflow → Export (API)* dans ComfyUI, puis « Importer » |

```bash
pip install playwright && playwright install chromium   # pour l'extraction un-clic
pip install -r requirements-dev.txt                     # pour lancer les tests
```

Rien d'autre : l'historique des runs passe par `sqlite3` (bibliothèque standard),
et aucun service tiers n'est appelé.

## Ce qui est livré, ce qui appartient à la machine

Le paquet ne décrit **aucune machine** : ni chemin d'installation, ni workflow
extrait d'ailleurs, ni identifiant. Tout ce qui décrit UN poste vit dans le
dossier de données (`_data/`, jamais versionné) et se fusionne par-dessus les
ressources livrées :

| ressource livrée | surcharge locale | ce qu'elle déclare |
|---|---|---|
| `resources/engines.json` (profil *attach* seul) | `_data/engines.local.json` | où ComfyUI est installé, comment le lancer |
| `resources/reconciliation.json` (exemples neutres) | `_data/reconciliation.local.json` | les workflows propres à ce poste |
| — | `_data/workflows/` | les graphes extraits d'ICI, ré-extractibles à tout moment |

Une entrée locale de même nom remplace celle livrée ; ce que la surcharge ne dit
pas reste inchangé. Un fichier local illisible est signalé, jamais avalé.
Pour lancer ComfyUI depuis la passerelle, copiez
`resources/engines.local.exemple.json` en `_data/engines.local.json` et mettez
vos chemins. Un test du dépôt vérifie qu'aucune ressource livrée ne nomme de
machine.

## Démarrage rapide

### Raccourci bureau (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/installer-raccourci.ps1
```

Pose « **ComfyUI Bridge** » sur le Bureau. Un double-clic lance la passerelle si
elle dort, attend qu'elle réponde vraiment, puis ouvre la console. Si elle
tourne déjà, il ouvre simplement la console — jamais un second service. Le
script est rejouable : le relancer après un déplacement du projet suffit.

Le moteur ComfyUI, lui, est amené par la passerelle (elle se raccroche à celui
qui répond, sinon elle le lance selon le profil actif). S'il est éteint, la
console affiche un bouton **▶ Démarrer le moteur** — il n'apparaît QUE dans ce
cas, pour ne jamais inviter à lancer un second serveur sur un port occupé.

### À la main

Par défaut le backend est **`http`** : le service pilote un vrai serveur ComfyUI
et livre le média produit. Pour travailler hors-ligne (aucun ComfyUI requis),
passer `COMFY_BACKEND=cli` — le dry-run écrit alors un *manifest* de plan,
explicitement marqué comme simulation.

```bash
# 1. Test de bout en bout, zéro dépendance (ni GPU, ni FastAPI, ni pytest)
python scripts/smoke.py

# 2. Le serveur REST + console web
pip install -r requirements.txt
python -m comfyui_bridge serve --port 8077
#   Console (UI)  : http://127.0.0.1:8077/ui
#   Docs OpenAPI  : http://127.0.0.1:8077/docs

# 3. La CLI opérateur (même moteur que le REST)
python -m comfyui_bridge render --prompt "a lone astronaut on a red dune" --steps 24
python -m comfyui_bridge render --prompt "clip" --kind video --duration 3 --fps 8
python -m comfyui_bridge render --prompt "big" --width 1024 --height 1024 --batch 4
python -m comfyui_bridge workflows
```

## Endpoints

| Méthode | Chemin                         | Rôle                                            |
|---------|--------------------------------|-------------------------------------------------|
| POST    | `/v1/render`                   | Soumet une intention → `202` + `Location` du job |
| GET/PUT/DELETE | `/v1/workflows/{name}/apercu` | **L'aperçu animé** d'un mode (six secondes qui résument toute la vidéo ; WebP animé, ~270 Ko, ou GIF si l'encodeur manque) : servi, désigné depuis un job livré ou un fichier du dossier de sortie (`{"job_id"}` / `{"path"}`), retiré ; `presentation.apercu_url` l'annonce quand il existe |
| GET     | `/v1/jobs`                     | **Les runs**, du plus récent au plus ancien (mémoire + persistés) ; les sous-jobs d'une chaîne sur demande (`?enfants=1`) |
| GET     | `/v1/jobs/{id}`                | État du job + artefacts + **journal (logs)** + `etapes` d'une chaîne |
| POST    | `/v1/jobs/{id}/rejouer`        | **Rejouer** un run, à l'identique ou avec `{"reglages": {…}}` |
| GET     | `/v1/jobs/{id}/events`         | **Progression live (SSE)** jusqu'à l'état final  |
| GET     | `/v1/jobs/{id}/artifacts`      | Artefacts seuls (avec `url` servable)            |
| GET     | `/artifacts/…`                 | **Livraison** : fichiers de sortie servis (aperçu direct) |
| POST    | `/v1/preview`                  | Graphe ComfyUI injecté (du workflow choisi), sans exécuter |
| GET     | `/v1/workflows`                | **Fichier de réconciliation** : workflows appelables par nom |
| POST    | `/v1/workflows`                | **Importer** un workflow (Export API ComfyUI) → auto-lié, appelable |
| GET     | `/v1/workflows/{name}`         | Détail : bindings + **dépendances** (modèles, nœuds) + provenance |
| GET     | `/v1/comfyui/workflows`        | Workflows **sauvés dans ComfyUI** + statut (à-extraire / extrait / **MAJ dispo**) |
| POST    | `/v1/comfyui/workflows/{name}/extract` | **Extraction un-clic** (headless graphToPrompt → auto-lié) |
| GET     | `/v1/hermes/runs`              | Ce que Hermes sait : runs réels de cet hôte      |
| GET     | `/v1/hermes/problems`          | Problèmes connus d'un workflow                   |
| GET     | `/v1/backend`                  | **Test de connexion** : type de backend + probe ComfyUI |
| GET     | `/v1/engine/queue`             | **La queue de ComfyUI**, relayée (pas de seconde file) |
| GET     | `/ui`                          | Console web (HTML/JS vanilla, sans build ni CDN) |
| GET     | `/healthz`, `/`                | Santé / info                                     |
| GET     | `/docs`, `/redoc`, `/openapi.json` | Documentation OpenAPI générée                |

### Exemple — soumettre une intention

```bash
curl -X POST http://127.0.0.1:8077/v1/render -H 'content-type: application/json' -d '{
  "prompt": "a lone astronaut on a red dune, cinematic",
  "kind": "video",
  "width": 768, "height": 768,
  "duration_s": 3, "fps": 8,
  "steps": 24,
  "constraints": [ { "key": "width", "op": "lte", "value": 1024 } ]
}'
```

### Exemple — erreur RFC 7807 (refus sur problème connu)

Une configuration qui a déjà échoué ici renvoie `422` en
`application/problem+json`, **en citant le problème passé** :

```json
{
  "type": "https://cortex/problems/reconciliation-refused",
  "title": "Reconciliation refused: known problem",
  "status": 422,
  "detail": "déjà rencontré ici : oom — CUDA error: out of memory",
  "instance": "/v1/render",
  "problem": "oom",
  "config": "1024x1024x1",
  "workflow": "sd15-txt2img",
  "host": "localhost"
}
```

`problem`, `config`, `workflow`, `host` sont des *extension members* RFC 7807 :
le contexte voyage avec l'erreur.

## Fichier de réconciliation (catalogue de workflows)

Les workflows appelables sont **déclarés** dans
[`resources/reconciliation.json`](comfyui_bridge/adapter/resources/reconciliation.json),
keyés par nom. Un pipeline choisit un workflow par nom (`"workflow": "..."` dans
l'intention) ; les champs omis héritent des `defaults` déclarés, et `limits`
(ex. `max_signature`) alimente Hermes.

```jsonc
{
  "default": "sd15-txt2img",
  "workflows": {
    "z-image-turbo": {
      "kind": "image",
      "workflow": "workflow_zimage.json",
      "defaults": { "width": 1024, "height": 1024, "steps": 8, "cfg": 1.0 },
      "limits":   { "max_signature": 1048576 },
      "bindings": { "prompt": { "node": "27", "input": "text" }, ... }
    }
  }
}
```

Ajouter un modèle = ajouter une entrée (workflow + bindings + defaults), **aucun
code à changer**. Pointe `COMFY_CATALOG` vers ton propre fichier pour un autre
catalogue.

```bash
python -m comfyui_bridge workflows                              # lister
python -m comfyui_bridge render --workflow z-image-turbo --prompt "a knight"
curl -s -X POST :8000/v1/render -d '{"workflow":"z-image-turbo","prompt":"..."}'
```

Dans l'UI : un sélecteur **Workflow** (peuplé depuis `/v1/workflows`) choisit
l'entrée et pré-remplit ses defaults.

### Auto-ingestion (sans écrire de bindings)

Tu n'as pas à écrire les bindings à la main, ni à me les demander au coup par
coup. Sauve ton workflow dans ComfyUI en **Export (API)**, puis :

- **dépose** le `.json` dans `resources/workflows/` → auto-découvert au démarrage ;
- ou **importe-le** : UI (bouton *Importer*), `POST /v1/workflows`, ou
  `python -m comfyui_bridge add-workflow <fichier.json>`.

Le service **déduit les bindings du graphe** (`adapter/autobind.py`) : `prompt` =
le `CLIPTextEncode` qui alimente le positif du sampler (tracé), `width`/`height` =
le nœud de latent, `latent_batch` = `length` (vidéo) ou `batch_size` (image),
`seed`/`steps`/`cfg`, `filename_prefix`, et **chaque entrée média** du graphe
(voir plus bas). Ce qui n'est pas identifié reste non lié
(le workflow garde sa valeur). **Vérifie le mapping déduit** avec `POST /v1/preview`
ou `GET /v1/workflows/<nom>` avant de t'en servir ; pour un contrôle fin, déclare
une entrée explicite dans `reconciliation.json` (elle a priorité).

### Les pièces jointes : autant que le workflow en a

Un workflow n'a pas *une* image d'entrée. Il en a parfois deux (une première et
une dernière image), quatre (les vues d'un assemblage, les angles d'un
photogrammétrique), ou une image **et** une voix. La découverte les nomme
**toutes**, dans l'ordre des nœuds :

| ce que le graphe porte | ce que la passerelle annonce |
|---|---|
| 1 `LoadImage` | `image` |
| 4 `LoadImage` | `image`, `image_2`, `image_3`, `image_4` |
| `LoadImage` + `LoadAudio` | `image`, `audio` |
| `LoadImage` + `LoadVideo` | `image`, `video` |
| `Load3D` | `3d` |

La première de chaque catégorie garde son nom nu — un workflow à une seule image
s'appelle exactement comme avant. Les nœuds reconnus sont ceux que ComfyUI
déclare lui-même téléversables (drapeaux `image_upload` / `video_upload` /
`audio_upload` / `file_upload` dans `/object_info`) ; la table est dans
[`adapter/media_inputs.py`](comfyui_bridge/adapter/media_inputs.py), et
`GET /v1/workflows/<nom>/io` liste sous `media_inputs_unbound` toute entrée que
le moteur déclare téléversable et que cette table ne connaît pas encore — ce qui
manque se dit plutôt que de disparaître.

Le nom du nœud écrit par l'auteur dans ComfyUI (« Load Last Frame ») accompagne
chaque entrée : c'est lui qui distingue deux images, pas `image_2`.

**Déposer un fichier**, puis le citer :

```bash
# 1. le fichier part chez ComfyUI ; le moteur répond sous quel nom il le connaît
curl -X POST http://127.0.0.1:8077/v1/inputs/media -F 'file=@fin.png' -F 'param=image_2'
# {"name":"fin.png","bytes":51234,"subfolder":""}

# 2. l'intention cite ce nom, sous l'entrée que le workflow annonce
curl -X POST http://127.0.0.1:8077/v1/render -H 'content-type: application/json' -d '{
  "workflow": "ltx-flf2v",
  "prompt": "la voiture se retourne",
  "media": { "image": "debut.png", "image_2": "fin.png" }
}'
```

`param` ne sert qu'à savoir **où** le moteur range cette catégorie : images,
vidéos et sons vivent à la racine du dossier d'entrée, un modèle 3D vit dans
`3d/` et se cite `3d/<nom>` — c'est le seul nom que `Load3D` accepte. La
passerelle rend toujours le nom **citable par un graphe**, sous-dossier compris.

Les noms annoncés sont aussi acceptés **à la racine** du corps
(`{"image_2": "fin.png"}`) : ce que `/v1/workflows` annonce, `/v1/render` le
prend. Un nom qui n'est pas une entrée média reste refusé en `422` — une faute
de frappe est une erreur d'appelant, elle se dit.

Une entrée laissée vide reçoit un **élément neutre** quand sa catégorie en a un
(une image blanche), sinon le workflow tourne sur son propre contenu — et le
journal du run le dit, entrée par entrée, avec le nom du fichier concerné.

### Extraction un-clic (headless) et extension ComfyUI

Deux voies pour transformer un workflow **sauvé dans ComfyUI** (format UI, avec
subgraphs) en entrée appelable, sans Export API manuel — toutes deux via le
`graphToPrompt` *natif* de ComfyUI (la conversion de référence, jamais réécrite) :

- **Un-clic headless** (bouton *Extraire* dans la vue de gestion) :
  `POST /v1/comfyui/workflows/{name}/extract` lit le workflow sauvé, lance un
  **navigateur temporaire headless**, exécute graphToPrompt, auto-lie, enregistre.
  Requiert `playwright` : `pip install -e '.[browser]' && playwright install chromium`.
  Le module [`browser/`](comfyui_bridge/browser/headless.py) est **générique et
  réutilisable** — toute fonction ayant besoin d'un navigateur jetable s'appuie
  dessus (`evaluate_on(url, js)`), il ignore tout de ComfyUI.
- **Extension ComfyUI** (sans dépendance) : un bouton *→ Pipeline* dans ComfyUI
  pousse le workflow ouvert au bridge. Voir [`extensions/comfyui/`](extensions/comfyui/README.md).

L'extraction pose la **provenance** (source + hash) → `GET /v1/comfyui/workflows`
signale ensuite `update-available` si la source ComfyUI change. L'auto-binding est
best-effort : sur des workflows très atypiques certains paramètres (parfois le
prompt) restent non liés — le preview le montre, complète alors via une entrée
explicite dans `reconciliation.json`.

### Manifeste de réconciliation + dépendances (maintenu)

Le sous-système de workflows est un module borné (un « gestionnaire de paquets »
de workflows) avec ses propres APIs et un **manifeste maintenu**
(`resources/workflows/index.json`) : pour chaque workflow ingéré — nom, kind,
**provenance** (le workflow ComfyUI dont il est extrait + son hash, pour
détecter les MAJ), et **dépendances** (modèles + types de nœuds requis, déduits
du graphe). Il est écrit/mis à jour à chaque import et sert de source de vérité.

`GET /v1/comfyui/workflows` liste les workflows sauvés dans ComfyUI et les
croise avec les extraits : `not-extracted` / `extracted` / `update-available`
(la source ComfyUI a changé depuis l'extraction). Les dépendances listées sont **descriptives** (lues du graphe à chaque fois,
jamais recopiées). Le bridge ne les revalide PAS : **ComfyUI déclare et valide
les siennes** et rejette un graphe nommant un modèle absent — c'est sa réponse
qui fait foi, elle est classée puis retenue par Hermes.

### La file d'attente est celle de ComfyUI

Le bridge ne tient **pas** sa propre file : il soumet à ComfyUI, qui possède déjà
une queue, et relaie la sienne (`GET /v1/engine/queue`). Dès que ComfyUI accepte
un run, son `prompt_id` est attaché au job (`engine_ref`, affiché dans l'UI et
journalisé) — le même identifiant que dans ComfyUI, donc le run s'y **suit,
s'y surveille et s'y annule** normalement. Un job du bridge et une entrée de
queue ComfyUI sont la même chose vue de deux côtés, jamais deux états qui
pourraient diverger.

### Contrat de livraison

En bout de chaîne, le pipeline livre le **média produit** en cas de succès
(image/vidéo/audio, servi sous `/artifacts`), ou l'**erreur** en cas d'échec —
laquelle est **versée à Hermes / la base de connaissance** (un OOM abaisse le
plancher pour les runs suivants). Le backend `http` (défaut) applique ce contrat.
Le manifest du mode `cli`+`dry_run` est un **bouchon de simulation hors-ligne, pas
un livrable** (il est marqué comme tel).

## Brancher le vrai ComfyUI (backend HTTP)

Le bridge sait piloter un serveur ComfyUI réel via son **API HTTP** (POST `/prompt`
→ poll `/history` → GET `/view`), en stdlib pure — la même approche que le
connecteur Cortex. On l'active par le backend :

```bash
# ComfyUI Desktop doit tourner (serveur sur 8188)
COMFY_BACKEND=http python -m comfyui_bridge probe          # test de connexion
COMFY_BACKEND=http python -m comfyui_bridge serve --port 8000
```

Backends (`COMFY_BACKEND`) : `cli` (défaut, honore `COMFY_DRY_RUN` → manifest) ·
`http` (serveur ComfyUI réel). URL via `COMFYUI_BASE_URL` (défaut
`http://127.0.0.1:8188`). Serveur éteint → `probe` renvoie `available:false` avec
une raison claire ; un job échoue proprement (RFC 7807), jamais de blocage.

> Le workflow par défaut (`resources/workflow_template.json`) cible un modèle
> SD1.5. Si ton install ComfyUI a d'autres modèles (LTX, Wan, z-image…), pointe
> `COMFY_MAPPING` vers un mapping + workflow adaptés — aucun code à changer.

## Piloter sans l'UI — l'intégration dans un flux plus grand

La console web n'est **qu'un client** de cette API : elle n'appelle rien qu'un
autre appelant ne puisse appeler, et ne calcule rien qu'il devrait recalculer.
Un flux englobant envoie ses paramètres aux **IN** et récupère les fichiers
créés dans le **OUT**.

```bash
python scripts/exemple_integration.py            # le parcours complet, sans UI
```

**Découvrir les IN** — jamais deviner un nom : `GET /v1/workflows` donne, par
workflow, `intent_fields` — **les noms exacts à mettre dans le corps** d'un
rendu — et `runnable` (ce qui a déjà échoué ici). `GET /v1/workflows/{n}/io`
donne `intent_inputs` : pour chaque champ, le nœud piloté, le type, la valeur
actuelle du workflow et les **bornes déclarées par ComfyUI** (une borne qui vaut
la limite machine n'est pas une borne : elle n'est pas rendue). Tout le reste du
graphe reste adressable en `inputs: {"noeud.entrée": valeur}`.

Un champ que ce modèle ne connaît pas est **refusé** (422 `problem+json`) :
rien n'est avalé en silence.

**Connaître le coût avant** — `POST /v1/estimate` avec l'intention entière
renvoie la charge lue sur le graphe, l'estimation ajustée sur les runs mesurés,
et `ignored` : ce que ce workflow ne recevra pas.

**Lancer et suivre** — `POST /v1/render` renvoie `202` + un identifiant ;
`GET /v1/jobs/{id}` (ou le flux `…/events` en SSE) jusqu'à un état terminal
(`succeeded` / `failed` / `cancelled`), avec la position en file quand le moteur
est occupé. `POST /v1/jobs/{id}/cancel` arrête.

**Vérifier ce qui a été livré** — le média produit est **mesuré** (dimensions,
durée, nombre d'images) et comparé à ce qui a été demandé. Un workflow peut
recalculer ce qu'on lui donne — un latent construit à la moitié de la taille
demandée puis suréchantillonné rend 320×192 pour 352×224 demandés, et 0,75 s
pour 1 s. Chaque artefact porte `measured` et `gaps`, et
le journal du run l'écrit. Ce qu'on ne sait pas lire (WEBM, MKV, OGG) ne produit
aucune mesure — donc aucun écart : une mesure absente vaut mieux qu'une mesure
inventée. Un arrondi de conteneur (48 images à 24 i/s tiennent en 2,042 s) n'est
pas un écart.

**Récupérer le OUT** — le job terminé porte `artifacts` : `kind`, `path`
**absolu sur l'hôte**, `url` téléchargeable, `bytes` ; plus `duration_s`, la
durée mesurée par le moteur. Le champ `label` de l'intention nomme la sortie
(`cortex/<label>_00001_.mp4`) : un flux appelant retrouve ainsi **ses** fichiers
via `GET /v1/artifacts`, même si le service a redémarré entre-temps — les jobs
vivent en mémoire, les fichiers non.

## Quand un workflow change dans ComfyUI

L'analyse stockée est une **photo** de la source au moment de l'extraction. Si
l'auteur modifie le workflow dans ComfyUI, c'est cette photo qui continue de
tourner. Une seule lecture de fraîcheur (empreinte de la source + noms de nœuds
lus) répond à trois endroits, pour qu'ils ne divergent jamais :

- `GET /v1/workflows` — chaque entrée porte `source_changed` et sa raison ;
- `GET /v1/workflows/updates` — la notification en une requête, pour un flux
  qui n'affiche aucune console ;
- au **lancement**, le journal du job le dit : sans cela un run a rendu un
  `.flac` alors que la nouvelle version sauvait un `.mp3`, sans un mot.

La console montre une pastille sur l'onglet *Workflows* et avertit sur le
workflow sélectionné.

Ré-analyser (`POST /v1/comfyui/workflows/{fichier}/extract`) relit le graphe par
`graphToPrompt` et **dit ce qu'elle a re-mesuré** — `changes` : entrées apparues,
disparues, déplacées d'un nœud à l'autre, changement de type de média, et
**changement de LIVRAISON** (`delivery_changed`, `delivers`) : le nœud qui
délivre fait partie du contrat autant que les entrées, puisqu'en changer change
le fichier produit.

Une source **supprimée** dans ComfyUI n'est pas passée sous silence
(`source_missing`), et `DELETE /v1/workflows/{nom}` retire l'extrait devenu
orphelin — ce qui s'ingère doit pouvoir se retirer.
Éprouvé sur un workflow dont les IN ont été bouleversés :

```
− negative_prompt, steps · déplacés : cfg (5.cfg → 14.cfg),
  duration_s (4.seconds → 10.value), seed (5.seed → 13.noise_seed)
```

Un appelant resté sur l'ancien contrat n'est pas trahi en silence : les champs
qui n'existent plus reviennent dans `ignored`.

## Estimation du temps

Forme reconnue pour la diffusion : le temps croît **linéairement avec le nombre
d'étapes de débruitage** et avec la **surface** générée, multipliée par le nombre
d'images. La passerelle mesure donc une **charge** :

```
charge = (largeur × hauteur / 1e6) × images × étapes
temps  ≈ mise en route + coefficient × charge
```

Tout est **lu sur le graphe injecté**, celui que le moteur va réellement
recevoir — jamais sur les seuls champs du formulaire :

- **images** : `length` du latent, sinon durée × FPS ;
- **étapes** : la somme de **toutes les passes d'échantillonnage**, qu'elles
  portent un `steps`, un ordonnanceur en amont ou une liste explicite de sigmas
  (un workflow LTX réel en fait deux : 3 + 8) ; une passe qui ne couvre qu'une
  fenêtre de l'ordonnancement (Wan 2.2 : 0→10 puis 10→fin) n'est pas comptée
  deux fois ;
- **surface** : le nœud latent, ou la valeur liée en amont.

Un facteur **illisible compte pour 1** — neutre, jamais annulant : il est fixé
dans le graphe, donc identique à chaque run de ce workflow, et le coefficient
appris l'absorbe. L'annuler rendait l'estimation sourde à tout : 24 images et
96 images donnaient le même chiffre (mesuré : 134 s et 411 s).

La **mise en route** est *mesurée* : le moteur ne dit rien tant qu'il charge son
modèle, puis annonce ses étapes — l'écart entre les deux est le chargement. Elle
est retirée avant l'ajustement, sans quoi les tailles ne s'alignent pas (mesuré :
un run à froid de charge 10 en 110 s, un run à chaud de charge 166 en 73 s — le
plus lourd paraissait le moins cher et aucune droite ne passait). Le
**coefficient** est ensuite ajusté au moindre carré sur le temps de calcul des
runs de CE workflow, sur cette machine, et la mise en route médiane est rajoutée.
Il faut **deux tailles mesurées** pour séparer la mise en route de la charge :
avec une seule, aucune estimation en charge n'est rendue (appliqué
proportionnellement, un run mesuré à 110 s en annonçait 29 min pour un autre qui
en prend 2). Tant que c'est le cas, la console le dit au lieu de laisser croire
que les paramètres comptent, et le repli est la médiane de cette configuration.
Un run **resservi par le cache du moteur** livre un vrai fichier en quelques
dixièmes de seconde : sa durée est journalisée mais n'entre jamais dans
l'ajustement. Le barème de charge est **versionné** : le changer
n'autorise pas à mélanger deux échelles dans le même ajustement.

## Retrouver avec quoi un fichier a été produit

Le moteur **inscrit déjà** le graphe dans ce qu'il produit — c'est ce qui permet
de glisser une image dans ComfyUI et d'y retrouver le workflow :

| format | où | lu par |
|---|---|---|
| PNG | chunk `tEXt` `prompt` (et `workflow`) | `adapter/provenance.py` |
| MP4 / MOV | boîte `moov/udta/meta`, clé `prompt` | idem |
| FLAC | `VORBIS_COMMENT`, champ `prompt=` | idem |

Rien n'est donc recopié pour ces formats : `GET /v1/artifacts/origin?name=<chemin
relatif>` **lit** le fichier et rend un résumé — prompt, dimensions, images,
étapes, cfg, graine — nommé comme dans le contrat d'entrée, parce qu'il est tiré
par la même analyse (`autobind`). Un fichier reste ainsi explicable seul, des
mois plus tard, sans ce service et sans l'historique du moteur, qui est purgé à
chaque redémarrage.

Un livrable qui ne sait rien porter (`.txt`, `.csv`, `.webp`) reçoit un fichier
compagnon `<nom>.origine.json` de même contenu — écrit **uniquement** dans ce
cas, et jamais listé comme livrable.

Enfin, un job porte `params` : ce qui a été **demandé**. C'est complémentaire de
ce que le fichier dit — celui-ci rend les valeurs **effectives**, y compris
celles que l'appelant n'a pas fournies et que le workflow portait.

## Ce qu'un run livre

Un workflow ne produit pas que du média : certains **mesurent** et rendent des
nombres, une légende, une liste de régions. ComfyUI rapporte ces fichiers sous
la clé `files` de son historique, à côté de `images`, `videos` et `audio` — les
ignorer livrait l'illustration d'une analyse sans jamais livrer son résultat.
Les deux backends lisent la même définition (`adapter/media.py`), et ne
ramassent jamais les fichiers de travail du service lui-même.

Le nom de sortie (`label`) atteint **tous** les nœuds de sauvegarde : un
workflow qui délivre une image et une mesure a deux nœuds, et n'en piloter qu'un
laissait la moitié des livrables hors du nom demandé.

## Quel workflow fait autorité ?

Un même nom peut venir de deux sources : une entrée **déclarée** dans le fichier
de réconciliation, ou un graphe **enregistré** par `POST /v1/workflows` (stocké
dans le dossier des workflows). **L'entrée déclarée l'emporte** — ses liaisons
sont écrites à la main, donc voulues.

Ce masquage était silencieux : un enregistrement semblait réussir puis cédait la
place au redémarrage. Désormais l'ingestion sous un nom déclaré est **refusée**
avec la raison, et `GET /v1/workflows` liste les masquages déjà en place sous
`shadowed_by_declaration`.

## Hermes — réconciliation contre les problèmes CONNUS

Hermes est un **rôle de réconciliation** appelé en **mode local**. Sa valeur est
la mémoire, pas la ruse : avant un run, il consulte ce qui a **déjà mal tourné
ici** et ce qui est **vérifiable d'avance**. Rien n'est inventé — aucune
heuristique de VRAM, aucun seuil deviné. Un registre vide signifie honnêtement
« aucun problème connu », et le run passe.

Un souvenir ne refuse un run que si **réessayer coûte cher** : refuser interdit
aussi le seul run qui prouverait la réparation. Une dépendance absente est
rejetée par le moteur avant tout calcul, donc la retenter ne coûte rien — elle
**avertit** (`warnings`) sans bloquer. Un dépassement mémoire ou un blocage du
système coûtent des minutes : ceux-là refusent, et `POST /v1/render?force=true`
laisse essayer malgré tout. Réenregistrer un workflow avec un graphe **différent**
**révise** ce qu'on savait de l'ancien : ce souvenir ne parle plus de ce workflow.

### Un souvenir démenti est révisé, et le moment est gardé

Une mémoire qui ne se re-teste jamais devient un mensonge. Hermes est donc
**tenu d'ajuster** ce qu'il décrit dès qu'un **processus nouveau** change cet
état — l'ajustement se fait dans l'écriture même de l'issue, pas au bon vouloir
d'un appelant :

* le run **a réussi** là où un souvenir disait que cette configuration échoue ;
* le run a été livré par un autre **mécanisme de livraison** (`DELIVERY_MECHANISM`
  dans `adapter/media.py`, versionné comme le barème de charge `WORK_MODEL`) que
  celui qui avait retenu le problème. Seul ce que ce mécanisme peut avoir causé
  lui-même est revu (un échec non classé) ; un dépassement mémoire ne lui doit
  rien et reste debout. Mesuré : un maillage écrit par le moteur mais non
  ramassé par la passerelle avait été retenu contre un workflow qui marchait ;
* le workflow a été **réenregistré** avec un graphe différent.

Rien n'est effacé : effacer emportait avec le souvenir **le moment où il a cessé
d'être vrai** et **ce qui l'a changé**. La ligne reste, marquée et datée, et se
lit sous `revisions` (readiness), `revised` (`/v1/hermes/problems`) et
`revised_problems` (réponse d'ingestion).

**La dernière date, quand c'est elle qui compte.** Un problème connu est annoncé
avec `last_seen` (la dernière fois qu'il s'est vérifié), `first_seen` et
`occurrences` — la console affiche cette dernière date. Une **durée estimée**,
elle, n'a pas de date : c'est une médiane de runs, annoncée par son nombre de
mesures (`samples`) et sa base (`basis`). Dater un calcul le ferait passer pour
un fait daté.

Il refuse un run quand :

1. une **dépendance manque vraiment** sur l'hôte (modèle ou type de nœud absent,
   vérifié via `/object_info`) — uniquement si le moteur va réellement tourner ;
2. cette **même configuration a déjà échoué ici** pour une cause qui se
   reproduirait (`oom`, `missing-model`, `missing-node`, `blocked-by-os`). Le
   refus **cite le problème passé**. Un succès ultérieur sur la même
   configuration révise cette mémoire, à la date où c'est arrivé (l'hôte a
   changé).

Les causes non reproductibles (`timeout`, `unreachable`, `workflow-error`) sont
remontées comme **contexte**, jamais comme refus.

**Ce qui est observé** : chaque run réel est journalisé avec son résultat, sa
configuration (`largeur x hauteur x images`) et, en cas d'échec, la **cause
classée depuis le message réel** du moteur plus ce message verbatim. La zone
d'observation est ainsi honnête : elle ne réduit plus toute panne à « OOM ».

**Localité** : la base vit dans le dossier privé `_data/` de CE pipeline et
chaque ligne porte un `scope` (`HERMES_SCOPE`, défaut `comfyui`) sur lequel
toutes les lectures filtrent — pas de fuite ni de pollution entre pipelines.

Consultation : `GET /v1/hermes/runs` (ce qu'il sait, avec la levée éventuelle de
chaque échec) et `GET /v1/hermes/problems?workflow=…` (problèmes debout, plus
les souvenirs `revised` avec leur moment et leur cause).

## Vidéo longue dirigée (menus de style)

Trois montages du catalogue produisent une vidéo LONGUE dont chaque bloc
reçoit le temps du récit où il tombe, au lieu de rejouer la même consigne :

| entrée | moteur | mécanique |
|---|---|---|
| `video-longue-stylee-h3` | MiniMax H3 | blocs de 8 s à mémoire constante, recollés en un livrable |
| `video-longue-stylee-ltx` | LTX-2.3 | blocs chaînés par recouvrement (latentes cumulées) |
| `video-longue-relais-stylee` | LTX-2.3 | une passe, fenêtres de contexte + relais de prompt |

Deux champs d'intention les dirigent — `style_graphique` (le médium) et
`style_narratif` (les temps du récit). Ce sont des champs SÉMANTIQUES, comme
`prompt` : une clé de catalogue, jamais un numéro de nœud. Les valeurs sont
celles que ComfyUI déclare pour le nœud `DirectionDeStyle` du paquet
`comfyui-direction-de-style` — `GET /v1/workflows/{nom}/io` les rend dans
`intent_inputs[].options`, et la console les montre en menus déroulants.
Le `prompt` s'écrit `décor | temps un | temps deux …` : la première part vaut
pour toute la vidéo, les suivantes sont réparties sur les temps du récit.

Dans un montage, la clé `"blocs": ["amorce", "segment"]` nomme les fragments
qui produisent un morceau : chacun voit alors `bloc_rang` et `blocs_total`
dans ses calculs (`{"$calc": "bloc_rang"}`), ce que le tour de boucle ne dit
pas (l'amorce est hors boucle). Le nœud `DirectionDuBloc` de chaque bloc en
déduit son temps.

## Chaînes (workflow of workflows)

Beaucoup de livrables ne tiennent pas en un seul run : une révélation PUIS sa
fermeture, un plan PUIS son prolongement. Écrire cet enchaînement chez
l'appelant lui rendait la logique métier — l'ordre des étapes, la reprise des
fichiers, les contrôles — que la passerelle existe pour tenir. Une **chaîne**
est donc une entrée de catalogue comme une autre, avec `chaine` au lieu de
`workflow` + `bindings` :

```jsonc
"video-revelation": {
  "kind": "video",
  "chaine": "…/_data/chaines/video-revelation.json",
  "titre": "Révéler une image", "categorie": "reveler-une-image", "ordre": 1,
  "aides": { "cta": "Une phrase courte, écrite à l'encre sur la page refermée. Vide : aucun appel." }
}
```

Elle se lance par le **même verbe** que tout le reste : `POST /v1/render` avec
son nom, et ses champs exposés à la racine du corps. Elle se suit par le même
`GET /v1/jobs/{id}` (+ SSE), avec ses `etapes`. Une étape peut porter `"quand": "$champ"` : elle n'est jouée que si ce renvoi
désigne une valeur non vide, sinon elle est **sautée** (statut `skipped`, raison dite)
et rend le média qu'elle devait reprendre en livrable — l'aval qui la nomme tient. Une étape `rendre` est un run
ORDINAIRE : sous-job visible dans `/v1/jobs`, Hermes, journal, estimation,
reprise — rien n'est réécrit pour elle.

Le fichier de chaîne (copies de référence dans
[`resources/chaines-exemples/`](comfyui_bridge/adapter/resources/chaines-exemples/)) :

```jsonc
{
  "version": 1, "chaine": "video-revelation", "resume": "…",
  "expose": {
    "image":       { "media": "image", "requis": true, "libelle": "L'image à révéler" },
    "duration_s":  { "type": "FLOAT", "defaut": 45, "min": 5, "max": 79, "unite": "s" },
    "style_narratif": { "type": "COMBO", "defaut": "reseau-social",
                        "options_depuis": { "menu": "style_narratif" },
                        "libelle": "Structure du récit" },
    "fond":        { "type": "COMBO", "defaut": "washi", "options": ["washi", "sepia"] },
    "cta":         { "type": "STRING", "defaut": "", "libelle": "Appel final (facultatif)" }
  },
  "etapes": [
    { "id": "analyse",   "rendre": { "workflow": "image-iconographe",
        "media": { "image": "$image" } } },
    { "id": "intention", "rendre": { "workflow": "image-intention",
        "media": { "image": "$image" }, "style_narratif": "$style_narratif",
        "inputs": { "62.markers_json": "$analyse.recit.markers_json" } } },
    { "id": "plan_valide", "verifier": [
        { "id": "le_plan_nomme_son_climax", "valeur": "$intention.recit.climax",
          "op": "exists" } ] },
    { "id": "deroulement", "rendre": { "workflow": "video-reveal-cinematic-dirige",
        "media": { "image": "$image" }, "duration_s": "$duration_s",
        "inputs": { "61.direction_json": "$intention.recit.direction_json",
                    "61.fond": "$fond" } } },
    { "id": "plan_tenu", "verifier": [
        { "id": "l_accroche_est_vue_a_2_5_s",
          "valeur": "$deroulement.recit.hook_vu.atteint", "op": "gte", "attendu": 0.10 } ] },
    { "id": "raccord",    "extraire_queue": { "video": "$deroulement.livrable", "images": 50 } },
    { "id": "conclusion", "rendre": { "workflow": "video-reveal-closing",
        "media": { "video": "$raccord.depot" }, "duration_s": "$conclusion_s" } },
    { "id": "appel",      "quand": "$cta",
                          "rendre": { "workflow": "video-appel-final",
        "media": { "video": "$conclusion.livrable" }, "inputs": { "7.texte": "$cta" } } },
    { "id": "montage",    "recoller": { "parts": ["$deroulement.livrable", "$appel.livrable"] } },
    { "id": "controle",   "verifier": [
        { "id": "les_deux_parts", "valeur": "$montage.parts", "op": "eq", "attendu": 2 } ] }
  ],
  "livrable": "$montage.livrable"
}
```

**Menus dont la liste n'est pas écrite** : un champ peut dire d'OÙ viennent ses
valeurs plutôt que les recopier — `"options_depuis": {"catalogue": {…}}` (les
entrées publiées du catalogue, filtrées) ou `"options_depuis": {"menu": "<nom>"}`
(les valeurs d'un menu déclaré, qui peut n'être que la projection d'un fichier
tenu par un fournisseur). Recopiée, une liste vieillit au premier ajout ; ici
elle reste celle du fournisseur, et la passerelle refuse tout ce qui n'y est
pas. Nommer deux sources à la fois, ou un menu sans le nommer, est refusé **à
la lecture**.

**Renvois** : `$champ` (une valeur exposée), `$etape.cle` (un résultat d'étape
PRÉCÉDENTE). Un renvoi vers l'aval ou vers un nom inconnu est refusé **à la
lecture**, en le nommant : découvert en route, il faisait échouer la chaîne
après avoir dépensé les étapes d'avant.

**Réglages de nœud** : une étape `rendre` peut écrire directement une entrée
du graphe qu'elle vise, `"inputs": {"<nœud>.<entrée>": "$champ"}` — c'est
ainsi que le fond et l'encre choisis par l'utilisateur atteignent le nœud de
révélation (`61.fond`, `61.encre`) et l'appel final le nœud d'inscription
(`7.texte`). L'entrée doit exister en littéral dans le graphe (sinon l'étape
échoue en la nommant), et jamais une entrée déjà liée par les `bindings`, que
l'override écraserait en silence.

**Média depuis un livrable** : un `rendre` peut consommer `$etape.livrable` en
média — la passerelle le dépose chez le moteur, comme elle le fait déjà pour
ce que `extraire_queue`/`extraire_image` produisent.

**Récit** : le premier artefact `.json` qu'un run rapporte (hors compagnon `.origine.json`) est parsé sous `recit`
dans le résultat de l'étape. Une étape `verifier` contrôle alors ce que le
nœud a MESURÉ — `$revelation.recit.hook_vu.atteint`, `…climax_tenue_s`,
`…duree_retenue_s` — et arrête la chaîne AVANT de dépenser la fermeture
quand le plan ne tient pas la règle des quatre temps.

**Genres d'étape** et ce que chacun rend :

| genre | ce qu'il fait | résultat |
|---|---|---|
| `rendre` | un run de workflow, par les moyens ordinaires | `livrable`, `artefacts`, `mesure`, `job_id`, `recit` (le premier artefact `.json` du run, hors compagnon, parsé ; la fiche du job n'en garde que les valeurs simples du premier niveau, le récit entier reste lisible par les étapes `verifier`) |
| `extraire_queue` | les N dernières images, en clip SANS PERTE (`-qp 0`, compte revérifié), déposé chez le moteur | `fichier`, `depot`, `images` |
| `extraire_image` | une image, par son index exact (`first`/`last`/N) | `fichier`, `depot` |
| `recoller` | joindre des parts (ré-encodage uniforme, piste silencieuse si muet) | `livrable`, `mesure`, `parts` |
| `mesurer_raccords` | la ressemblance (SSIM) de part en part, aux frontières du montage réel | `paires`, `pire`, `moyenne` |
| `verifier` | des contrôles `eq/ne/lte/gte/between/exists` sur ce qui a été mesuré | `controles` |

Une part de `recoller` / `mesurer_raccords` s'écrit `"chemin"` ou
`{"fichier": "…", "depuis_image": 17}` — le rognage de tête jette le
chevauchement que l'amont a re-rendu.

Un contrôle faux **arrête** la chaîne (`problem_kind: "controle-echoue"`, avec
le mesuré ET l'attendu) ; les fichiers déjà produits restent dans les artefacts,
puisque c'est en les regardant qu'on comprend. Les pièces intermédiaires vivent
sous `<sortie>/cortex/_travail/<job>/` et ne sont jamais listées comme
livrables. Annuler le parent arrête le sous-job en cours par les moyens du
moteur et saute le reste.

Trois chaînes sont livrées.

`video-revelation` — « Révéler une image », le seul flux publié de sa
catégorie, **onze étapes** qui portent les noms du travail :

| étape | ce qu'elle fait |
|---|---|
| `analyse` | la DOCUMENTATION de l'image par **Iconographe** (`image-iconographe`) — carte d'attention, éléments découpés au pixel, hiérarchie mesurée, noms et textes — servie par sa bibliothèque si l'œuvre y est déjà (verdict « repris », 15,9 s mesurées), calculée sinon (≈ 18 min, une fois). Ne livre aucun média : son artefact `.json` EST son résultat |
| `culture` | la CULTURE de l'œuvre par **Iconologue** (`image-iconologue`) — identité prouvée, notice, passage du récit représenté, sens des motifs, et une **attestation par élément** du relevé. Elle rend l'ancrage **enrichi**, et son central re-décidé sur la figure que les bases déclarent sujet : sur l'Uccello, le climax passe du cheval blanc au **dragon** (rang 6 + 3 contre 7). Œuvre inconnue des bases : `reconnu: false`, l'ancrage ressort intact, rien ne casse |
| `intention` | le plan : accroche, temps retenus, climax, dans la structure de récit demandée. Artefact `.json` lui aussi |
| `plan_valide` | le plan tient-il ? accroche et climax nommés, l'accroche ne recouvre pas le climax, au moins trois temps, **le plan tient dans son APPROCHE et son trajet ne revient pas sur ses pas** — avant de dépenser la moindre seconde de rendu |
| `deroulement` | la peinture, qui reçoit le relevé et le plan tels quels — et, depuis le 2026-09-14, qui les SUIT : le champ `conduite` vaut « le plan », l'ordre des temps, leur rythme et le cadrage viennent de l'intention (« la camera » rejoue le déroulement d'avant). Le champ `rendu` vaut « ink-bleed » : une tache d'encre par temps, qui fleurit, s'étend à bords humides et rejoint les autres. Le champ `ambiance` vaut « lanterne » depuis le 2026-09-15 : une flaque de lumière chaude posée hors champ, dont le centre dérive et dont la flamme respire, et dont les rayons rasants font accrocher les fibres du papier — la page se VIT pendant qu'on dessine dessus (« selon-le-fond » rejoue la lampe fixe d'avant, « atelier » la page nue). Depuis le 2026-09-15, trois choses de plus : la CONTEMPLATION (`contemplation_s`, 4 s, bornée de 3 à 5) est la dernière étape du déroulement — l'image révélée se regarde sous une caméra qui continue de s'ouvrir, jamais figée ; le NÉGATIF est un champ (`negatif`, « non » par défaut : l'encre est ce qui est sombre dans l'œuvre, une nuit se peint en lavis noir autour d'une lune laissée en réserve — « selon-l-oeuvre » rejoue l'inversion automatique d'avant) ; et le SILLAGE fait suivre la caméra par l'encre pendant les trajets (une goutte par seconde là où elle sera, un trait sur le contour qu'elle va montrer), sans jamais entrer dans la boîte d'un temps à venir. Et depuis le 2026-09-15 après-midi, les BORDS de l'œuvre (`bords`, « fondus » par défaut) : la matière de l'œuvre continue au-delà de son arête et se fond dans la feuille sur un front ondulé — le rectangle de l'œuvre ne se lit plus pendant la construction (« francs » rejoue le prolongement d'avant, flouté dès l'arête, où il se lisait ; le récit mesure `cadre_lu_encre` / `cadre_lu_couleur`) |
| `plan_tenu` | ce que la peinture a MESURÉ contre ce que le plan promettait : accroche vue, climax hors de l'ouverture et tenu, étapes qui se suivent, **ordre du plan suivi, chaque temps cadré (≥ 0,9) à son heure, aucun temps supprimé, caméra qui glisse (≤ 0,1 largeur/s) sans saccade (accélération ≤ 0,5 largeur/s²), page qui ne s'achève pas d'un coup (≤ 0,25 au dézoom), temps lisibles (halo encré ≥ 0,85), ordre d'ARRIVÉE de l'encre conforme au plan, cœur du climax en dernier, contemplation qui ne se fige pas (≤ 0,5 s immobile), jamais de page blanche sous la caméra (≥ 1 % du cadre encré après l'accroche)** |
| `raccord` | les 50 dernières images, en clip sans perte |
| `conclusion` | la page se referme (0 s = pas de conclusion) — sous la MÊME ambiance, et à la seconde où le déroulement s'arrête (`6.depart_s` = `$deroulement.recit.duree_retenue_s`) : la flamme y reprend sa phase, et la luminance ne bouge pas de plus de 1 % au raccord. Depuis le 2026-09-15 elle ne contemple plus (`hold_s` 0,5 s au lieu de 2,2 : la contemplation appartient au déroulement) : un souffle, puis l'encre reprend la page — et elle MÈNE AU CTA |
| `appel` | l'appel final (`cta`) écrit à l'encre quand la fermeture a fini : il ne mord que sur sa dernière seconde, puis reste le temps de se lire, déduit du texte (la conclusion reçoit le même texte par `6.appel_texte` et prolonge sa page refermée, vivante, d'autant) ; la police s'injecte par `cta_police` (nom ou chemin). Sans texte, l'étape est **sautée** (`"quand": "$cta"`) et rend le livrable de la conclusion tel quel |
| `montage` | déroulement + fin, recollés |
| `controle` | deux parts, un livrable qui pèse |

Les graphes qu'elle enchaîne (`image-iconographe`, `image-iconologue`,
`image-intention`, `video-reveal-cinematic-dirige`, `video-reveal-closing`,
`video-appel-final`) et `video-still-motion` restent des **techniques**, sans
catégorie : le lanceur ne les montre pas.

`video-revelation-brume` — « Révéler une image par la brume », **l'essai d'une
autre technique** dans la même catégorie (ordre 2), en **onze étapes**. Elle
PARTAGE tout l'amont avec la précédente — mêmes `analyse`, `culture`,
`intention`, `plan_valide`, mêmes appels, même plan remis tel quel — et ne change que la
peinture : son `deroulement` appelle `video-reveal-brume-dirige` (nœud
`RevealBrume`) au lieu du nœud d'encre. L'image est déjà là, **entière et en
couleur**, sous une nappe de bruit fractal animé qui se dissipe selon le même
champ d'heures narratif ; sous la brume elle est floue et désaturée d'autant
qu'elle est couverte. **Elle a sa conclusion depuis le 2026-09-15**, et c'est
la même queue que celle de l'encre, au même endroit : `raccord` (les 50
dernières images) → `conclusion` → `appel` → `montage` en deux parts, avec son
champ `conclusion_s` (0 = aucune). On avait écrit qu'une brume qui reviendrait
ne refermerait rien ; le reproche d'Antoine a déplacé le jugement — « il manque
la partie conclusion à toutes ces vidéos » : il ne s'agit pas de REFERMER un
récit mais de le POSER, puis d'amener l'appel. Le nœud `BrumeClosing`
(`video-reveal-brume-closing`) fait revenir la brume depuis les bords vers le
climax, **repris en dernier** (son heure de retour n'est que sa distance au
foyer : rien n'est découpé), avec la MÊME brume que le déroulement et une nappe
qui reprend sa dérive à la seconde où celui-ci s'est arrêté (`6.depart_s`). La
dernière image est une **page de brume claire** — luminance 0,82 pour un contrat
à 0,80 —, celle sur laquelle l'appel final écrit son encre sombre. Le champ
`fond` y choisit la teinte de la brume (blanche, grise, dorée) ; ni `encre`
ni `rendu`. Son `plan_tenu` mesure les mêmes grandeurs que l'encre quand elles
ont un sens, **sur la carte de densité que le nœud vient de rendre** : accroche
vue, climax hors de l'ouverture et tenu, caméra qui glisse sans saccade, temps
lisibles (boîte et halo sous 0,3 de densité à leur heure), ordre d'arrivée,
cœur en dernier, aucun temps supprimé.

`video-prolongement` — 17 dernières images → prolongement → mesure du raccord
→ recollage sans le chevauchement.

### Le socle ink — ce qui est figé, ce qui se règle, où ajouter

Antoine, le 2026-09-15 à midi, sur les vidéos ink livrées : « c'est parfait —
assure cette standardisation ». Trois couches, et un témoin exécutable
(`tests/test_socle_ink.py`) qui les épingle :

| couche | ce que c'est | où |
|---|---|---|
| **le plan** | agnostique, hors de tout style : les onze étapes, dans cet ordre, avec leurs genres — `analyse` → `culture` → `intention` → `plan_valide` → `deroulement` → `plan_tenu` → `raccord` → `conclusion` → `appel` → `montage` → `controle` — et le contrat par lequel chacune parle à la suivante (`$etape.recit.*`, `$etape.livrable`, `$raccord.depot` ; la fermeture reçoit `fermeture_json` du récit du déroulement au lieu de relire le disque). La chaîne de la brume porte le même plan. | `_data/chaines/*.json` et leurs jumeaux |
| **les paramètres** | tout ce qui se règle : structure du récit, approche, fond, ambiance, tracé, rendu, conduite, négatif, contemplation, conclusion, CTA et sa police, format. **Leurs défauts sont ceux du style ink livré ce jour-là et ne changent pas** : `reseau-social`, `peinture-calme`, `washi`, `lanterne`, `lavis`, `ink-bleed`, `le plan`, négatif `non`, contemplation 4 s, conclusion 8 s, 45 s, 704×1280, 25 i/s, graine 71 | `expose` de la chaîne ; littéraux du graphe local `video-reveal-cinematic-dirige` |
| **les styles** | ce qu'on ajoute sans rien casser : un style narratif ou une approche dans les catalogues de `comfyui-direction-de-style` (`styles/narratifs.json`, `styles/approches.json`), un fond, une encre, une ambiance, un rendu, un négatif dans les tables du paquet de nœuds (`FONDS`, `ENCRES`, `AMBIANCES`, `RENDUS`, `NEGATIFS`), une brume dans `BRUMES`. Une **entrée de plus**, jamais un défaut de moins ; le défaut reste en tête de chaque liste | les catalogues et les tables |

Le témoin refuse tout écart : plan, défauts, bornes, options (le défaut en tête,
rien de retiré), contrat de chaque étape, listes de contrôles, littéraux des
graphes locaux quand ils sont là. Le paquet de nœuds a le sien
(`tests/test_socle_ink.py` de `comfyui-ink-reveal`) pour les défauts des nœuds
et les constantes du rendu.

### Ce qui est agnostique est APPELÉ, jamais ancré

Une étape qui ne regarde pas ce flux-ci n'a rien à faire dedans. Cinq
mécanismes de la chaîne ci-dessus valent pour n'importe quel flux, et sont
donc des entrées de catalogue qu'elle APPELLE — un autre flux les appellera
sans rien dupliquer :

| mécanisme | où il vit | ce qu'il rend |
|---|---|---|
| la DOCUMENTATION d'une image | graphe `image-iconographe` (nœud `IconographeDocumentation`, paquet `comfyui-iconographe`) → le service **Iconographe** (`E:/Claude Code/Programmes/Iconographe`, `127.0.0.1:7940`) et sa BIBLIOTHÈQUE, schéma `iconographe/documentation` | où l'œil va, ce qu'il y a et où, ce qui compte (attraction, accroche, central), les noms — payés une seule fois par œuvre (sha256 exact puis empreinte perceptuelle), quel que soit ce qu'on en fera. Il ne dit PAS ce que l'œuvre est : son port `culture` n'a aucun adaptateur, et c'est l'étape suivante qui l'apporte |
| la CULTURE d'une œuvre | graphe `image-iconologue` (nœud `IconologueCulture`, paquet `comfyui-iconologue`) → le service **Iconologue** (`E:/Claude Code/Programmes/Iconologue`, `127.0.0.1:7950`) et son CATALOGUE, schéma `iconologue/dossier` | ce que l'œuvre EST, sourcé : identité et degré de la preuve, notice, passage du récit représenté, sens des motifs, et une **attestation par élément** (qui l'affirme, sur quelle base). L'ancrage en ressort enrichi : une figure attestée par le titre ou par les sujets déclarés pèse 3 de plus, et le central est re-décidé sur elle |
| la STRUCTURE du récit | catalogue de structures du paquet `comfyui-direction-de-style` (`styles/narratifs.json`), servi au formulaire par `options_depuis: {"menu": "style_narratif"}` et au graphe `image-intention` par le champ sémantique `style_narratif` | les temps, leur ordre, ce que chacun doit faire (`reseau-social` : hook · setup · corps · conclusion facultative · appel) |
| l'APPROCHE | catalogue d'approches du MÊME paquet (`styles/approches.json`), servi au formulaire par `options_depuis: {"menu": "style_approche"}` et au graphe par `70.style_approche` | COMMENT la révélation se conduit : combien de temps au plus, combien de temps chacun tient, ce que la caméra s'autorise, par quel geste l'encre vient (`peinture-calme` : au plus 6 temps, un chemin continu sans retour, une caméra qui glisse à 0,08 largeur/s et ne s'arrête jamais) |
| l'APPEL FINAL | graphe `video-appel-final` | le texte écrit à l'encre sur la fin d'une vidéo — de n'importe quelle vidéo, pas seulement d'une révélation |

Le signe qu'un mécanisme doit sortir d'un flux : on peut le nommer sans
nommer le flux. La conclusion et l'appel final vivaient dans le même graphe ;
on ne pouvait pas avoir l'un sans l'autre. Ils sont maintenant deux étapes,
et deux graphes.

## Vitrine : catégories, titres, menus

Un lanceur ne doit tenir aucune liste : ni de noms de workflow, ni de
catégories, ni de valeurs. Tout cela est **déclaré à la passerelle**, dans le
fichier de réconciliation, et servi par le réseau.

* `categories` (clé de premier niveau) : `{"reveler-une-image": {"titre": "Révéler
  une image", "ordre": 1, "icone": "🖌️"}}` — rendu tel quel par `GET /v1/workflows`.
* Sur **toute entrée** : `titre`, `description`, `categorie`, `ordre`. Le
  catalogue les rend sous `presentation`. **Pas de `categorie` ⇒ l'entrée est
  technique** (étape de chaîne, utilitaire) et n'est pas publiée aux lanceurs.
  Une entrée qui ne porte QUE ces clés **décore** un graphe déposé dans le
  dossier des workflows, sans lui faire perdre son auto-liaison.
* `menus` (clé de premier niveau) : les libellés des valeurs d'un champ. La
  LISTE, elle, vient toujours du fournisseur — le moteur pour un COMBO de nœud,
  la chaîne pour un COMBO à `options` littérales (`fond`, `encre` de
  `video-revelation`), ou le catalogue quand une chaîne choisit un mode parmi
  les entrées d'un préfixe (`"options_depuis": {"catalogue": {"prefixe":
  "…"}}`). Deux formes :

```jsonc
"menus": {
  "encre": { "libelle": "Style de tracé", "libelles": { "lavis": { "libelle": "Lavis", "resume": "aplats et lavis, le défaut" } } },
  "style_graphique": { "source_fichier": {
      "chemin": "…/comfyui-direction-de-style/styles/graphiques.json",
      "table": "styles", "libelle": "libelle", "resume": "resume", "groupe": "famille" } }
}
```

`GET /v1/workflows/{nom}/io` rend alors, par champ, `choix` (valeur, libellé,
résumé, groupe), `libelle` et `unite`. Une valeur sans libellé apparaît telle
quelle ; une source illisible rend un `manque` plutôt que de dégarnir le menu
en silence. Pour une **chaîne**, `/io` répond `described: true` même moteur
éteint : son contrat est écrit, pas découvert.

## Ajouter un workflow

Aucun code à toucher : on ajoute une entrée au fichier de réconciliation (voir la
section ci-dessus) — un graphe ComfyUI (format API) + sa table de bindings
`param → { node, input }` + ses `defaults`. Un paramètre sans binding est ignoré
(ex. `fps` sur un workflow d'image fixe) ; un binding pointant vers un nœud/entrée
absent lève une erreur explicite (`workflow-mapping`, `500`) plutôt que d'échouer
en silence.

## Modifier un flux de création — où, et comment le savoir

Un flux ne vit que dans des **données** ; le code de la passerelle n'en connaît
aucun par son nom, et le lanceur (maestro) encore moins. La question « où vais-je
modifier ça ? » a donc une réponse par nature de changement :

| Ce qu'on veut changer | Où c'est écrit | Ce qui le sert |
|---|---|---|
| l'EFFET lui-même (l'encre, les taches, la caméra, la fermeture) | le paquet de nœuds ComfyUI (`comfyui-ink-reveal`, `comfyui-direction-de-style`, `comfyui-iconographe`…) | le moteur ; ré-extraire si les entrées changent |
| ce qu'on SAIT d'une image (éléments détectés, hiérarchie, noms) | **pas ici** : le service Iconographe (`E:/Claude Code/Programmes/Iconographe` — profils, seuils, adaptateurs de ports) ; ici on ne règle que la TRADUCTION (`elements_max`, `profil` du graphe `image-iconographe`) | l'étape `analyse` d'une chaîne |
| ce qu'on SAIT d'une ŒUVRE (identité, notice, récit, motifs, attestations) | **pas ici** : le service Iconologue (`E:/Claude Code/Programmes/Iconologue` — profils, sources, seuils d'identification) ; ici on ne règle que la TRADUCTION et le POIDS d'un sujet attesté (`POIDS_DU_SUJET_ATTESTE` du paquet `comfyui-iconologue`) | l'étape `culture` d'une chaîne |
| le GRAPHE d'un mode (ses nœuds, ses valeurs figées) | `_data/workflows/<nom>.json` + ses liaisons dans `_data/reconciliation.local.json` | `POST /v1/render` |
| l'ORDRE des étapes, les durées, les contrôles d'un flux composé | `_data/chaines/<nom>.json` (et sa copie `resources/chaines-exemples/`) | le runner de chaînes |
| un CONTRÔLE sur ce que le nœud a MESURÉ (hook vu, climax tenu, durée retenue) | l'étape `verifier` de la chaîne, sur `$etape.recit.<clé>` (le premier artefact `.json` d'un run est parsé sous `recit`) | le runner de chaînes |
| une RÉPÉTITION (blocs de boucle, conditions) | le montage `_data/workflows/<montage>.json`, ses blocs `_data/blocs/` | le dépliage |
| ce que l'utilisateur VOIT (titre, catégorie, résumé, libellés, aides) | `_data/reconciliation.local.json` : `titre`, `categorie`, `menus`, `aides` | `/v1/workflows`, `/io` |
| le VOCABULAIRE des styles | `styles/*.json` du paquet de direction de style | `/io` (`options` + `choix`) |

Jamais dans un fichier `.py` de la passerelle, jamais dans le lanceur. Ce n'est
pas une consigne : c'est **gardé**. La règle `flux-hors-du-code` du socle
(`garde.json`) dérive les noms de tous les flux du catalogue — réconciliation,
graphes extraits, chaînes — et refuse tout fichier de code qui en nomme un
(un commentaire ne fait que signaler). Le lanceur porte la même règle, dont le
vocabulaire est lu chez la passerelle de ce poste. Un cas particulier écrit
« pour ce flux-là » dans le code ne passe donc plus le commit ; et la copie de
référence d'une chaîne qui diverge de `_data/` est refusée par les tests.

## Configuration (variables d'environnement)

| Variable            | Défaut                    | Rôle                                   |
|---------------------|---------------------------|----------------------------------------|
| `COMFY_BACKEND`     | `http`                    | `http` (ComfyUI réel) \| `cli` (honore `COMFY_DRY_RUN`) |
| `COMFY_DRY_RUN`     | `1`                       | (backend cli) manifest au lieu d'un vrai rendu |
| `COMFY_CATALOG`     | `adapter/resources/reconciliation.json` | Fichier de réconciliation actif |
| `COMFYUI_BASE_URL`  | `http://127.0.0.1:8188`   | (backend http) serveur ComfyUI          |
| `COMFY_OUTPUT_DIR`  | `./_comfy_output`         | Répertoire de sortie ComfyUI            |
| `HERMES_DB`         | `_data/hermes.sqlite3`    | Registre des problèmes (privé au pipeline) |
| `HERMES_HOST`       | `localhost`               | Identifiant d'hôte                      |
| `HERMES_MODE`       | `local`                   | `local` (registre) \| `off`             |
| `HERMES_SCOPE`      | `comfyui`                 | Cloisonnement de la connaissance        |
| `COMFYUI_TIMEOUT`   | `3600`                    | Budget d'un run (vidéo = long)          |

## Tests

```bash
python scripts/smoke.py                 # sans dépendance
pip install -r requirements-dev.txt
pytest                                  # unitaires + API (TestClient)
```

## Intégration Cortex (piste)

Le service est sans état partagé hors SQLite et n'expose que du JSON : un rôle
Cortex peut poster une intention sur `/v1/render`, suivre le job, récupérer les
artefacts. `HERMES_HOST` permet de tenir un historique par machine du parc, et
la boucle d'apprentissage OOM rend le parc de plus en plus sûr à l'usage. Le
`container.py` (composition root) est le point d'accroche pour injecter d'autres
implémentations de ports (backend distant, journal en base partagée) sans
toucher au cœur.
```
