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
  demande), `categories_de_champs[]` (premier niveau : le vocabulaire des
  RUBRIQUES d'un formulaire — `valeur`, `titre`, `resume`, `repliee` —, dans
  l'ordre où le lanceur les rend ; liste vide quand rien n'est déclaré, et le
  formulaire reste une seule liste), et par entrée `presentation` (`titre`,
  `resume`, `categorie` — sans catégorie l'entrée est technique, invisible —,
  `ordre`, `apercu_url`), `runnable`, `warned`, `chaine`, `etapes[].workflow`,
  et `raccourcis[]` (les ensembles de réglages enregistrés sous ce mode :
  `id`, `titre`, `resume`, `valeurs`, `ecarts[].{champ,libelle,valeur,libelle_valeur}`,
  `sources` (les pièces jointes de la livraison qui l'a fait naître, par nom de
  champ média, toujours publiées — c'est cette image-là que le raccourci
  rejoue, depuis le 2026-09-20 ; `{}` sans livraison ; une fiche d'avant est
  complétée au démarrage depuis sa livraison, et `GET /v1/recovered` le dit
  dans `raccourcis_completes`),
  `apercu_url` s'il existe, `job_id`, `ordre`, et `perime: {champs[], raison}`
  quand le raccourci a vieilli — champ que le mode n'expose plus, valeur hors
  menu, réglage d'une autre technique que la sienne, source dont la pièce
  jointe n'est plus exposée ; ABSENT quand il tient ;
  le lanceur grise la carte et dit la raison (depuis le 2026-09-19) — liste
  vide sinon ; le MODE lui-même, avec ses défauts, est le raccourci implicite,
  affiché en premier),
  et sur une CHAÎNE `techniques[]` (`valeur`, `libelle`, `resume` — les façons
  de tenir ses rôles, liste vide si elle n'en emploie aucune) ;
- `GET /v1/workflows/{nom}/io` : `intent_inputs[]` (`field`, `type`, `value`,
  `min`, `max`, `step`, `options`, `choix`, `libelle`, `unite`, `aide`,
  `categorie` (une valeur de `categories_de_champs` : la rubrique où le
  lanceur range ce champ ; absente, le champ va dans une dernière rubrique
  sans titre), `derived`, `requis`, et pour un champ qu'une TECHNIQUE
  apporte : `selon` (`{champ, valeurs}` — le lanceur ne le montre que sous ces
  techniques-là), plus `selon_options` / `selon_defauts` par technique quand
  deux techniques exposent le même nom avec des valeurs différentes) et
  `media_inputs[]` (`param`, `category`, `label`, `accept`, `carried`, et
  `categorie`, `aide` quand la chaîne les déclare) — le formulaire est bâti
  uniquement dessus ;
- `POST /v1/render` (corps plat, `label` = nom de la production),
  `POST /v1/render` (en-tête `Idempotency-Key` : même clé = même job, jamais
  un second lancement ; `X-Idempotence: rejouee`), `GET /v1/lenteurs` (les
  appels lents ou ratés et les retards de boucle, mesurés ; un moteur mort
  — connexion REFUSÉE 60 s — arrête le run au lieu d'attendre son budget, et
  un veilleur le relève s'il est à nous, jamais s'il répond ; une pièce jointe
  que le moteur ne sait pas ouvrir est refusée au dépôt par son CONTENU et au
  lancement par son NOM — `core/pieces_jointes.py` —, un dessin vectoriel
  ayant fait tomber le moteur vingt et une fois le 2026-09-22 ; un SVG, lui,
  est PEINT au dépôt — `adapter/svg_rendu.py`, Inkscape — et le document est
  gardé pour le nœud `ChargerSVG`),
  `POST /v1/estimate` (une chaîne : par ses propres livraisons, `dit` la
  source — même configuration, droite sur le nombre de runs, autre
  configuration —, `impraticable` quand un montage refuse la demande),
  `GET /v1/jobs`, `GET /v1/jobs/{id}` + `/events`,
  `POST /v1/jobs/{id}/cancel`, `/rejouer` et `/reprendre` (une chaîne échouée
  repart à l'étape en échec, ses étapes abouties reprises), `PUT /v1/workflows/{nom}/apercu`,
  `GET|POST /v1/workflows/{nom}/raccourcis` et
  `GET|PUT|DELETE /v1/workflows/{nom}/raccourcis/{id}` (+ `…/{id}/apercu`) —
  le lanceur DÉSIGNE (une livraison devient un raccourci), la passerelle
  valide, range et fabrique l'image ;
  `POST /v1/preview` sur une chaîne rend, par étape, `params` — pour une étape
  à rôle, `params.inputs` porte AUSSI les entrées que la technique met derrière
  le rôle, résolues : ce que le lanceur montre est ce qui part ;
  un job porte `demande`, `etapes` (chaque étape rendue : `resultat`, dont
  `recit`, le récit compact écrit par le nœud — ce sur quoi l'étape `verifier`
  de la chaîne a jugé, et `controles[].{id,ok,mesure,attendu,aide}` ; une étape
  gardée par clé porte `resultat.memoire.{cle, reprise}` et, reprise sans run,
  `job_id` nul et la note « reprise de la mémoire : même clé » ; une étape
  sautée porte `note` et `resultat.sans_effet[]`, les champs qu'elle seule
  lisait et qu'on avait réglés — sauf ceux que son `sinon` RENVOIE, qui
  traversent l'étape et ont donc bien un effet ; son `quand` prend aussi la
  forme NOMMÉE d'un contrôle, `{valeur, op, attendu}`, pour sauter une étape
  dont la valeur dit « rien à faire » sans être vide),
  `artifacts[].{kind,path,url}`, `problem` — et sur un refus de chaîne
  (`problem_kind` `controle-echoue`) : `problem.etape` et
  `problem.controles[].{id,op,attendu,mesure,ok,aide}`, `aide` étant
  l'explication que le contrôle porte dans sa chaîne ou sa technique (ce qu'il
  mesure, quoi faire — jointe aussi au `detail` ; le lanceur la montre sous le
  refus, contrôle par contrôle, depuis le 2026-09-19). Un job de
  CHAÎNE réussi ne livre QUE son livrable (la production finale montée) : les
  produits d'étapes — déroulement recollé, conclusion, appel, clips, récits,
  tranches — restent dans `etapes[].resultat` et dans les sous-jobs
  (`/v1/jobs?enfants=1`), jamais dans `artifacts` (2026-09-16 : « il doit
  toujours livrer l'état terminé »). En ÉCHEC, tout ce qui a été écrit reste
  listé.
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
standardisation ») : le plan des douze étapes (onze jusqu'au 2026-09-20) est agnostique, tout le reste est un
paramètre dont le DÉFAUT est celui du rendu ink livré ce jour-là ; un style de plus
est une entrée de plus dans un catalogue ou une table, jamais un défaut de moins.
Depuis le 2026-09-16, **la chaîne ne nomme aucune TECHNIQUE** (Antoine : « la
mention de brume ne doit pas être tenue par le workflow de la passerelle : cela
veut dire qu'il porte une dépendance à la brume et devra se faire doublon pour
faire autrement ») : ses étapes `deroulement`, `conclusion` et `appel` nomment
un RÔLE et la technique qui le tient (`"role"` + `"technique": "$technique"`), et
`plan_tenu` prend la liste de contrôles de cette technique — en CONSTAT depuis le
17 au soir (genre `constater` : écrit, jamais bloquant ; « c'est l'utilisateur qui
juge ») ; `plan_valide` joint
aux dix contrôles du plan la liste `plan` de la technique (ce qu'elle exige du
plan se juge AVANT de peindre — 2026-09-17, trente-deux minutes perdues sur un
plan à un tracé sur six, vingt-cinq sur une accroche nichée dans le temps
suivant ; l'encre exige `part_des_traces` ≥ 0,25 et
`accroche_couverte_par_le_suivant` ≤ 0,5 ; une technique qui n'exige rien
déclare `"plan": []`).
Quel graphe tient chaque rôle, ses réglages propres (`fond`, `encre`, `ambiance`,
`negatif`… :
ce que la PEINTURE lit ne vit pas dans le plan) et ses contrôles vivent dans
`_data/techniques/<nom>.json` — une technique de plus est un FICHIER de plus,
`video-revelation-brume` a disparu au profit de la technique `brume`, et le
champ `technique` du plan n'a PAS de défaut : c'est la technique qui se dit
`par_defaut` (Antoine, 2026-09-16 au soir : « les paramètres ne vivent pas dans
le workflow mais se réconcilient avec lui quand le paramètre l'appelle »). Le
témoin épingle donc les deux : le plan dans la chaîne, l'encre dans sa technique.
Une SEULE exception depuis : le FORMAT par défaut est passé au portrait 720p à
30 i/s (`width` 720, `height` 1280, `fps` 30) le 2026-09-15 au soir, à la demande
d'Antoine — un format est un réglage d'usage, pas un trait du style, et rien de
ce que le style fait n'en dépend. Le témoin `tests/test_socle_ink.py` (ici, et
dans `comfyui-ink-reveal`) épingle plan, défauts, contrat des étapes et
constantes : le faire échouer est une décision à écrire, voir la section « Le
socle ink » du README.

**Créer une image, sous une charte (2026-09-24, après-midi)** : la charte
Héraldiste se choisit EN PREMIER dans les deux modes (champ `charte`, rubrique
`charte` en tête du formulaire — Antoine : « la charte ne doit pas être
sélectionnée sur la fin, sinon c'est pas logique ») et pèse sur tout ce qui
suit : l'étape `contrainte` (graphe `image-heraldiste`, nœud `HeraldisteCharte`,
sautée sans charte avec un `sinon` aux mêmes clés) précède la `direction`, qui
reçoit `positif` (les mots de style de la marque), `couleurs_en` (SA palette, à
la place de celle qu'on aurait choisie — le champ `palette` porte
`selon: {charte: [aucune]}` et ne s'affiche plus sous une charte ; la direction
dit ce qu'elle a écarté, `palette_dite`), `negatif` (ses interdits) et
`logo_ancrage` (une zone calme est demandée là où le logo sera posé) ; la
`livraison` (composer) pose le logo tel quel, fond la texture, et — visuel
social — écrit le message dans la police et la couleur des titres de la charte
sur son fond de titre (`police`, `couleur_texte`, `bandeau` sous `selon`
aussi), en ôtant ce que le wordmark écrit déjà ; `conformite` (graphe
`video-heraldiste-conformite`, nœud `HeraldisteConformite`, qui lit une image
fixe par son chemin absolu, `1.images` = 1) mesure l'image livrée ;
`constat_de_la_charte` constate : charte appliquée, palette dans la consigne et
tenue, logo posé / intact / à sa taille, règles mesurables, et ce que ce modèle
ne sait PAS honorer — les interdits sous la technique rapide (cfg 1 :
`$technique.negatif.applique`, déclaré dans chaque fichier de technique) et
les images de référence de la charte (`references_transmises` non vide : ce
modèle n'en prend aucune). Un champ de CHAÎNE peut porter `selon` (noyau
`Champ.selon`, refusé s'il désigne un champ absent ou lui-même) : la même clé
que celle des réglages de technique, un lanceur n'a qu'une règle.

**La règle compte partout (2026-09-24, Antoine)** — trois choses tenues par le
noyau et l'adaptateur, pour toute chaîne et toute technique, sans nommer un
flux : (1) **rien de fantôme** — un champ exposé que rien ne lit (aucune
étape, aucun rôle, aucun contrôle ; le champ qui choisit la technique excepté)
est refusé au chargement (`core.chaine.champs_que_rien_ne_lit`, en fin de
`verifier_techniques`) ; (2) **un champ IMPOSÉ se grise, il ne se cache pas** —
`impose_par: {champ, sauf}` sur un champ de chaîne ou de technique (le maître
doit exister), publié tel quel dans `/io` ; ce que chaque valeur du maître
impose vient du fournisseur du menu (colonne « impose », projetée par
`source_fichier.impose: {colonne, champs}` — Héraldiste dit `negatif`,
`palette`, `police_titres`, `couleur_titres`, `fond_titres`, seulement ce que
la charte donne ; la réconciliation traduit vers les champs des chaînes) et
part dans `choix[].impose` ; le lanceur ne grise que ce qui est imposé ; (3)
**ce qu'une technique ne règle pas se DÉTECTE et se dit** —
`adapter.techniques.manques(technique, voisines, graphe_de)` : un réglage
qu'une voisine de la même chaîne expose et qu'elle n'a pas, dit par l'auteur
(`<champ>: {applique: false, dit}`, `declare`), sinon lu dans son graphe
(entrée tenue à une valeur fixe, `graphe`), sinon `absent` ; publié dans
`/io` (`manques_par_valeur`) et `/v1/workflows` (`techniques[].manques`) ;
une section `applique: false` sur un champ exposé est une contradiction,
refusée. Et le **gabarit** : une chaîne qui écrit `"gabarit": "creation"` est
vérifiée contre `resources/gabarits/creation.json` au chargement
(`core.gabarit`), chaque écart nommé ; `presentation.gabarit` le publie ; la
lecture humaine est `resources/chaines-exemples/GABARIT-creation.md`.

**Créer une image (2026-09-24)** : deux modes publiés sous la catégorie
`creer-une-image` — `image-creation` (« Créer une image ») et
`image-visuel-social` (« Créer un visuel pour les réseaux ») — sur UN socle
de quatre étapes : `direction` (graphe `image-direction`, nœud
`DirectionDImage` du paquet `comfyui-direction-de-style` : le style enveloppe
le sujet, cadrage / lumière / palette / ambiance ajoutent leurs mots, le récit
les cite par paramètre, la taille NATIVE de rendu est calculée — multiples de
seize, ≤ 2048 de côté, ≤ 2,2 MP) → `rendu` (le RÔLE `image`, tenu par la
technique choisie : `rapide` = Z-Image Turbo int8, 8 pas, cfg 1, négatif mis à
zéro donc NON exposé ; `soignee` = Z-Image base int8, 25 pas, cfg et négatif
réels — poids Comfy-Org dans `models/`, encodeur `qwen_3_4b` type `lumina2`)
→ `livraison` (genre `composer` : l'image portée à la taille EXACTE demandée,
couvrir puis rogner au centre, jamais de bandes ; textes / images / texture
avec les mêmes clés qu'au recollage — le message d'un visuel social est POSÉ
là, tel quel, dans une police du poste, jamais confié au modèle, et la
direction a demandé une zone calme à sa position) → `constat` (taille tenue,
texte posé, pas d'agrandissement) → `controle` (fichier qui pèse). Les
techniques vivent dans le dossier commun `_data/techniques/` : une chaîne ne
voit que celles qui tiennent SES rôles (`core.chaine.techniques_pour`,
`Catalog.techniques_de`) — c'est ce qui permet à `rapide`/`soignee` (rôle
`image`) de coexister avec `encre`/`brume`/`livre` (rôles de la révélation)
sans qu'aucune chaîne ne refuse au démarrage. L'aperçu d'un mode ou d'un
raccourci se fabrique aussi depuis une IMAGE livrée (vignette WebP fixe).
Témoin : `tests/test_socle_image.py` ; composer : `tests/test_composition_image.py`.

**Une étape peut ne livrer aucun média** : un run qui n'écrit qu'un `.json`
réussit, et ce fichier est son livrable comme son `recit` (`_principal`,
`adapter/chaines.py`). C'est le cas de `analyse` et `intention`.

**Un `rendre` peut être rendu en TRANCHES par la passerelle** — et **pour un
graphe appelé directement aussi** (`POST /v1/render`, un mode de n'importe quelle
catégorie, un rejeu : le graphe devient une chaîne d'une seule étape « rendu ») —
quand le nœud le déclare (entrées littérales `segment_index` + `segment_count`,
et `duree_max_s` — borne absolue — ou `allonge_max_s` — de combien au plus il
allonge la durée demandée — qui bornent le compte : avec une allonge déclarée
et une durée demandée, c'est `demandée + allonge` qui compte, jamais le
plafond (2026-09-19 : compté sur `max(demandée, 79)`, 10 s demandées se
comptaient comme 79) ; la taille d'un graphe qui la prend d'une vidéo d'entrée
se lit sur cette vidéo) et que la mémoire l'oblige (budget calculé sur
`_data/materiel.local.json`) : N runs d'une même simulation, recollés par copie
de flux (aucune image ré-encodée), récits fusionnés. Le job porte alors
`etapes[].job_ids` et `etapes[].tranches` (un `job_id` par tranche, tous visibles
dans `/v1/jobs`) ; une tranche n'est pas le média : son sous-job n'écrit aucun
« écart » de durée. Voir « Rendu par tranches » dans le README.

**La durée demandée fait loi** (Antoine, 2026-09-19, après deux productions
de 10 s livrées à 72,9 puis 88,7 s : « tout paramètre a son poids, les
paramètres fantômes sont à bannir »). Dans « Révéler une image », l'intention
reçoit le budget — `62.duree_s`, `62.contemplation_s`, `62.queue_s` (la fin
fixe que le nœud de la technique choisie impose, lue dans SON fichier par le
renvoi `$technique.budget.queue_s`) — et la graine, et se taille dedans ;
`plan_dans_la_duree` CONSTATE en chiffrant un plan dont le minimum dépasse la
demande (`le_plan_tient_dans_la_duree` — un refus de `plan_valide` jusqu'au
2026-09-20 au matin ; Antoine : « mentionner une erreur ne doit pas suicider la
livraison ! les erreurs mentionnées ne tuent pas la livraison, elles émettent
seulement »), le déroulement reçoit la durée que le plan demande
(`$intention.recit.duree_prevue_s`), n'allonge que dans la marge que son
graphe déclare (`61.allonge_max_s`, 5 s) et `plan_tenu` le CONSTATE
(`la_duree_est_tenue`). Ce qui refuse encore : `plan_valide` (une inconformité
de l'image ou du plan, que le nœud ne saurait pas peindre) et `controle` (le
fichier final) ; tout écart chiffré se constate. Le plan est gardé par clé (`memoire` de l'étape
`intention` : même image, réglages, graine, technique → repris sans run).
Trois grammaires génériques portent cela, sans un nom de flux dans le code :
le renvoi `$<champ de technique>.<chemin>` (`core/chaine.py`), la clé
`memoire` d'un `rendre`, et `sans_effet` d'une étape sautée. Le témoin
statique `tests/test_chaque_champ_pese.py` refuse tout champ exposé qu'aucune
étape ne lit ou qui vise une entrée absente du graphe, et exige que chaque
raccourci soit valide ou périmé avec sa raison.

**Les LIMITES du poste sont déclarées, jamais devinées** : `_data/materiel.local.json`
(RAM totale, part réservée au reste de la machine, facteur de crête, VRAM, cœurs)
— « les limitations matérielles du PC doivent être dans un fichier de
réconciliation […] au cas où la RAM du PC venait à changer » (Antoine,
2026-09-16). Le budget d'une tranche en sort : `(totale − reservee) /
facteur_de_crete`. Ordre : surcharge `COMFY_TRANCHE_GO` > fichier > repli 8 Gio,
DIT au démarrage, dans le journal du job et sur `GET /v1/materiel`.

**Une étape facultative déclare ce qu'elle rend quand elle n'a pas lieu**
(`"sinon": {…}`, fusionné par-dessus le passe-plat), et une part de montage
accepte `sauf_les_dernieres` (rognage de QUEUE) à côté de `depuis_image`
(rognage de tête) ; une part au fichier nul est ignorée, et dite. C'est ce qui
permet à l'appel final de ne plus charger la conclusion entière : le montage
joint la conclusion sans les images que l'appel a reprises.

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

**Le moteur n'a qu'un guichet : la passerelle.** Une DEMANDE de l'utilisateur
(rendu, chaîne, rejeu, reprise) entre dans la file des demandes et tourne seule,
dans l'ordre (`GET /v1/file`, `file: {rang, devant}` sur le job). Tout ce qui
n'est pas une demande — une enquête, un banc, un agent qui veut faire tourner
un graphe — passe par `POST /v1/essais {"graphe": <graphe API>, "label": …}` :
l'essai attend que la voie des demandes soit vide, et CÈDE la place à une
demande qui arrive (interrompu, il repart de zéro après elle). Ne JAMAIS
envoyer un prompt directement au moteur (`:8188/prompt`) : ce qui atteint le
moteur sans la passerelle est ÉTRANGER — marqué sur `GET /v1/engine/queue`, et
RETIRÉ de la file du moteur (annulé s'il attend, interrompu s'il tourne) dès
qu'une demande attend derrière lui, en le disant sur le job qui passe. Mesuré
le 2026-09-18 : une enquête avait envoyé ses expériences au moteur, et le rendu
d'Antoine a attendu vingt minutes derrière elles — « ne corrige pas ce cas
unique, ajuste l'outillage pour que ce type de problème n'apparaisse plus, by
design ». Une analyse lourde (extraire des centaines d'images, du flux optique)
pendant un rendu prend la mémoire du poste : la garde de place du rendu attend,
et le dit ; la faire APRÈS.
