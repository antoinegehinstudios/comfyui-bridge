# Créer une image — plan du chantier (2026-09-24)

Demande d'Antoine : au catalogue maestro, un flux de CRÉATION d'image (texte → image),
avec la même logique de templatisation/paramètres que « Révéler une image »
(techniques interchangeables, raccourcis) ; des paramètres de style créatif qui
pèsent vraiment (aucun fantôme) ; deux modes distincts dans maestro : création
standard, et visuel pour les réseaux (affiche / message / carrousel). La charte
Héraldiste viendra ensuite (le composer et la direction lui laissent la place).

Modèle retenu (recherche du jour) : Z-Image (Alibaba Tongyi, Apache-2.0, cœur
ComfyUI 0.33.3, int8 6,2 Go qui tient dans les 12 Go de la 3060) en deux
techniques : « rapide » (Turbo, 8 pas, cfg 1 — le négatif y serait un fantôme :
non exposé) et « soignée » (base, 25 pas, cfg et négatif réels).

## À faire
- [x] A. Passerelle — les techniques d'une chaîne sont celles qui tiennent SES rôles
      (`techniques_pour`), partout où une chaîne lit ses techniques ; tests.
- [x] B. Passerelle — genre d'étape `composer` (image fixe : taille exacte, textes,
      logos, texture, via les mêmes filtres que le recollage) ; `adapter/composition_image.py` ;
      aperçu d'un mode / d'un raccourci depuis une image ; tests.
- [x] C. Paquet comfyui-direction-de-style — nœud `DirectionDImage` + catalogues
      `images`, `lumieres`, `ambiances`, `cadrages`, `palettes` ; récit
      `mots_par_parametre` ; taille native (multiples de 16, ≤ 2048, ≤ 2,2 MP) ; tests.
- [x] D. Données — graphes `image-direction`, `image-z-image-turbo`, `image-z-image` ;
      techniques `rapide`, `soignee` (+ copies) ; chaînes `image-creation`,
      `image-visuel-social` (+ copies) ; réconciliation (catégorie, menus, formats
      carré et 4:5, rubriques) ; témoin `test_socle_image.py`.
- [x] E. Relances (moteur pour le nœud, passerelle file vide) ; vrais runs des deux
      modes, deux techniques ; preuves (taille exacte, texte posé, style qui pèse).
- [x] F. Commits sur branches dédiées (index temporaire dans la passerelle) ; mémoire.

