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
  un job porte `demande`, `etapes`, `artifacts[].{kind,path,url}`, `problem`.
Les tests `tests/test_chaines_api.py` tiennent ces formes ; une chaîne modifiée
dans `_data/chaines/` doit garder sa copie `resources/chaines-exemples/`.

**Nommage des fichiers livrés** (règle générale, tenue ici) :
`cortex/<nom donné>_<type>…` — le type est le workflow, ou la chaîne pour un
livrable final (`<nom>_<chaine>-final_<id>`), et chaque étape rendue porte
`<nom>-<étape>_<workflow>`. Sans nom donné, le type seul.

Ne jamais relancer la passerelle (8077) pendant qu'un run tourne
(`GET /v1/engine/queue` : `running` et `pending` vides d'abord) ; relancer par
`start-bridge-silent.vbs`, prouver le changement de PID par le port.
Un montage (`_data/workflows/`) et ses blocs (`_data/blocs/`) sont lus SUR
DISQUE à chaque assemblage, sans relance : modifiés pendant qu'une chaîne
tourne, ses tours SUIVANTS les prennent, et le job ne le dit pas (depuis le
2026-09-18 ; avant, le montage restait figé en mémoire à côté de blocs
frais). Réconciliation, chaînes et techniques, eux, sont lus une fois — au
démarrage ou au premier usage — et jamais relus : relancer, file vide.
