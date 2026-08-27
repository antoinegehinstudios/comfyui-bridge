# comfyui-bridge — campagne d'épreuve (2026-08-26)

Objectif : éprouver le système en poussant les paramètres, ajouter un workflow neuf,
vérifier que l'analyse le branche correctement (UI + API), et corriger à la source
toute zone qui ment ou qui frustre.

## 1. Estimation pilotée par les paramètres
- [x] Estimation affichée juste avant le bouton de lancement
- [x] Recalcul sur changement de n'importe quel champ (POST /v1/estimate)
- [x] Repli honnête quand rien de comparable n'a été mesuré
- [x] Un run mesuré alimente `work` → l'estimation varie avec résolution / images
- [x] Vérifier que l'attente en file n'entre pas dans l'estimation (horodatage moteur)
- [x] Coût fixe de mise en route séparé du coût par unité (ajustement affine)

## 2. Paramètres : chaque champ arrive vraiment dans le graphe
- [x] Balayage par /v1/preview : chaque paramètre accepté modifie bien le nœud lié
- [x] Vérifier sur le média livré (résolution, nombre d'images, fps)
- [x] Entrées brutes (overrides "node.input") : appliquées, refus 422 si cible absente

## 3. Nouveau workflow
- [x] Importer un workflow encore non extrait depuis ComfyUI
- [x] L'analyse produit les champs UI (accepts) et les entrées brutes
- [x] Le lancer depuis l'UI et obtenir un livrable

## 4. Chasse aux mensonges
- [x] Statuts (queued / running / failed) fidèles à la file du moteur
- [x] Livrables : chemin absolu réel, existant
- [x] Un run terminé pendant un redémarrage est récupéré, plus perdu
- [x] Erreurs : RFC 7807 avec la vraie cause (422 pour une faute d'appel)
- [x] Options d'énumération lisibles (plus de "[object Object]")

## 5. Conduite d'un run (trouvé en éprouvant)
- [x] Un run en attente derrière un autre le dit (au lieu de « chargement du modèle »)
- [x] Bouton « Arrêter ce run » (file : suppression ciblée ; en cours : interrupt)
- [x] Un run arrêté est `cancelled`, pas `failed` — rien retenu contre le workflow
- [x] Un run annulé n'attend plus le budget complet avant d'être conclu
- [x] Vider la file depuis l'UI
- [x] Redémarrage moteur : issue honnête quand il ne nous appartient pas

## 6. Découverte et formulaire
- [x] Titres « Float (duration) » lus — la durée manquait pour tout un workflow
- [x] Ré-extraction proposée quand l'analyse stockée est plus pauvre que la source
- [x] Bornes des champs = celles déclarées par le workflow (plus de plafond inventé)
- [x] Champ dérivé (durée → images) proposé et annoncé comme tel
- [x] Paramètre non exposé : dit, jamais avalé en silence
- [x] La console ouvre sur un workflow réellement lançable

## 7. Ce qu'une console doit permettre
- [x] Fournir sa propre image (relais de l'upload officiel ComfyUI)
- [x] Voir la mémoire d'Hermès (runs réels, durées mesurées, problèmes)
- [x] Un job disparu après redémarrage : dit une fois, plus de 404 en boucle

## 8. Autres formes de workflow
- [x] Workflow partant d'une VIDÉO (interpolation) : entrée découverte, choix
      réels proposés, envoi de fichier, run livré depuis l'UI
- [x] Catégorie sans élément neutre : dit clairement que le contenu du
      workflow sera utilisé (au lieu d'annoncer un neutre inexistant)

## Revue — 2026-08-27

Campagne menée depuis l'UI, moteur réel (RTX 3060 12 Go, ComfyUI :8188).
79 tests passent. Ce qui a été trouvé en éprouvant, et corrigé à la source :

**Estimation**
- Le temps annoncé ignorait le coût fixe d'un run : 8 s annoncés pour un run
  de 71 s. Ajustement affine (mise en route + charge), calé sur les runs mesurés.
- L'estimation est calculée sur l'intention ENTIÈRE et affichée juste avant le
  bouton, recalculée à chaque changement de champ.
- Une estimation impossible se disait en disparaissant : elle s'explique.

**Découverte des entrées**
- Les titres du type « Float (duration) » n'étaient pas lus : tout un workflow
  se retrouvait sans champ de durée. Lus désormais.
- Une analyse stockée plus pauvre que sa source propose sa mise à jour.
- Les bornes des champs viennent du workflow (le plafond `batch ≤ 16` inventé
  côté API refusait 17 images que le nœud acceptait) ; les plafonds sont partis.
- Un champ dérivable (durée → images) est proposé et annoncé comme dérivé.
- Un paramètre que le workflow n'expose pas est nommé, plus jamais avalé.
- Les énumérations dynamiques de ComfyUI affichaient « [object Object] ».

**Conduite des runs**
- Un run terminé par le moteur pendant un redémarrage de la passerelle était
  perdu (ni média ni mesure). Journal des runs en vol + reprise au démarrage.
- Un run en attente derrière un autre affichait « chargement du modèle ».
- Un run arrêté par l'utilisateur était enregistré comme erreur du workflow.
- Un run annulé attendait tout le budget avant d'être conclu.
- Bouton « Arrêter ce run » (suppression ciblée en file / interrupt en cours).
- Après redémarrage, la console bouclait sur des 404 sans rien expliquer.

**Cohérence de la mémoire d'Hermès**
- `readiness` et le réconciliateur lisaient la mémoire différemment : l'UI
  pouvait dire « impossible » là où le lancement était accepté. Une seule
  lecture désormais, et un modèle absent bloque toutes les configurations
  alors qu'un dépassement mémoire ne concerne que la sienne.
- La mémoire est visible dans l'onglet Workflows.

**Divers**
- Une erreur d'appel (entrée brute inexistante) rendait 500 ; c'est 422.
- La console ouvrait sur un workflow impossible ici ; elle ouvre sur un
  workflow lançable et signale les autres.
- Envoi d'une image depuis la console (relais de l'upload officiel).

**Suite — 2026-08-27, sur constat de l'utilisateur**
- L'estimation ignorait deux choses : les ÉTAPES quand aucun champ `steps`
  n'existe (LTX : deux passes en sigmas, 3 + 8), et TOUTE la charge dès qu'un
  facteur était illisible (minimax : 24 puis 96 images, 134 s puis 411 s
  mesurées, même chiffre annoncé). Charge lue sur le graphe injecté ; facteur
  illisible = neutre, jamais annulant ; barème versionné.
- Un run orphelin n'était récupéré qu'au démarrage suivant : la consultation
  des livrables déclenche aussi le balayage.

**Limite connue**
- La mise en route varie fortement selon que le modèle est déjà chargé
  (mesuré : 71 s à 97 s pour la même charge). L'intervalle affiché s'élargit
  avec l'expérience ; distinguer « à froid » de « à chaud » reste à faire.
