# comfyui-bridge — ce qu'un thread doit savoir avant de toucher un flux

Ce dépôt est la PASSERELLE : c'est ici que vivent les flux de création (graphes,
montages, chaînes), leur vitrine et leur nommage. Le lanceur `maestro`
(`E:\Claude Code\Programmes\maestro`) ne fait que montrer ce que cette passerelle
publie — il ne porte aucun nom de flux, aucune liste, aucune étape.

**Où modifier un flux** : la table « Modifier un flux de création » du
[README](README.md) dit, par nature de changement, le fichier à toucher
(`_data/workflows/`, `_data/chaines/`, `_data/reconciliation.local.json`, le
paquet de nœuds). Jamais dans un `.py` : la règle `flux-hors-du-code` de
`garde.json` le refuse au commit, dans ce dépôt comme dans le lanceur.

**Ce que le lanceur lit, et qui ne doit pas casser sans le dire** :
- `GET /v1/workflows` : `categories`, `formats` (premier niveau : le vocabulaire
  des formats — `orientations[]` et `resolutions[]` avec `valeur`, `libelle`,
  `cote_court`, `cote_long` ; deux listes vides quand rien n'est déclaré. Le
  lanceur les rend et TRADUIT le choix en `width`/`height` : portrait ⇒ la
  largeur est le petit côté ; ni `format` ni `orientation` ne partent dans une
  demande), et par entrée `presentation` (`titre`,
  `resume`, `categorie` — sans catégorie l'entrée est technique, invisible —,
  `ordre`, `apercu_url`), `runnable`, `warned`, `chaine`, `etapes[].workflow`,
  et `raccourcis[]` (les ensembles de réglages enregistrés sous ce mode :
  `id`, `titre`, `resume`, `valeurs`, `ecarts[].{champ,libelle,valeur,libelle_valeur}`,
  `apercu_url` s'il existe, `job_id`, `ordre` — liste vide sinon ; le MODE
  lui-même, avec ses défauts, est le raccourci implicite, affiché en premier) ;
- `GET /v1/workflows/{nom}/io` : `intent_inputs[]` (`field`, `type`, `value`,
  `min`, `max`, `step`, `options`, `choix`, `libelle`, `unite`, `aide`,
  `derived`, `requis`) et `media_inputs[]` (`param`, `category`, `label`,
  `accept`, `carried`) — le formulaire est bâti uniquement dessus ;
- `POST /v1/render` (corps plat, `label` = nom de la production),
  `POST /v1/estimate`, `GET /v1/jobs`, `GET /v1/jobs/{id}` + `/events`,
  `POST /v1/jobs/{id}/cancel` et `/rejouer`, `PUT /v1/workflows/{nom}/apercu`,
  `GET|POST /v1/workflows/{nom}/raccourcis` et
  `GET|PUT|DELETE /v1/workflows/{nom}/raccourcis/{id}` (+ `…/{id}/apercu`) —
  le lanceur DÉSIGNE (une livraison devient un raccourci), la passerelle
  valide, range et fabrique l'image ;
  un job porte `demande`, `etapes` (chaque étape rendue : `resultat`, dont
  `recit`, le récit compact écrit par le nœud — ce sur quoi l'étape `verifier`
  de la chaîne a jugé), `artifacts[].{kind,path,url}`, `problem`.
Les tests `tests/test_chaines_api.py` tiennent ces formes ; une chaîne modifiée
dans `_data/chaines/` doit garder sa copie `resources/chaines-exemples/`.

