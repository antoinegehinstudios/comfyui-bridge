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
- `GET /v1/workflows` : `categories`, et par entrée `presentation` (`titre`,
  `resume`, `categorie` — sans catégorie l'entrée est technique, invisible —,
  `ordre`, `apercu_url`), `runnable`, `warned`, `chaine`, `etapes[].workflow` ;
- `GET /v1/workflows/{nom}/io` : `intent_inputs[]` (`field`, `type`, `value`,
  `min`, `max`, `step`, `options`, `choix`, `libelle`, `unite`, `aide`,
  `derived`, `requis`) et `media_inputs[]` (`param`, `category`, `label`,
  `accept`, `carried`) — le formulaire est bâti uniquement dessus ;
- `POST /v1/render` (corps plat, `label` = nom de la production),
  `POST /v1/estimate`, `GET /v1/jobs`, `GET /v1/jobs/{id}` + `/events`,
  `POST /v1/jobs/{id}/cancel` et `/rejouer`, `PUT /v1/workflows/{nom}/apercu` ;
  un job porte `demande`, `etapes` (chaque étape rendue : `resultat`, dont
  `recit`, le récit compact écrit par le nœud — ce sur quoi l'étape `verifier`
  de la chaîne a jugé), `artifacts[].{kind,path,url}`, `problem`.
Les tests `tests/test_chaines_api.py` tiennent ces formes ; une chaîne modifiée
dans `_data/chaines/` doit garder sa copie `resources/chaines-exemples/`.

**Ce qui est agnostique est APPELÉ, jamais ancré dans un flux.** Une étape
qu'on peut nommer sans nommer le flux est un mécanisme à part : une entrée de
catalogue que la chaîne appelle, et qu'un autre flux appellera. Aujourd'hui,
trois : la DOCUMENTATION d'une image (`image-iconographe` → le service
**Iconographe**, `E:/Claude Code/Programmes/Iconographe`, `127.0.0.1:7940` : il
documente, et sa BIBLIOTHÈQUE fait qu'une œuvre n'est jamais analysée deux fois —
sha256 exact, puis empreinte perceptuelle ; la CULTURE manque en v1, port écrit et
aucun adaptateur, et le récit le dit), la STRUCTURE du récit (catalogue de
`comfyui-direction-de-style`, servie au formulaire par
`"options_depuis": {"menu": "style_narratif"}` et au graphe par le champ
sémantique `style_narratif`), l'APPROCHE (catalogue du même paquet,
`styles/approches.json`, servi par `"options_depuis": {"menu": "style_approche"}`
et lié au graphe par `70.style_approche` : elle dit COMMENT la révélation se
conduit — combien de temps, combien de temps chacun tient, ce que la caméra
s'autorise), l'APPEL FINAL (`video-appel-final`, qui vaut pour
n'importe quelle vidéo). Une chaîne ne recopie donc jamais une liste ni une
documentation : elle dit d'où elles viennent.

**Une étape peut ne livrer aucun média** : un run qui n'écrit qu'un `.json`
réussit, et ce fichier est son livrable comme son `recit` (`_principal`,
`adapter/chaines.py`). C'est le cas de `analyse` et `intention`.

**Nommage des fichiers livrés** (règle générale, tenue ici) :
`cortex/<nom donné>_<type>…` — le type est le workflow, ou la chaîne pour un
livrable final (`<nom>_<chaine>-final_<id>`), et chaque étape rendue porte
`<nom>-<étape>_<workflow>`. Sans nom donné, le type seul.

Ne jamais relancer la passerelle (8077) pendant qu'un run tourne
(`GET /v1/engine/queue` : `running` et `pending` vides d'abord) ; relancer par
`start-bridge-silent.vbs`, prouver le changement de PID par le port.
