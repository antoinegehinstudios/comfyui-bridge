# Chantier « réconciliant unique par techno » — 2026-09-25

Demande d'Antoine : « lance le chantier de standardisation et réconciliation unique par techno, suivant les prérequis que
tu as proposés, et que chaque nouveau flux de création sur maestro suive la même structure à la base. Identifie donc les
divergents qui sont silotés, pour adapter la couche en silo. Toujours résultant par des tests qui prouvent que le flux
n'est pas cassé. »

Prérequis retenus (réponse du 2026-09-25) : des emplacements PAR RÔLE, jamais par nom de champ de la source ; ce qui ne
trouve pas de place est DIT ; le gabarit n'évolue que pour un rôle nouveau ; un seul élément lit la source.

## Inventaire (fait)

| Mode (catalogue) | Sources lues | Comment | Écart |
|---|---|---|---|
| image-creation | Héraldiste | étapes contrainte/conformité recopiées, 17 + 6 clés du récit lues | gabarit « creation », couche charte recopiée |
| image-visuel-social | Héraldiste | idem, 21 + 7 clés | idem |
| video-depuis-un-texte | Héraldiste | sa propre copie de la couche charte, 35 + 9 clés ; constats de charte évalués même sans charte ; pas de contrôle final | aucun gabarit, charte pas en premier |
| video-revelation | Iconographe, Iconologue | étapes analyse et culture écrites dans la chaîne | aucun gabarit |
| video-affiche | aucune (réalisateur seul) | — | aucun gabarit |
| video-prolongement | aucune | — | aucun gabarit, pas de contrôle du poids |
| 8 graphes seuls catalogués (image→vidéo ×3, vidéo longue ×3, son, 3D) | aucune | graphes nus | hors socle : ni constats ni contrôle |

## Plan

- [x] Langage du réconciliant (`core/reconciliant.py`) : branchements `@nom`, présence conditionnelle `@si`/`@sauf`, variantes
      `@selon`, places (début, après la livraison, avant le contrôle), emplacements `$<rôle>.<chemin>` réécrits, refus
      nommés (emplacement inconnu, lecture directe d'une source, branchement requis absent, collision d'étape ou de champ,
      deux réconciliants pour une même techno).
- [x] Réconciliant `charte` (Héraldiste) : champs charte/logo/logo_ou, étapes contrainte → conformité → constats de la
      charte, valeurs « sans charte », emplacements par rôle, faits lus (le reste est dit « non servi »).
- [x] Réconciliants `analyse` (Iconographe) et `culture` (Iconologue).
- [x] Gabarit `socle` (toute création cataloguée) + `creation` qui l'étend ; une chaîne cataloguée sans gabarit au socle
      est refusée ; un graphe seul catalogué est publié « hors socle » avec sa raison.
- [x] Chaînes réécrites : les 6. Témoin « sans casse » : chaîne dépliée contre la chaîne d'avant, figée ; tout écart est
      dans une liste déclarée, chacun avec sa raison, et prouvé équivalent quand il réécrit une valeur.
- [x] Faits non servis publiés par option de charte ; maestro les montre sous le champ.
- [x] Suite complète dans l'arbre isolé, échecs comparés à la base (0054155).
- [ ] Commit sur `chantier/reconciliant-unique` ; report sur la copie vivante ; bascule file et moteur vides ; preuves
      réelles (image, visuel social, banc c7 vidéo de la session charte).
- [ ] Mémoire et doc.

## Revue

(à remplir)