## Fait (état au milieu de la matinée)
- A : `core.chaine.techniques_pour`, `Catalog.techniques_de`, propagé dans l'API et le runner ; témoins adaptés (chaque-champ-pèse, socle ink, même socle) + un test neuf.
- B : genre `composer` (noyau + `adapter/composition_image.py` + runner), `textes.py` : `decalage`, `incrustations.py` : `format_sortie` ; aperçu fixe des images (mode, raccourci) ; 8 tests.
- C : `image.py`, nœud `DirectionDImage`, 5 catalogues (71 styles, 12 lumières, 13 ambiances, 11 cadrages, 12 palettes), 16 tests ; branche `chantier/direction-d-image` 1bc10ab.
- D : graphes `image-direction`, `image-z-image-turbo`, `image-z-image` ; techniques `rapide` (défaut), `soignee` ; chaînes `image-creation`, `image-visuel-social` ; réconciliation (catégorie `creer-une-image` ordre 0, menus `*_image` + `couleur_texte`, formats carré 1080 et 1080×1350, rubriques `sujet`, `style`) ; témoin `test_socle_image.py`.
- maestro : « En faire l'exemple du mode » pour une image ; branche `chantier/creer-une-image` 19ab5f6.
- Arbre du commit sélectif de la passerelle bâti depuis 0ce8b61 (l'autre session a du travail non committé dans chaines.py, catalog.py, main.py, README) : validation dans un worktree.
- En attente : fin du job d'Antoine (2add0f8b) pour relancer moteur + passerelle, puis les vrais runs.

## Fait (fin de matinée)
- Relances : moteur + passerelle à 10:28 (file vide), moteur seul à 10:36 (correctif du nom de récit), passerelle seule à 10:44 (correctif des menus) — toujours entre deux jobs de l'autre session.
- Cinq vrais runs réussis (voir la mémoire `creer-une-image`) : 35 s / 30 s / 105 s / 30 s / 25 s.
- Commits : passerelle `chantier/creer-une-image` d5aa66f + 667fbe1 (base 0ce8b61) ; paquet `chantier/direction-d-image` 1bc10ab + 9f73526 ; maestro `chantier/creer-une-image` 19ab5f6.
- Suite complète : mon arbre = 14 échecs, tous présents dans la base pure (21) — ils tiennent au travail non committé de l'autre session (test_video_h3_texte_exemple, jumeau de video-depuis-un-texte).

## La charte (après-midi)
- [x] Charte en tête des deux modes ; contrainte avant direction ; palette/police/couleur/bandeau sous `selon` ; logo, texture, fond de titre posés ; conformité sur l'image ; constats (dont ce que le modèle ne sait pas : interdits sous rapide, références non lues).
- [x] Preuves grabuge-fest@4 : standard rapide (0,965 dans la palette) et social soignée (0,994, textes dans la police de la charte).
- [x] Commits : passerelle 56465e3 ; direction-de-style 2b4a323 ; heraldiste `chantier/recit-positif-couleurs-en` 2c8a5db.

## 2026-09-24, après-midi — la règle compte partout (commit n° 5)

- [x] `impose_par {champ, sauf}` : un champ imposé reste exposé, se grise chez le lanceur, ne part pas ; palette (création), police / couleur_texte / bandeau (visuel social), négatif de la soignée — imposés par la charte.
- [x] Ce que chaque charte impose vient d'Héraldiste (`menu.json`, colonne `impose` : negatif, palette, police_titres, couleur_titres, fond_titres — seulement ce qu'elle donne) ; la réconciliation traduit (`source_fichier.impose: {colonne, champs}`) ; maestro ne grise que ce qui est imposé.
- [x] Rien de fantôme : `champs_que_rien_ne_lit`, refus au chargement pour toute chaîne et toute technique ; trois fixtures d'essai branchées (structure, duree).
- [x] Manques détectés : `techniques.manques(technique, voisines, graphe_de)` — declare / graphe / absent, avec libellé ; `/io` (`manques_par_valeur`) et `/v1/workflows` ; rapide déclare négatif et cfg.
- [x] Gabarit « creation » manifesté par les deux modes, vérifié au chargement (`core/gabarit.py`, `resources/gabarits/creation.json`, `GABARIT-creation.md`).
- [x] Preuves : suite complète verte ; passerelle relancée (PID 41016), `/io` relu ; maestro selftest 67/67 (commit 048edf0) ; Héraldiste 106 tests (commit a3590bc, chantier/menu-impose).
- [ ] Reste : brume et livre tiennent « bords » à « fondus » sans l'exposer (détecté par le graphe) — l'exposer si l'on veut qu'il se règle sous elles.
- [x] Connecteur Héraldiste → passerelle → maestro standardisé : Héraldiste n'expose que des FAITS (`faits`, schéma `heraldiste/menu` 1.1.0, `/v1/schema/menu`) ; l'usage (quel champ, `si`, `dit`) est déclaré dans la réconciliation ; maestro inchangé.

## 2026-09-24, soir — la décision du logo et le colorway du nœud de la charte

- [x] Noyau : `selon: {champ, sauf}` (un champ qui n'existe que hors de ces valeurs) ; valeur conditionnelle `{"si", "alors", "sinon"}` vérifiée à la lecture.
- [x] Composer : une image sans fichier dit sa `raison` (journal, `images_non_posees`).
- [x] Chaînes : champ `logo` → `1.logo` ; visuel social : message → `1.accroche` / `1.appel` ; livraison : `logo_fichier_pendant` + `logo_raison` ; direction : `colorway_en` et zone calme conditionnelle ; conformité : `1.logo_attendu`.
- [x] maestro : `selon.sauf` (86640be, selftest 69/69).
- [x] Sans collision : aucun redémarrage du moteur, nœud de la session sœur intact, `_data/chaines` basculé seulement à la relance, file et moteur vides.
- [x] Preuves sur le moteur (Grabuge Fest v4, rapide, graine 71) : ambiance → pas de logo, pas de zone calme, part dans la palette 0,913 ; « avec » → logo posé haut-centre dans une zone laissée calme, retrouvé sur 100 % des images, 0,927 ; visuel avec message → logo posé, 0,999 ; « sans » → pas de logo et le nom de la marque GARDÉ dans le sous-message (défaut vu sur la première preuve : le nom était ôté sans logo — corrigé par un `laisser_au_logo` conditionnel), 0,971. Consigne : « brand color mood: brown, orange » (avant : les 19 couleurs, 0,965 sur la même graine).
- [x] Deux bascules de `_data/chaines`, à 22:11:37 et 22:27:33, chacune file et moteur vides, la seconde déclenchée par une boucle qui a attendu la fin d'un visuel social d'un autre process ; sauvegardes dans le scratchpad (`bascule-avant*`).