**Ce qui est agnostique est APPELÉ, jamais ancré dans un flux.** Une étape
qu'on peut nommer sans nommer le flux est un mécanisme à part : une entrée de
catalogue que la chaîne appelle, et qu'un autre flux appellera. Aujourd'hui,
cinq : la DOCUMENTATION d'une image (`image-iconographe` → le service
**Iconographe**, `E:/Claude Code/Programmes/Iconographe`, `127.0.0.1:7940` : il
documente ce que l'image MONTRE, et sa BIBLIOTHÈQUE fait qu'une œuvre n'est jamais
analysée deux fois — sha256 exact, puis empreinte perceptuelle), la CULTURE d'une
œuvre (`image-iconologue` → le service **Iconologue**, `E:/Claude
Code/Programmes/Iconologue`, `127.0.0.1:7950` : il enquête sur des sources
publiques et dit ce que l'œuvre EST — identité prouvée, notice, récit, sens des
motifs, une attestation par élément — et son CATALOGUE fait qu'une œuvre n'est
jamais enquêtée deux fois ; l'ancrage en ressort enrichi, et le climax retombe
sur la figure que les bases déclarent sujet), la STRUCTURE du récit (catalogue de
`comfyui-direction-de-style`, servie au formulaire par
`"options_depuis": {"menu": "style_narratif"}` et au graphe par le champ
sémantique `style_narratif`), l'APPROCHE (catalogue du même paquet,
`styles/approches.json`, servi par `"options_depuis": {"menu": "style_approche"}`
et lié au graphe par `70.style_approche` : elle dit COMMENT la révélation se
conduit — combien de temps, combien de temps chacun tient, ce que la caméra
s'autorise), l'APPEL FINAL (`video-appel-final`, qui vaut pour
n'importe quelle vidéo). Une chaîne ne recopie donc jamais une liste ni une
documentation : elle dit d'où elles viennent.

**Le socle ink est figé** (2026-09-15, « c'est parfait — assure cette
standardisation ») : le plan des onze étapes est agnostique, tout le reste est un
paramètre dont le DÉFAUT est celui du rendu ink livré ce jour-là ; un style de plus
est une entrée de plus dans un catalogue ou une table, jamais un défaut de moins.
Une SEULE exception depuis : le FORMAT par défaut est passé au portrait 720p à
30 i/s (`width` 720, `height` 1280, `fps` 30) le 2026-09-15 au soir, à la demande
d'Antoine — un format est un réglage d'usage, pas un trait du style, et rien de
ce que le style fait n'en dépend. Le témoin `tests/test_socle_ink.py` (ici, et
dans `comfyui-ink-reveal`) épingle plan, défauts, contrat des étapes et
constantes : le faire échouer est une décision à écrire, voir la section « Le
socle ink » du README.

**Une étape peut ne livrer aucun média** : un run qui n'écrit qu'un `.json`
réussit, et ce fichier est son livrable comme son `recit` (`_principal`,
`adapter/chaines.py`). C'est le cas de `analyse` et `intention`.

**Un `rendre` peut être rendu en TRANCHES par la passerelle** — et **pour un
graphe appelé directement aussi** (`POST /v1/render`, un mode de n'importe quelle
catégorie, un rejeu : le graphe devient une chaîne d'une seule étape « rendu ») —
quand le nœud le déclare (entrées littérales `segment_index` + `segment_count`,
et `duree_max_s` qui borne le compte) et que la mémoire l'oblige
(`COMFY_TRANCHE_GO`, 8 Gio par défaut) : N runs d'une même simulation, recollés
par copie de flux (aucune image ré-encodée), récits fusionnés. Le job porte alors
`etapes[].job_ids` et `etapes[].tranches` (un `job_id` par tranche, tous visibles
dans `/v1/jobs`) ; voir « Rendu par tranches » dans le README.

**Nommage des fichiers livrés** (règle générale, tenue ici) :
`cortex/<nom donné>_<type>…` — le type est le workflow, ou la chaîne pour un
livrable final (`<nom>_<chaine>-final_<id>`), et chaque étape rendue porte
`<nom>-<étape>_<workflow>`. Sans nom donné, le type seul.

Ne jamais relancer la passerelle (8077) pendant qu'un run tourne
(`GET /v1/engine/queue` : `running` et `pending` vides d'abord) ; relancer par
`start-bridge-silent.vbs`, prouver le changement de PID par le port.
