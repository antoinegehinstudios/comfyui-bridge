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
| POST    | `/v1/jobs/{id}/reprendre`      | **Reprendre** une chaîne échouée là où elle s'est arrêtée : les étapes abouties sont reprises (livrables et récits relus), la chaîne repart à l'étape en échec — 422 si rien n'a échoué ou si ce n'est pas une chaîne |
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
| `rendre` | un run de workflow, par les moyens ordinaires — ou N runs successifs quand la mémoire l'oblige (voir « Rendu par tranches », plus bas) | `livrable`, `artefacts`, `mesure`, `job_id`, `recit` (le premier artefact `.json` du run, hors compagnon, parsé ; la fiche du job n'en garde que les valeurs simples du premier niveau, le récit entier reste lisible par les étapes `verifier`) ; tranché, il porte en plus `job_ids`, `tranches`, `jonctions` |
| `extraire_queue` | les N dernières images, en clip SANS PERTE (`-qp 0`, compte revérifié), déposé chez le moteur | `fichier`, `depot`, `images` |
| `extraire_image` | une image, par son index exact (`first`/`last`/N) | `fichier`, `depot` |
| `recoller` | joindre des parts (ré-encodage uniforme, piste silencieuse si muet) | `livrable`, `mesure`, `parts` |
| `mesurer_raccords` | la ressemblance (SSIM) de part en part, aux frontières du montage réel | `paires`, `pire`, `moyenne` |
| `verifier` | des contrôles `eq/ne/lte/gte/between/exists` sur ce qui a été mesuré | `controles` |

Une part de `recoller` / `mesurer_raccords` s'écrit `"chemin"` ou
`{"fichier": "…", "depuis_image": 17, "sauf_les_dernieres": 13}` :
`depuis_image` est le rognage de TÊTE (il jette le chevauchement que l'amont a
re-rendu), `sauf_les_dernieres` le rognage de QUEUE, en images — il sert quand
l'AVAL a déjà repris la fin de cette part. Une part dont le fichier est `null`
ou vide est **ignorée**, et dite au journal : c'est ainsi qu'une étape sautée
rend « rien » sans casser le montage qui la nomme (il en faut au moins une qui
reste). `mesurer_raccords` compare alors la dernière image **gardée** d'une part
à la première de la suivante — mesurer la dernière image du fichier aurait jugé
une frontière que personne ne voit.

Une étape facultative (`quand`) peut déclarer ce qu'elle rend quand elle N'A PAS
LIEU : `"sinon": {"livrable": null, "recit": {"images_reprises": 0}}`, un objet
libre fusionné **par-dessus** le passe-plat (le média repris, qui reste le
défaut sans `sinon`). Sans lui, un aval qui lit `$appel.recit.images_reprises`
gardait un renvoi non résolu, et le montage échouait à cause d'une étape qu'on
avait justement choisi de ne pas jouer.

**Un job de chaîne réussi livre son livrable, rien d'autre** : ses `artifacts`
ne portent que la production finale montée — ni le déroulement recollé, ni la
conclusion, ni l'appel, ni les clips ou les récits. Antoine, 2026-09-16 :
« maestro ne doit pas, dans son outil de visualisation des productions,
afficher les produits d'itérations, mais seulement la production finale montée ;
il doit toujours livrer l'état terminé ». Ce que les étapes ont produit reste
lisible dans `etapes[].resultat` (livrable, `job_ids`, `tranches`, récit résumé)
et dans les sous-jobs (`/v1/jobs?enfants=1`) — c'est du travail, pas une
livraison. Une chaîne qui ne déclare aucun `livrable` garde l'ancien
comportement : tout ce qu'elle a produit est listé, faute de mieux.

