# ComfyUI Bridge

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
python -m comfyui_bridge serve --port 8000
#   Console (UI)  : http://127.0.0.1:8000/ui
#   Docs OpenAPI  : http://127.0.0.1:8000/docs

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
| GET     | `/v1/jobs/{id}`                | État du job + artefacts + **journal (logs)**     |
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
curl -X POST http://127.0.0.1:8000/v1/render -H 'content-type: application/json' -d '{
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
`seed`/`steps`/`cfg`, `filename_prefix`. Ce qui n'est pas identifié reste non lié
(le workflow garde sa valeur). **Vérifie le mapping déduit** avec `POST /v1/preview`
ou `GET /v1/workflows/<nom>` avant de t'en servir ; pour un contrôle fin, déclare
une entrée explicite dans `reconciliation.json` (elle a priorité).

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
disparues, déplacées d'un nœud à l'autre, et changement de type de média.
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

## Hermes — réconciliation contre les problèmes CONNUS

Hermes est un **rôle de réconciliation** appelé en **mode local**. Sa valeur est
la mémoire, pas la ruse : avant un run, il consulte ce qui a **déjà mal tourné
ici** et ce qui est **vérifiable d'avance**. Rien n'est inventé — aucune
heuristique de VRAM, aucun seuil deviné. Un registre vide signifie honnêtement
« aucun problème connu », et le run passe.

Il refuse un run quand :

1. une **dépendance manque vraiment** sur l'hôte (modèle ou type de nœud absent,
   vérifié via `/object_info`) — uniquement si le moteur va réellement tourner ;
2. cette **même configuration a déjà échoué ici** pour une cause qui se
   reproduirait (`oom`, `missing-model`, `missing-node`, `blocked-by-os`). Le
   refus **cite le problème passé**. Un succès ultérieur sur la même
   configuration efface cette mémoire (l'hôte a changé).

Les causes non reproductibles (`timeout`, `unreachable`, `workflow-error`) sont
remontées comme **contexte**, jamais comme refus.

**Ce qui est observé** : chaque run réel est journalisé avec son résultat, sa
configuration (`largeur x hauteur x images`) et, en cas d'échec, la **cause
classée depuis le message réel** du moteur plus ce message verbatim. La zone
d'observation est ainsi honnête : elle ne réduit plus toute panne à « OOM ».

**Localité** : la base vit dans le dossier privé `_data/` de CE pipeline et
chaque ligne porte un `scope` (`HERMES_SCOPE`, défaut `comfyui`) sur lequel
toutes les lectures filtrent — pas de fuite ni de pollution entre pipelines.

Consultation : `GET /v1/hermes/runs` (ce qu'il sait) et
`GET /v1/hermes/problems?workflow=…` (problèmes d'un workflow).

## Ajouter un workflow

Aucun code à toucher : on ajoute une entrée au fichier de réconciliation (voir la
section ci-dessus) — un graphe ComfyUI (format API) + sa table de bindings
`param → { node, input }` + ses `defaults`. Un paramètre sans binding est ignoré
(ex. `fps` sur un workflow d'image fixe) ; un binding pointant vers un nœud/entrée
absent lève une erreur explicite (`workflow-mapping`, `500`) plutôt que d'échouer
en silence.

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
