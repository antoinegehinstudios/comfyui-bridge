# Leçons — comfyui-bridge

Ce que les corrections de l'utilisateur ont appris, formulé comme des règles à
appliquer avant d'écrire la ligne suivante.

## 1. Une seule source de vérité, toujours celle qui sait

ComfyUI déclare ses entrées, leurs bornes, ses dépendances, sa file, et valide
le graphe qu'on lui donne. Toute règle écrite ici en parallèle finit par mentir.

- Un plafond inventé côté API (`batch ≤ 16`) a refusé 17 images qu'un nœud
  acceptait ; les bornes viennent maintenant de `/object_info`.
- Deux lectures de la mémoire d'Hermès (readiness d'un côté, réconciliateur de
  l'autre) ont fini par se contredire à l'écran. Une seule fonction les sert.

**Règle** : avant d'ajouter une validation, une liste ou une borne, chercher qui
la détient déjà. Si quelqu'un la détient, la relayer — jamais la recopier.

## 2. Ne rien afficher qu'on n'ait pas mesuré

- Une durée « estimée » proportionnelle à la charge annonçait 8 s pour un run
  qui en prend 71 : le coût fixe de mise en route existait et n'était pas modélisé.
- Un job en attente derrière un autre affichait « chargement du modèle ».
- Un run arrêté à la main était enregistré comme une erreur du workflow.

**Règle** : chaque phrase de l'interface doit pouvoir être rattachée à une
mesure ou à une déclaration du moteur. Sinon, dire qu'on ne sait pas.

## 3. Un paramètre qui ne va nulle part doit le dire

Un champ réglé qui n'atteint aucun nœud est pire qu'un champ absent : il donne
l'illusion d'avoir agi. Le plan nomme désormais ce qu'il n'a pas pu transmettre.

## 4. Ce que le moteur continue de faire pendant qu'on redémarre existe

Un rendu terminé pendant un redémarrage de la passerelle disparaissait :
ni fichier livré, ni durée mesurée. Toute remise en état doit se demander ce qui
était en vol — et le demander au moteur, qui, lui, s'en souvient.

## 5. Éprouver par l'usage, pas par le code

Les défauts trouvés ici ne l'ont pas été en relisant : ils sont apparus en
lançant de vrais rendus depuis l'interface et en regardant ce qui s'affichait,
ce qui arrivait au moteur et ce qui atterrissait sur le disque.

**Règle** : après chaque correction, refaire le geste de l'utilisateur.