Un contrôle faux **arrête** la chaîne (`problem_kind: "controle-echoue"`, avec
le mesuré ET l'attendu) ; **en échec**, les fichiers déjà produits restent dans
les artefacts, puisque c'est en les regardant qu'on comprend. Les pièces intermédiaires vivent
sous `<sortie>/cortex/_travail/<job>/` et ne sont jamais listées comme
livrables. Annuler le parent arrête le sous-job en cours par les moyens du
moteur et saute le reste.

### Rendu par tranches : la mémoire ne borne plus la durée

Un nœud qui tient toute une vidéo en mémoire — une image RVB en float32 par
image rendue — dépasse la mémoire du poste dès qu'un plan s'allonge. **Mesuré le
2026-09-15** : 2 258 images en 720×1280 (75 s à 30 i/s) demandaient **23,3 Gio
d'un coup, refusés** ; 1 980 images (20 Gio) passaient de justesse. Ce n'est pas
au demandeur de raccourcir sa vidéo ni de la rendre plus petite : la passerelle
demande le rendu d'**une seule et même simulation** en N tranches successives,
et les recolle.

**Ce qu'un nœud déclare.** Un nœud qui sait rendre une tranche porte, dans ses
entrées et **en littéral**, `segment_index` et `segment_count` : la tranche *i*
de *N* rend les images `[n·i/N, n·(i+1)/N)` de la même simulation — le grain,
la caméra et la lumière sont ceux de la seconde ABSOLUE, pas du début de la
tranche, sans quoi les parts ne se rejoindraient pas. Il déclare aussi de
combien il peut ALLONGER, puisque c'est lui qui décide de la durée retenue :
`duree_max_s`, la durée ABSOLUE au-delà de laquelle il n'allonge plus (le
déroulement, qui allonge un plan pour ne précipiter aucun temps), ou
`allonge_max_s`, de combien AU PLUS il allonge au-delà de la durée demandée
(une conclusion, qui garde la page vivante le temps que l'appel s'écrive :
`frames + allonge_max_s × fps` images, jamais une de plus — le nœud tient la
borne qu'il déclare). Une entrée **liée** à un autre nœud (`["12", 0]`) ne
compte pas : on ne peut pas y écrire. Sans `segment_index`/`segment_count`,
rien n'est tranché — trancher un graphe qui ne sait pas le faire rendrait N fois
la vidéo entière.

**Ce mécanisme ne connaît aucun projet.** Il ne lit que le graphe et son
budget : un nœud déclare la convention (`segment_index`/`segment_count`, une
borne d'allonge), la passerelle compte, lance, recolle par copie de flux, mesure
les jonctions et fusionne les récits par des règles de NOMS (`_min`, `_max`,
listes) — rien n'y nomme une chaîne, une technique ni un nœud du paquet de
révélation ; les épreuves du mécanisme tournent sur un nœud d'essai
(`RenduDEssaiParTranches`) qui n'existe nulle part ailleurs, et la règle
`flux-hors-du-code` du socle refuse tout nom de flux dans le code. Un autre
paquet de nœuds, un autre flux, une autre chaîne l'emploient tels quels.

**La taille.** Elle vient des réglages du run (`width`, `height`, `fps`) ou des
défauts déclarés du mode. Un graphe qui prend sa taille d'une **vidéo d'entrée**
(une conclusion reprend la queue du déroulement telle qu'elle est) ne la dit
pas : la passerelle la lit alors sur la vidéo elle-même — un livrable d'étape,
ou un dépôt qu'elle a fait chez le moteur et dont elle garde le chemin — et le
journal le dit (« taille lue sur la vidéo d'entrée « raccord-queue.mp4 » —
720×1280 à 30 i/s »). Rien de lisible : le run part entier, en le disant.

**Le budget.** Il vient du MATÉRIEL déclaré du poste (voir « Le matériel du
poste », plus bas) :

```
budget = (memoire.totale_octets − memoire.reservee_octets) / memoire.facteur_de_crete
images = ceil(max(duration_s + allonge_max_s, duree_max_s) × fps)
N      = ceil(images × largeur × hauteur × 12 / budget)
```

**La place du moment.** Le budget est déclaré, pas mesuré — mais le poste
change pendant un rendu de cinq heures. Mesuré le 2026-09-16 : un voisin a
chargé un modèle de 26 Go à la tranche 15/31 d'un 4K/60, et l'allocation de
8,6 Gio a été refusée avec 18 Gio de RAM physique libre — c'est la **limite de
commit** de Windows (RAM + fichier d'échange) qui était atteinte. Depuis, avant
chaque tranche, la passerelle mesure la marge du moment (le plus petit de la RAM
physique libre et du commit restant) et la compare au pic attendu (poids d'une
tranche × `facteur_de_crete`) : si ça ne tient pas, la tranche **attend** que
la place revienne, en le disant au journal (« le poste n'a que 6,8 Gio de marge
(physique 18,1 Gio, commit 6,8 Gio) pour un pic attendu de 32 Gio ; il garde en
ce moment 57 Go, plus que les 28 Go déclarés réservés — attente, jusqu'à
30 min »), puis refuse en disant pourquoi (`COMFY_ATTENTE_PLACE_S`, 1 800 s ;
pas de 30 s). Sans mesure possible, personne n'est retenu. `GET /v1/materiel`
porte `marge_du_moment_octets` et avertit quand le poste garde plus que le
déclaré.

Deux raffinements, pour ne pas attendre ce qu'on pourrait rendre. Au
découpage, le budget d'une tranche est **ramené à la marge du moment** quand
elle est plus petite que le déclaré (« budget ramené à 6,9 Gio par la marge du
moment (physique 34,6 Gio, commit 24,0 Gio), facteur 3,5 ») : plus de
tranches, jamais plus longues — et si même ainsi il en faudrait plus de 64,
c'est dit avant le premier run (« le poste garde en ce moment plus que ce que
materiel.local.json déclare : libérer la mémoire, puis relancer »). Et dès que
la première tranche est rendue, les suivantes attendent leur place sur les
images qu'une tranche rend **vraiment** (comptées sur le fichier), pas sur la
borne d'avant le run — un nœud qui déclare 79 s peut n'en retenir que 48.

Et le poste peut changer ENTRE la garde et l'allocation (mesuré : un modèle de
21 Go chargé pendant le rendu d'une tranche). Une tranche qui échoue alors que
la marge du moment ne tient pas le pic attendu est **reprise après attente**,
jusqu'à trois fois, en le disant (« la tranche 1/43 a échoué (…) alors que le
poste n'avait que 13,5 Gio de marge pour un pic attendu de 23,3 Gio — reprise
après attente (essai 1/3) ») ; une tranche qui échoue avec de la place a échoué
d'autre chose, et cet échec-là se dit tel quel, sans reprise.

`N ≤ 1` : le run part entier, comme avant. `N > 64` (la borne de `segment_count`) :
la demande est refusée **avant le premier run**, en le disant — c'est le poste
qui est hors de portée, pas le découpage qui manque. Le journal du job dit le
budget ET d'où il vient (« budget de 18 Gio par tranche, d'après
materiel.local.json ») : un découpage qui change parce qu'un fichier a bougé
doit se lire, jamais se deviner.

### Le matériel du poste

Antoine, 2026-09-16 : « les limitations matérielles du PC doivent être dans un
fichier de réconciliation, qui permet de faire les calculs pour que le workflow
sache ajuster son nombre d'itérations (au cas où la RAM du PC venait à
changer) ». Le nombre de tranches n'est pas un réglage de flux : il se **déduit**
de la mémoire de la machine. Écrit dans le code, il aurait fallu le rouvrir à
chaque barrette ajoutée ; écrit dans une chaîne, il aurait suivi le flux sur une
autre machine, où il aurait été faux.

