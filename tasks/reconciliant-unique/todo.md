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
- [x] Commit sur `chantier/reconciliant-unique` ; report sur la copie vivante ; bascule file et moteur vides ; preuves
      réelles (image, visuel social, banc c7 vidéo de la session charte).
- [ ] Mémoire et doc.

## Revue (2026-09-25)

- Portée, fixée par Antoine en cours de route : « les flux jamais utilisés, on peut les laisser tomber ;
  l'arrangement est pour ceux utilisés plus de 3 fois en deux mois ». Mesuré dans Hermes (depuis le 26/08) et
  `_data/jobs` (depuis le 13/09, libellés) : les siens sont Écrire une vidéo, Révéler une image, le visuel pour
  les réseaux et Créer une image — tous quatre arrangés. L'affiche (12 lancements, tous des essais de
  sessions) et le prolongement (0) restent au socle a minima ; les 8 graphes seuls publiés (lancements d'août :
  essais « verif-3d », « testB-vid », « stitch-… ») sont laissés tombés, dits `hors_socle`.
- Code : passerelle e2e53d1, maestro c187f30. Arbre isolé : 673 tests passent, les 13 échecs sont préexistants
  (`test_video_h3_texte_exemple`). Copie de l'arbre vivant avec le correctif : 700 passent et 1 échoue.
  L'échec, c'est le témoin « sans casse » qui détecte la coquille « demi-runs » non commitée d'une autre session
  (remplacement global resté depuis le 23/09), rendue en « demi-tours » à la bascule.
- Bascule le 25/09 à 13:47:22, file et moteur vides : `git apply` de mes seuls hunks (35 fichiers), chaînes du
  poste, catalogue du poste (`socle`, choix du logo), maestro. Relance de la passerelle saine ; 87 témoins verts
  dans l'arbre vivant ; maestro vivant 70/70.
- Vu dans maestro (navigateur) : la charte en tête d'« Écrire une vidéo », le logo caché sans charte, et sous la
  charte la ligne « Sert aussi, sans que ce mode s'en serve encore : la typographie du texte courant ».
- Preuves réelles :
  - Un visuel « Grabuge » d'Antoine, lancé à 13:47:53, passe sur la nouvelle structure : 9 étapes, mêmes verdicts.
  - Les quatre preuves du logo d'hier (même graine) sont identiques au pixel près aux dernières d'avant la
    bascule, avec les mêmes constats et le même journal de livraison.
  - Sans charte, Créer une image est identique au pixel près à hier matin. Pour le visuel, le rendu du modèle
    est identique ; seule la place du sous-message diffère, et ce décalage date de d5aa66f (24/09 10:40), avant
    la bascule.
- Banc vidéo c7 de la session charte : lancé ; il attend la place (47,8 Gio atteignables pour 48 exigés). Le
  résultat viendra ici.

## Revue, second et troisième temps (2026-09-25, après-midi)

- Demande d'Antoine : « j'ai demandé un template, la prise de charte doit y être présente, et à son poids exactement
  comme c'est défini par le template ; les spécificités propres au workflow sont portées ailleurs, mais le template
  délivre les choses qu'il porte, sans mentir, sans faux paramètre que le workflow ne sait pas tenir ».
- Le socle EXIGE le réconciliant « charte ». Celui-ci déclare huit promesses ; chaque mode tient chacune (un emplacement
  lu, par la chaîne ou par sa technique) ou la décline avec sa raison (« sans »). Ni l'un ni l'autre, ou les deux, est
  refusé au chargement. Les promesses déclinées sont publiées sous le champ « Charte » (« Ne tient pas … ») et vues dans
  maestro.
- Les techniques lisent les emplacements, réécrites pour la chaîne qui les emploie. Le silo des trois techniques de
  révélation, qui lisaient `$analyse.recit.markers_json`, est refermé.
- Révéler une image prend la charte : logo et texture au montage, police de l'appel final.
- L'affiche et le prolongement quittent la vitrine : jamais lancés par Antoine, ils ne savent pas tenir la charte.
- Preuves :
  - Rejeu de la demande « Sepia » d'Antoine sans charte, après chaque bascule : 1 210 images identiques sur 1 210,
    octets identiques. Les 12 étapes d'origine sont inchangées (intention reprise de la mémoire, même clé) ; les 3
    étapes de charte sont sautées.
  - La même demande avec la charte grabuge-fest@4 et un appel : logo posé en carton final, texture fondue, police de la
    charte reçue par InkCaption (historique du moteur), palette 0,85 contre 0,6, logo retrouvé sur 100 % des images.
  - Cette preuve a montré « Grabuge Fest » réécrit dans l'appel alors que le logo signe, et un constat qui se disait
    tenu sans avoir vu. D'où la huitième promesse (nom du logo), déclinée par la révélation, et le constat réservé aux
    textes que pose la livraison.
- Suites : 678 tests passent en arbre isolé (13 échecs préexistants) ; 706 sur une copie de l'arbre vivant (second
  temps) ; 101 témoins verts dans l'arbre vivant ; maestro 71/71.
- Bascules : 14:59:24 (second temps) et 15:31:14 (troisième), file et moteur vides.
- Banc vidéo c7 : relancé après la troisième bascule ; il attend 48 Gio atteignables.