`_data/materiel.local.json` (non versionné : il décrit UNE machine ; copie
d'exemple `resources/materiel.exemple.json`) :

```jsonc
{
  "memoire": { "totale_octets": 68719476736,     // la RAM du poste
               "reservee_octets": 30064771072,   // ce que le reste de la machine garde
               "facteur_de_crete": 2.0 },        // le pic d'un run, en fois le poids des images
  "memoire_graphique": { "totale_octets": 12884901888 },   // déclaré pour ce qui viendra
  "coeurs": 28                                             // idem : rien ne le lit encore
}
```

`facteur_de_crete` sort de mesures. Le 2026-09-15, à 720p, un run seul :
20 Gio d'images passaient avec 36 Go libres, 23,3 Gio échouaient — un pic à
≈ 1,6×. Le 2026-09-16, à 4K/60 rendu par tranches, la RAM libre échantillonnée
toutes les 30 s : 9,3 Gio d'images par tranche, **30 Gio au pic** — 3,2× — parce
que le moteur gardait en cache les images du run précédent pendant le suivant
(≈ 9 Gio) et que l'encodage copie. Le moteur est depuis lancé en
`--cache-none` par la commande déclarée dans `engines.local.json` (nos graphes
n'ont rien à réutiliser d'un run à l'autre), et la même mesure donne alors
**10,4 Gio au pic pour 7,8 Gio d'images** — 1,35×. D'où 2,0 : la marge sur le
mesuré. Avec les valeurs ci-dessus : (64 − 28) / 2 = **18 Gio par tranche**.

L'ordre, écrit une seule fois : **surcharge** (`COMFY_TRANCHE_GO`, pour un
essai — dite quand elle s'applique) > **fichier** > **repli de 8 Gio**, dit
lui aussi au démarrage et dans la route. Un fichier qui ne tient pas (totale à
zéro, réservée plus grande que la totale, facteur sous 1) est refusé **au
démarrage**, nommé — pas au premier rendu long.

`GET /v1/materiel` rend le déclaré, le **mesuré** (psutil s'il est installé,
sinon `GlobalMemoryStatusEx` sous Windows, sinon rien — et c'est dit), le budget
qui en découle, sa provenance, et les avertissements : « le fichier dit 512 Go,
le poste en a 64 » est l'erreur qu'on ne découvrait qu'en panne sèche. Une marge
de 5 % évite de crier sur les 0,13 % qu'un système garde toujours pour lui
(mesuré ici : 68 631 527 424 octets rendus pour 68 719 476 736 déclarés).

**Le recollage.** Les N parts viennent du même encodeur avec les mêmes réglages :
elles sont jointes par **copie de flux** (démultiplexeur `concat`, `-c copy`) —
aucune image n'est ré-encodée, et le livrable ne subit **aucune génération de
perte de plus** que le rendu d'un seul tenant. Si la copie refuse (des parts qui
ne se ressemblent pas assez), le recollage ré-encode, et le journal le dit :
« copie de flux impossible (…) — recollage ré-encodé », puis
« N tranches recollées en ré-encodant ». Les **jonctions sont mesurées** (SSIM
de la dernière image d'une part à la première de la suivante) et dites :
« jonctions mesurées : pire …, moyenne … ». Une jonction qui ne se mesure pas
n'invalide pas le livrable — elle se dit au journal.

**Les récits fusionnent.** Une étape `verifier` contrôle le récit sans savoir
qu'il a été rendu en N fois. Ce qui est **identique** d'une tranche à l'autre
est un fait du plan (la même simulation l'a écrit) et reste tel quel ; ce qui
**diffère** est une mesure prise sur les images de la tranche :

| dans le récit | ce que la fusion en fait |
|---|---|
| un nombre en `_min` / `_max` | le minimum / le maximum de toutes — et son instant `_s` est celui de LA tranche qui le porte |
| une liste | concaténée, dans l'ordre des tranches |
| un objet | fusionné de même, récursivement |
| tout autre scalaire qui diffère | la valeur de la **première** tranche, et la clé est nommée dans `tranches_divergentes` |
| une clé qu'une seule tranche porte | absente ou de la première tranche, et nommée dans `tranches_divergentes` |

Le récit fusionné porte `tranches` (le compte). Un contrôle sait ainsi ce qu'il
lit : une valeur nommée dans `tranches_divergentes` n'a pas été mesurée sur
toute la vidéo.

**Ce que l'étape en montre** : `job_ids` (un par tranche — ce sont des runs
ordinaires, visibles dans `/v1/jobs`, chacun avec `parent`), `tranches` (le
compte), `note` (« tranche 2/3 » pendant, « 3 tranches » après) et, dans son
résultat, `jonctions` (`pire`, `moyenne`, `nombre`). Un arrêt demandé au parent
est honoré **entre deux tranches** comme il l'est pendant un run.

**Pour n'importe quel appelant.** Le découpage ne regarde ni le mode, ni sa
catégorie, ni qui appelle : seulement le graphe (un nœud qui déclare la tranche)
et le budget. Un mode de maestro de n'importe quelle catégorie, un appel d'API,
un rejeu — un `POST /v1/render` sur un graphe qui déborde devient **une chaîne
d'une seule étape « rendu »** dont le livrable est le recollage des tranches :
mêmes journaux, mêmes `etapes[].job_ids` / `tranches`, mêmes sous-jobs visibles
dans `/v1/jobs`. Le job accepté annonce déjà son étape, comme une vraie chaîne.
Sans cela, le même nœud tenait en mémoire appelé depuis une chaîne et débordait
appelé seul. Hermes est consulté **avant** la première tranche (un souvenir qui
tient encore refuse en 422, pas au troisième run), les champs hors modèle
restent refusés comme avant, et un rendu qui tient d'un seul tenant reprend le
chemin ordinaire — sans étapes, un appelant ne voit rien changer. Une demande
qui dépasserait 64 tranches est refusée en **422** : c'est la demande qui est
hors de portée du poste, et le message dit quoi baisser.

**Sans bruit d'encodage visible.** Les tranches sont recollées par copie de
flux : **aucune image n'est ré-encodée**, donc aucune génération de perte de
plus qu'un rendu d'un seul tenant. Une jonction tombe sur une **image-clé** —
un rendu entier en porte déjà une toutes les 250 images (mesuré sur un rendu de
1 539 images : I aux images 1, 251, 501, 751, 1001), si bien qu'une frontière de
tranche ne se distingue pas des frontières que l'encodeur pose de lui-même. Et
depuis le 2026-09-16, les graphes **intermédiaires** de la révélation
(déroulement, conclusion, appel, encre et brume) encodent en **h264 crf 10**
(quasi sans perte) au lieu du crf 23 par défaut : mesuré à 435 kb/s sur un
rendu 352×640, le grain du papier était mangé **avant** le montage final à
crf 18 — la perte ne venait pas du recollage mais de l'intermédiaire. Réglé dans
`_data/workflows/*.json` (nœud `SaveVideo` : `codec` `h264`, `codec.encoding`
`re-encode`, `codec.encoding.crf` `10`).

### Une chaîne ne nomme aucune technique

Antoine, 2026-09-16 : « la mention de brume ne doit pas être tenue par le
workflow de la passerelle : cela veut dire qu'il porte une dépendance à la brume
et devra se faire doublon pour faire autrement ». `video-revelation` (encre) et
`video-revelation-brume` étaient deux chaînes **jumelles** : même plan de onze
étapes, seules la peinture, la conclusion et leurs contrôles `plan_tenu`
différaient. Une technique de plus était une chaîne de plus, recopiée.

Une **chaîne** est donc le PLAN, agnostique : ses étapes nomment des **rôles**
(`deroulement`, `conclusion`, `appel`), jamais un graphe de peinture ni
d'écriture, et elle expose un champ dont la liste est celle des techniques
déclarées — **sans défaut** : c'est la technique qui se dit `par_defaut` dans
son fichier (deux qui se le disent sont refusées à la lecture ; aucune : la
première par son nom). Une **technique**
(`_data/techniques/<nom>.json`, copie de référence `resources/techniques-exemples/`)
dit quel graphe tient chaque rôle et avec quelles entrées de nœud, quels réglages
elle ajoute, et quels contrôles elle porte :

```jsonc
// la chaîne : le plan — aucun nom de technique, ni par un graphe, ni par un défaut
"expose": { "…": "…", "technique": { "type": "COMBO",
                                     "options_depuis": { "techniques": true } } },
"etapes": [
  { "id": "deroulement", "rendre": { "role": "deroulement", "technique": "$technique",
      "media": { "image": "$image" }, "duration_s": "$duration_s" } },
  { "id": "plan_tenu",   "verifier": { "technique": "$technique", "controles": "plan_tenu" } },
  { "id": "appel", "quand": "$cta", "rendre": { "role": "appel", "technique": "$technique",
      "media": { "video": "$conclusion.livrable" } }, "sinon": { "…": "…" } }
]

// la technique : ce qui tient les rôles, et ses propres réglages
{ "version": 1, "technique": "encre", "libelle": "Encre", "resume": "…", "par_defaut": true,
  "expose": { "fond": { "type": "COMBO", "defaut": "washi", "options": ["washi", "…"] },
              "bords": { "…": "…" }, "conduite": { "…": "…" } },
  "roles": { "deroulement": { "workflow": "video-reveal-cinematic-dirige",
                              "inputs": { "61.fond": "$fond", "61.conduite": "$conduite" } },
             "conclusion":  { "workflow": "video-reveal-closing", "inputs": { "…": "…" } },
             "appel":       { "workflow": "video-appel-final",
                              "inputs": { "7.texte": "$cta", "7.police": "$cta_police" } } },
  "controles": { "plan_tenu": [ { "id": "l_accroche_est_vue_a_2_5_s", "op": "gte" } ] } }
```

Antoine, 2026-09-16 au soir : « les paramètres — le style de tracé, le fond,
l'ambiance, la technique de style… — ne vivent pas dans le workflow mais se
réconcilient avec le workflow quand le paramètre l'appelle ; ce qui permet de
les interchanger, d'en créer de nouvelles, avec un rendu drastiquement différent
si le style l'est ». Le plan n'expose donc que ce que le PLAN lit (durées, appel,
structure, approche, format, graine, le choix de la technique) ; tout ce que la
peinture lit (`fond`, `ambiance`, `bords`, `conduite`, …) est exposé par la
technique qui le lit — l'encre expose `bords` et `conduite`, la brume `bords`
seulement : sous elle, « qui commande » n'existe pas.

* `rendre` nomme **soit** un `workflow`, **soit** un `role` + `technique` (un
  renvoi : la technique est choisie à l'appel, jamais écrite dans le plan) —
  jamais les deux. Le runner résout : le graphe est celui du rôle, et les
  `inputs` du rôle passent **sous** ceux de l'étape (le plan garde le dernier
  mot sur ce qu'il a écrit lui-même). Le journal le dit : « étape deroulement :
  technique encre → video-reveal-cinematic-dirige ».
* `verifier` accepte `{"technique": "$…", "controles": "<nom>"}` : la liste est
  celle de la technique — deux peintures ne se jugent pas sur les mêmes
  grandeurs. Et `{"controles": [...], "technique": "$…",
  "controles_de_la_technique": "<nom>"}` : la liste de l'étape **puis** celle
  que la technique porte sous ce nom — c'est ainsi que `plan_valide` juge le
  plan **avant de peindre** aussi sur ce que la technique exige de lui (l'encre
  déclare `plan` : un quart de temps tracés au moins,
  `$intention.recit.part_des_traces`, et une accroche que le temps suivant ne
  recouvre pas à plus de moitié, `$intention.recit.accroche_couverte_par_le_suivant`
  ; une technique qui n'exige rien déclare `"plan": []`). Mesuré le
  2026-09-17 : un plan à un tracé sur six temps avait été peint trente-deux
  minutes en 720p avant que `des_traits_et_pas_que_des_blocs` le refuse, et une
  accroche nichée dans le temps suivant vingt-cinq minutes avant
  `l_accroche_est_vue_a_2_5_s` (la saignée de l'encre s'arrête à la porte du
  temps suivant dès l'ouverture).
* `constater` porte les mêmes contrôles que `verifier`, dans les trois formes,
  et n'arrête rien : non tenus, ils sont ÉCRITS au récit de l'étape (`constat`,
  `non_tenus`, les lignes) et au journal, et la chaîne livre. Antoine,
  2026-09-17 : « il ne faut plus que maestro annonce des erreurs quand la
  vidéo est très bien, c'est l'utilisateur qui juge ». Dans « Révéler une
  image », `plan_tenu` (la peinture mesurée) est un constat ; `plan_valide`
  (le plan, avant de peindre) et `controle` (le livrable) restent des refus.
* Les **renvois** d'une technique se résolvent avec les champs de la chaîne, les
  siens et les résultats des étapes précédentes, comme dans une chaîne ; un rôle
  absent, une liste de contrôles absente ou un renvoi qui ne désigne rien sont
  refusés **à la lecture** (`verifier_techniques`), avant la première seconde de
  rendu.
* Les champs d'une chaîne = ses champs **communs** ∪ l'union des `expose` de
  toutes les techniques. Dans `/io`, un champ qu'une technique apporte porte
  `"selon": {"champ": "technique", "valeurs": ["encre"]}` — le lanceur ne le
  montre que sous ces techniques-là. Deux techniques peuvent exposer le **même
  nom** (`fond`) avec des options différentes : le champ porte alors
  `selon_options` et `selon_defauts` par technique, et la passerelle valide
  contre celle qui est choisie. Rien n'est écrit dans le lanceur : tout vient
  de `/io`.
* Un réglage d'une **autre** technique que celle choisie est **écarté et dit**
  au journal (« non appliqué — n'est pas un réglage de la technique encre »),
  jamais refusé : un raccourci enregistré sous une technique se rejoue sous une
  autre sans être cassé.
* `GET /v1/workflows` publie sur chaque chaîne
  `techniques: [{valeur, libelle, resume}]`.

**Ajouter une technique** : un fichier dans `_data/techniques/`, sa copie dans
`resources/techniques-exemples/`, qui tient les trois rôles (`deroulement`,
`conclusion`, `appel`), expose ses propres réglages et porte sa liste
`plan_tenu`. Rien dans la chaîne, rien dans le code — la règle
`flux-hors-du-code` du socle prend aussi les noms de techniques. Éprouvé le
2026-09-16 au soir, deux fois. D'abord `brume-et-encre` (le déroulement de la
brume, la fermeture et l'appel de l'encre, un réglage `papier` à elle) : déposée
dans `_data/techniques/`, la passerelle relancée — elle est au menu, ses champs
`selon` avec elle, maestro les montre ; ses deux productions d'essai ont été
REFUSÉES par le contrôle de caméra de la brume elle-même (« la_camera_ne_saccade_pas »,
1,56 puis 1,61 pour 1,0 admis — le travers connu de la brume sur cette œuvre) :
le câblage a joué, la technique a jugé sa peinture ; elle reste en exemple
(`resources/techniques-exemples/brume-et-encre.json`), hors du menu vivant. Puis
`livre` (voir ci-dessous) : deux nœuds neufs, deux graphes, deux entrées de
réconciliation, un fichier — et une production complète, sans qu'une ligne de
chaîne ni de code ait bougé.

**Les pages d'un livre** (`livre`, exemple `resources/techniques-exemples/livre.json`) :
Antoine, 2026-09-16 au soir : « essayer un nouveau mode : on tourne les pages d'un
livre pour découvrir petit à petit les éléments, mêmes étapes, même révélation
progressive — estimer à quel point la répartition socle / paramètres est
bonne ». Le paquet de nœuds porte `RevealLivre` (chaque temps du plan tourne une
page et découvre son élément ; la vue se resserre sur la page en cours puis
recule vers l'œuvre entière, découverte par la dernière page) et `LivreClosing`
(le livre se referme, la couverture reste vivante pour l'appel) ; la technique
expose `papier` et porte SES contrôles (pages, couverture, contemplation). Le
plan lu est celui de l'intention — `beats`, `temps`, `structure` — tel quel.

Deux chaînes sont livrées.

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
| `appel` | l'appel final (`cta`) écrit à l'encre quand la fermeture a fini : il ne mord que sur sa dernière seconde, puis reste le temps de se lire, déduit du texte (la conclusion reçoit le même texte par `6.appel_texte` et prolonge sa page refermée, vivante, d'autant) ; la police s'injecte par `cta_police` (nom ou chemin). **Depuis le 2026-09-16, il lit la conclusion PARESSEUSEMENT** (entrée `video` du nœud) et ne rend QUE les images qu'il écrit — en 4K, charger la conclusion entière pour quelques secondes d'encre était une vidéo de plus en mémoire ; son récit dit combien d'images il a reprises (`images_reprises`). Sans texte, l'étape est **sautée** (`"quand": "$cta"`) et son `sinon` rend un livrable nul et zéro image reprise |
| `montage` | déroulement + conclusion **sans les images que l'appel a reprises** (`sauf_les_dernieres`) + appel : trois parts, ou deux quand l'appel est sauté (sa part est ignorée, et rien n'est rogné) |
| `controle` | le montage a ses parts (2 ou 3), et un livrable qui pèse |

Les graphes qu'elle enchaîne (`image-iconographe`, `image-iconologue`,
`image-intention`, `video-reveal-cinematic-dirige`, `video-reveal-closing`,
`video-appel-final`) et `video-still-motion` restent des **techniques**, sans
catégorie : le lanceur ne les montre pas.

La technique **brume** — « Révéler une image par la brume », l'ESSAI d'une autre
peinture, était jusqu'au 2026-09-16 une chaîne jumelle (`video-revelation-brume`)
qui recopiait les onze étapes pour n'en changer que deux. C'est maintenant une
technique, `_data/techniques/brume.json`, choisie par le champ `technique` du
même mode. Son rôle `deroulement` appelle `video-reveal-brume-dirige` (nœud
`RevealBrume`) : l'image est déjà là, **entière et en couleur**, sous une nappe
de bruit fractal animé qui se dissipe selon le même champ d'heures narratif ;
sous la brume elle est floue et désaturée d'autant qu'elle est couverte. Son
rôle `conclusion` appelle `video-reveal-brume-closing` (nœud `BrumeClosing`) :
la brume revient depuis les bords vers le climax, **repris en dernier** (son
heure de retour n'est que sa distance au foyer : rien n'est découpé), avec la
MÊME brume que le déroulement et une nappe qui reprend sa dérive à la seconde où
celui-ci s'est arrêté (`6.depart_s`). La dernière image est une **page de brume
claire** — luminance 0,82 pour un contrat à 0,80 —, celle sur laquelle l'appel
final écrit son encre sombre. Son champ `fond` choisit la teinte de la brume
(blanche, grise, dorée) — le même NOM que sous l'encre, d'autres valeurs ; ni
`encre`, ni `rendu`, ni `ambiance`, ni `negatif`. Ses contrôles `plan_tenu`
mesurent les mêmes grandeurs que l'encre quand elles ont un sens, **sur la carte
de densité que le nœud vient de rendre** : accroche vue, climax hors de
l'ouverture et tenu, caméra qui glisse sans saccade, temps lisibles (boîte et
halo sous 0,3 de densité à leur heure), ordre d'arrivée, cœur en dernier, aucun
temps supprimé. Le champ commun `conduite` ne lui est pas passé : la brume ne
sait se dissiper que le long d'un chemin narratif, et son nœud porte « le plan »
en littéral — ce qu'un appelant choisit là n'est pas appliqué sous la brume, et
la passerelle le DIT au journal.

`video-prolongement` — 17 dernières images → prolongement → mesure du raccord
→ recollage sans le chevauchement.

### Le socle ink — ce qui est figé, ce qui se règle, où ajouter

Antoine, le 2026-09-15 à midi, sur les vidéos ink livrées : « c'est parfait —
assure cette standardisation ». Trois couches, et un témoin exécutable
(`tests/test_socle_ink.py`) qui les épingle :

| couche | ce que c'est | où |
|---|---|---|
| **le plan** | agnostique, hors de tout style : les onze étapes, dans cet ordre, avec leurs genres — `analyse` → `culture` → `intention` → `plan_valide` → `deroulement` → `plan_tenu` → `raccord` → `conclusion` → `appel` → `montage` → `controle` — et le contrat par lequel chacune parle à la suivante (`$etape.recit.*`, `$etape.livrable`, `$raccord.depot` ; la fermeture reçoit `fermeture_json` du récit du déroulement au lieu de relire le disque). Les deux étapes qui PEIGNENT nomment un rôle, jamais un graphe : c'est la technique choisie qui les tient. | `_data/chaines/*.json` et leurs jumeaux |
| **les paramètres** | tout ce qui se règle, chaque champ chez son propriétaire, avec sa rubrique (`categorie`) et son `aide` : ceux du PLAN dans la chaîne — structure du récit (seules les structures qui portent une accroche, `requiert`), approche, contemplation, conclusion, CTA et sa police, format, graine, technique — et ceux de la TECHNIQUE dans son fichier (l'encre : papier, tracé, négatif, ambiance ; la brume : sa teinte ; le livre : son papier). **Leurs défauts sont ceux du style ink livré le 2026-09-15 et ne changent pas** : `reseau-social`, `peinture-calme`, `washi`, `lanterne`, `lavis`, négatif `non`, contemplation 4 s, conclusion 8 s, 45 s, 720×1280, 30 i/s, graine 71. Depuis le 2026-09-17, une option « d'avant » ou « essai » n'est plus une option : `rendu`, `bords`, `conduite` ne sont plus exposés (le graphe porte `ink-bleed`, `fondus`, `le plan` en littéral) | `expose` de la chaîne et des techniques ; littéraux du graphe local `video-reveal-cinematic-dirige` |
| **les styles** | ce qu'on ajoute sans rien casser : un style narratif ou une approche dans les catalogues de `comfyui-direction-de-style` (`styles/narratifs.json`, `styles/approches.json`), un fond, une encre, une ambiance, un rendu, un négatif dans les tables du paquet de nœuds (`FONDS`, `ENCRES`, `AMBIANCES`, `RENDUS`, `NEGATIFS`), une brume dans `BRUMES`. Une **entrée de plus**, jamais un défaut de moins ; le défaut reste en tête de chaque liste | les catalogues et les tables |

**Une exception, écrite** : le FORMAT par défaut est passé du 704×1280 à 25 i/s
au portrait 720p à 30 i/s le 2026-09-15 au soir, à la demande d'Antoine
(« valeurs par défaut : portrait, 720p, 30 i/s »). Un format est un réglage
d'usage — la taille et la cadence que les réseaux attendent — pas un trait du
style ink : rien de ce que le style fait n'en dépend, et le nœud d'encre accepte
un pas de 8 (720 = 90 × 8). Le témoin épingle les nouveaux défauts, à la même
condition que les autres : les changer est une décision à écrire ici.

Le témoin refuse tout écart : plan, défauts, bornes, options (le défaut en tête,
rien de retiré), contrat de chaque étape, listes de contrôles, littéraux des
graphes locaux quand ils sont là. Le paquet de nœuds a le sien
(`tests/test_socle_ink.py` de `comfyui-ink-reveal`) pour les défauts des nœuds
et les constantes du rendu.

**La même base sous trois techniques, prouvée** (2026-09-17, Antoine :
« prouve par des tests qui seraient drastiquement différents mais prouvent que
la base est la même ») : `tests/test_meme_socle_trois_techniques.py` fait
tourner la chaîne de référence et les techniques de référence (encre, brume,
livre, et l'hybride) de bout en bout, avec la même demande, sur un moteur
d'essai qui honore les tranches et ne sait rien des techniques. Identique chez
toutes, mesuré : les onze étapes, l'amont (analyse, culture, intention), les
réglages du run et les entrées du plan qui atteignent le nœud de rôle (relevé,
plan, contemplation), trois tranches au déroulement, les contrôles du plan, un
seul livrable de trois parts et de la même durée. Différent chez les trois,
mesuré : les nœuds appelés, les réglages qui atteignent le nœud (le livre n'a
pas une clé en commun avec les deux autres ; l'encre et la brume partagent un
nom, « fond », pour d'autres valeurs), les grandeurs que « plan_tenu » juge. Le
paquet de nœuds porte le jumeau sur les IMAGES (même nom de fichier) : même
plan, même œuvre, trois rendus qui s'écartent de 0,12 à 0,24 par pixel à chaque
instant du corps, et la même base tenue — 75 s livrées, temps nommés d'après la
structure, ordre du plan, cœur en dernier, contemplation, contrat de fermeture,
et trois conclusions qui prolongent leur page du même temps pour le même appel.

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
* `formats` (clé de premier niveau) : le **vocabulaire** des formats — les cas
  généraux, leurs libellés, leurs côtés —, rendu tel quel par `GET /v1/workflows`
  à côté de `categories` :

  ```jsonc
  "formats": {
    "orientations": [ { "valeur": "portrait", "libelle": "Portrait (vertical)" },
                      { "valeur": "paysage",  "libelle": "Paysage (horizontal)" } ],
    "resolutions":  [ { "valeur": "720p", "libelle": "720p (HD)",
                        "cote_court": 720, "cote_long": 1280 }, … ]
  }
  ```

  Le lanceur en fait deux listes (Orientation, Résolution) et **traduit** le
  choix en `width`/`height`, qu'il envoie ; ni `format` ni `orientation` ne
  partent dans la demande (la passerelle les refuserait : ce ne sont pas des
  champs du mode). La règle de traduction est **géométrique**, pas métier —
  portrait ⇒ la largeur est le petit côté, paysage ⇒ le grand —, ce qui la garde
  vraie quand le vocabulaire s'allonge. Aucun défaut n'est écrit ici : le défaut
  d'un mode est celui de SES champs `width`/`height`/`fps` (`/io`), et le lanceur
  retrouve le format par défaut en cherchant le couple correspondant (720×1280 ⇒
  720p portrait ; hors vocabulaire ⇒ « Personnalisée »). Une résolution sans
  `valeur` ou sans ses deux côtés entiers est refusée **à la lecture**, nommée :
  servie tronquée, elle aurait donné une liste où un choix n'écrit rien. Sans
  déclaration : `{"orientations": [], "resolutions": []}` — le lanceur ne montre
  pas les listes, et largeur/hauteur restent des champs ordinaires.
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

### Raccourcis : un ensemble de réglages enregistré

Un mode publie ses champs et leurs défauts. Un utilisateur qui a trouvé SON
réglage — un fond ambré, plus de trait, une flamme étroite, 45 s — n'avait
aucun moyen de le garder : il le retapait, et le retapait faux. Un **raccourci**
est ce réglage-là, nommé, avec l'aperçu animé de la livraison qui l'a fait
naître. **Le mode lui-même est le raccourci par défaut** : ses défauts ne sont
écrits nulle part, et il s'affiche toujours en premier ; les raccourcis
enregistrés viennent après lui, sous le même groupe.

Le lanceur ne fait que DÉSIGNER (« garde ces réglages-là ») et RENDRE ce qui
est publié — aucune liste écrite chez lui, aucune image fabriquée par lui.

```jsonc
{
  "id": "sepia-au-trait-sec",          // le slug du titre ; « -2 » si le titre existe déjà
  "workflow": "…", "titre": "Sépia au trait sec", "resume": "…",
  "valeurs": { "duration_s": 45, "fond": "sepia", "…": "…" },
  "ecarts": [                          // ce qui DIFFÈRE des défauts du mode, habillé
    { "champ": "fond", "libelle": "Fond de départ",
      "valeur": "sepia", "libelle_valeur": "Sépia" },
    { "champ": "duration_s", "libelle": "Durée", "valeur": 45, "libelle_valeur": "45 s" }
  ],
  "apercu_url": "/v1/workflows/…/raccourcis/sepia-au-trait-sec/apercu",
  "job_id": "1f04…", "cree_le": "2026-09-15T20:10:00+00:00", "ordre": 100
}
```

`valeurs` porte TOUS les réglages, jamais les pièces jointes (l'image se
redépose à chaque fois) ni ce que la passerelle possède elle-même (`workflow`,
`label`, `kind`, `media`, `inputs`, `constraints`). `ecarts` est calculé **à la
lecture**, contre les défauts d'aujourd'hui : figé dans le fichier, il aurait
continué d'annoncer « Sépia » comme un choix particulier le jour où le mode en
fait son défaut. Une liste d'écarts vide se lit « les réglages par défaut ».
Quand `width` ET `height` diffèrent tous deux et que le couple correspond à un
format déclaré (voir `formats` plus haut), les deux écarts sont repliés en un
seul — `{"champ": "format", "libelle": "Format", "valeur": "1080p paysage",
"libelle_valeur": "1080p (Full HD), Paysage (horizontal)"}` — à la place du
premier des deux : l'utilisateur a fait UN choix, pas deux. Hors vocabulaire
(704×1280) ou sur un carré, les deux écarts restent (« 704 px »).
`apercu_url` n'est annoncé que si le fichier existe — une vignette promise et
absente fait une image cassée par carte.

| Route | Corps | Réponse |
|---|---|---|
| `GET /v1/workflows/{nom}/raccourcis` | — | `{"workflow", "raccourcis": [vue…]}`, triés par `ordre` puis titre |
| `POST /v1/workflows/{nom}/raccourcis` | `{"titre"` requis`, "resume"?, "job_id"?, "valeurs"?, "ordre"?}` — au moins `job_id` ou `valeurs` | **201** la vue |
| `GET /v1/workflows/{nom}/raccourcis/{id}` | — | la vue ; inconnu → **404** problem+json |
| `PUT /v1/workflows/{nom}/raccourcis/{id}` | les mêmes champs ; seuls ceux PRÉSENTS changent, `valeurs` remplace tout, `job_id` refait l'aperçu | **200** la vue |
| `DELETE /v1/workflows/{nom}/raccourcis/{id}` | — | `{"workflow", "id", "removed"}` — 200 même s'il n'existait pas |
| `GET /v1/workflows/{nom}/raccourcis/{id}/apercu` | — | le fichier (`image/webp` ou `image/gif`) ; sans fichier → **404** |
| `GET /v1/workflows` | — | chaque entrée porte `"raccourcis": [vue…]` (vide sinon) |

**La passerelle fait autorité sur les valeurs** : ce qu'un mode refuserait au
lancement, il le refuse à l'enregistrement — champ qu'il n'expose pas (422,
nommé), valeur hors bornes ou hors menu (422, avec `field`) —, et ce qui est
gardé est TYPÉ. Sans ce contrôle, un raccourci gardait une durée que le mode
plafonne et n'échouait qu'au lancement, longtemps après avoir été nommé. Une
livraison désignée doit être celle DE CE MODE (sinon 422, les deux nommés) ;
un run inconnu est un 404 ; un run qui n'a livré aucune vidéo n'est pas une
erreur — il n'a simplement rien à montrer.

Où ça vit : `_data/raccourcis/<mode>/<id>.json` et son aperçu `<id>.webp|.gif`,
à côté de `_data/apercus/` — écrits de côté puis remplacés d'un coup, comme
l'aperçu d'un mode (une fiche relue pendant sa réécriture serait tronquée). Par
l'API, jamais à la main.

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
| les LIMITES du poste (RAM, VRAM, cœurs) — donc le BUDGET MÉMOIRE d'une tranche | `_data/materiel.local.json` (copie d'exemple `resources/materiel.exemple.json`) ; `COMFY_TRANCHE_GO` ne reste qu'une surcharge d'essai. C'est une propriété du POSTE, jamais de la chaîne ni du flux | le rendu par tranches, `GET /v1/materiel` |
| la QUALITÉ d'un INTERMÉDIAIRE (ce qu'un graphe écrit avant le montage final) | le nœud `SaveVideo` du graphe, dans `_data/workflows/<nom>.json` : `codec`, `codec.encoding`, `codec.encoding.crf` (10 sur les graphes de la révélation) | le moteur, à l'écriture du fichier |
| ce que l'utilisateur VOIT d'un MODE (titre, catégorie de la vitrine, résumé, libellés des valeurs) | `_data/reconciliation.local.json` : `titre`, `categorie`, `menus` ; `aides` sur l'entrée d'un GRAPHE seulement | `/v1/workflows`, `/io` |
| ce que l'utilisateur VOIT d'un CHAMP (sa rubrique, son aide) et la liste qu'il offre | le champ lui-même, chez son propriétaire — `expose` de la chaîne pour les réglages du plan, `expose` de la technique pour les siens : `categorie` (une valeur de `categories_de_champs`, vocabulaire déclaré une fois dans `_data/reconciliation.local.json`, avec `titre` et `repliee`), `aide`, et pour une liste tirée d'un menu `options_depuis: {"menu": …, "requiert": {…}}` qui ne garde que les lignes qui portent ce que le plan exige | `/io` (`categorie`, `aide`, `options`), `/v1/workflows` (`categories_de_champs`) |
| une TECHNIQUE (quel graphe tient chaque rôle, ses réglages, ses contrôles) | `_data/techniques/<nom>.json` (et sa copie `resources/techniques-exemples/`) — une technique de plus est un FICHIER de plus | le runner de chaînes, `/v1/workflows` (`techniques`), `/io` (`selon`) |
| un RACCOURCI (un ensemble de réglages nommé, son aperçu) | `_data/raccourcis/<mode>/` — par l'API, jamais à la main | `/v1/workflows`, `…/raccourcis` |
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
| `COMFY_TRANCHE_GO`  | `0`                       | SURCHARGE d'essai du budget d'une tranche, en Gio. `0` = d'après `_data/materiel.local.json` (repli 8 Gio, dit) |

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
