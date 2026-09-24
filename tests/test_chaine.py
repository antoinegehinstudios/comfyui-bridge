"""Le cœur d'une chaîne : ce qu'il refuse, et ce qu'il résout.

Aucun moteur, aucun ffmpeg, aucun HTTP : c'est tout l'intérêt de ce module.
"""

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core.errors import (InputValueRefusedError, UnknownWorkflowInputError,
                                        WorkflowMappingError)


def _minimale(**remplace):
    base = {
        "version": 1,
        "chaine": "essai",
        "expose": {"duration_s": {"type": "FLOAT", "defaut": 5, "min": 2, "max": 10}},
        "etapes": [{"id": "un", "rendre": {"workflow": "wf", "duration_s": "$duration_s"}}],
        "livrable": "$un.livrable",
    }
    base.update(remplace)
    return base


def test_une_reference_vers_l_aval_est_refusee_a_la_lecture():
    """Découverte à l'exécution, elle faisait échouer la chaîne APRÈS avoir
    dépensé les étapes d'avant."""
    brut = _minimale(etapes=[
        {"id": "un", "recoller": {"parts": ["$deux.livrable"]}},
        {"id": "deux", "rendre": {"workflow": "wf"}},
    ], livrable="$deux.livrable")
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "deux" in refus.value.detail and "APRÈS" in refus.value.detail


def test_une_reference_inconnue_dit_ce_qui_existe():
    brut = _minimale(etapes=[{"id": "un", "rendre": {"workflow": "wf",
                                                     "duration_s": "$duree"}}])
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "$duree" in refus.value.detail
    assert "duration_s" in refus.value.detail        # ce qui existe est nommé


def test_un_parametre_inconnu_d_une_etape_est_refuse():
    brut = _minimale(etapes=[{"id": "un", "extraire_queue": {"video": "x.mp4",
                                                             "images": 3, "trop": 1}}],
                     livrable="$un.fichier")
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "trop" in refus.value.detail


def test_une_etape_sans_genre_ou_a_deux_genres_est_refusee():
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "un"}]))
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "un", "rendre": {"workflow": "wf"},
                                      "verifier": [{"valeur": "$duration_s", "op": "gte",
                                                    "attendu": 1}]}]))


def test_une_etape_ne_peut_pas_porter_le_nom_d_un_champ():
    """Sinon « $duration_s » ne saurait plus de quoi il parle."""
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "duration_s", "rendre": {"workflow": "wf"}}],
                             livrable="$duration_s.livrable"))


def test_les_defauts_comblent_et_les_bornes_refusent():
    chaine = noyau.lire(_minimale())
    assert noyau.valeurs(chaine, {})[0] == {"duration_s": 5.0}
    assert noyau.valeurs(chaine, {"duration_s": 7})[0] == {"duration_s": 7.0}
    with pytest.raises(InputValueRefusedError) as bas:
        noyau.valeurs(chaine, {"duration_s": 1})
    assert "minimum 2" in bas.value.detail
    with pytest.raises(InputValueRefusedError):
        noyau.valeurs(chaine, {"duration_s": 99})


def test_un_champ_requis_absent_est_refuse():
    chaine = noyau.lire(_minimale(expose={
        "image": {"media": "image", "requis": True, "libelle": "L'image"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf", "media": {"image": "$image"}}}]))
    with pytest.raises(InputValueRefusedError) as refus:
        noyau.valeurs(chaine, {})
    assert "image" in refus.value.detail
    assert noyau.valeurs(chaine, {"image": "a.png"})[0] == {"image": "a.png"}


def test_un_champ_que_la_chaine_n_expose_pas_est_refuse():
    chaine = noyau.lire(_minimale())
    with pytest.raises(UnknownWorkflowInputError) as refus:
        noyau.valeurs(chaine, {"steps": 8})
    assert "steps" in refus.value.detail


def test_une_valeur_hors_menu_est_refusee_et_le_menu_peut_venir_du_dehors():
    chaine = noyau.lire(_minimale(expose={
        "mode": {"type": "COMBO", "defaut": "a", "options": ["a", "b"]}},
        etapes=[{"id": "un", "rendre": {"workflow": "$mode"}}]))
    assert noyau.valeurs(chaine, {"mode": "b"})[0] == {"mode": "b"}
    with pytest.raises(InputValueRefusedError):
        noyau.valeurs(chaine, {"mode": "c"})
    # La liste peut être remplie par la passerelle (options_depuis) : c'est
    # celle-là qui fait autorité au moment de valider.
    assert noyau.valeurs(chaine, {"mode": "z"}, {"mode": ("z",)})[0] == {"mode": "z"}


def test_une_liste_peut_venir_du_catalogue_ou_d_un_menu_declare():
    """Une chaîne qui recopie une liste la fige au jour où on l'a écrite. Elle
    dit d'où elle vient — et les deux sources sont lues à la lecture, pas
    découvertes dans un formulaire aux choix vides."""
    for depuis in ({"catalogue": {"prefixe": "video-"}}, {"menu": "style_narratif"},
                   {"prefixe": "video-"}):
        chaine = noyau.lire(_minimale(expose={
            "mode": {"type": "COMBO", "defaut": "a", "options_depuis": depuis}},
            etapes=[{"id": "un", "rendre": {"workflow": "wf"}}]))
        assert chaine.champs["mode"].options_depuis == depuis


def test_une_liste_qui_nomme_deux_sources_ou_un_menu_sans_nom_est_refusee():
    for depuis, dit in (({"catalogue": {}, "menu": "styles"}, "deux sources"),
                        ({"menu": "   "}, "sans le nommer"),
                        ("style_narratif", "doit être un objet")):
        with pytest.raises(WorkflowMappingError) as refus:
            noyau.lire(_minimale(expose={
                "mode": {"type": "COMBO", "options_depuis": depuis}},
                etapes=[{"id": "un", "rendre": {"workflow": "wf"}}]))
        assert dit in refus.value.detail


def test_resoudre_descend_dans_les_listes_et_les_objets():
    valeurs = {"largeur": 704}
    resultats = {"un": {"livrable": "C:/a.mp4", "mesure": {"duration_s": 4.2}}}
    brut = {"parts": ["$un.livrable", {"fichier": "$un.livrable", "depuis_image": 17}],
            "largeur": "$largeur", "litteral": "video.mp4"}
    assert noyau.resoudre(brut, valeurs, resultats) == {
        "parts": ["C:/a.mp4", {"fichier": "C:/a.mp4", "depuis_image": 17}],
        "largeur": 704, "litteral": "video.mp4"}
    # Ce qui n'existe pas encore reste tel quel quand on DÉCRIT sans exécuter…
    assert noyau.resoudre("$deux.livrable", valeurs, {}, strict=False) == "$deux.livrable"
    # …et se dit quand on exécute.
    with pytest.raises(WorkflowMappingError):
        noyau.resoudre("$deux.livrable", valeurs, {})


def test_les_controles_disent_ce_qui_a_ete_mesure():
    controles = [
        {"id": "duree", "valeur": "$un.mesure.duration_s", "op": "between", "attendu": [1, 3]},
        {"id": "poids", "valeur": "$un.mesure.bytes", "op": "gte", "attendu": 1000},
        {"id": "absent", "valeur": "$un.mesure.frames", "op": "exists"},
    ]
    lignes = noyau.controler(controles, {}, {"un": {"mesure": {"duration_s": 4.0,
                                                               "bytes": 2000}}})
    assert [l["ok"] for l in lignes] == [False, True, False]
    # Un contrôle qui ne dit pas la valeur mesurée oblige à refaire le run.
    assert lignes[0]["mesure"] == 4.0 and lignes[0]["attendu"] == [1, 3]


def test_un_controle_qui_porte_une_aide_la_rend_avec_sa_ligne():
    """2026-09-19 : deux plans refusés sur « mesuré 0.245, attendu lte 0.15 »
    — juste, et muet. Un contrôle qui dit ce qu'il mesure et quoi faire le
    rend avec sa ligne ; celui qui ne dit rien n'a pas la clé (une aide vide
    n'est pas une aide)."""
    controles = [
        {"id": "aire", "valeur": "$un.recit.aire", "op": "lte", "attendu": 0.15,
         "aide": "  Un détail : au plus 15 % du cadre. Recadrer sur le sujet.  "},
        {"id": "nom", "valeur": "$un.recit.nom", "op": "exists"},
        {"id": "vide", "valeur": "$un.recit.nom", "op": "exists", "aide": "   "},
    ]
    lignes = noyau.controler(controles, {}, {"un": {"recit": {"aire": 0.245, "nom": "x"}}})
    assert [l["ok"] for l in lignes] == [False, True, True]
    assert lignes[0]["aide"] == "Un détail : au plus 15 % du cadre. Recadrer sur le sujet."
    assert "aide" not in lignes[1] and "aide" not in lignes[2]


def test_un_attendu_peut_lui_aussi_renvoyer_a_ce_qui_a_ete_demande():
    lignes = noyau.controler(
        [{"id": "tenue", "valeur": "$un.mesure.duration_s", "op": "gte",
          "attendu": "$duration_s"}],
        {"duration_s": 4}, {"un": {"mesure": {"duration_s": 4.0}}})
    assert lignes[0]["ok"] is True and lignes[0]["attendu"] == 4


def test_un_reglage_de_noeud_peut_renvoyer_a_un_champ_expose():
    """Une étape « rendre » règle une entrée de nœud par « inputs » : le renvoi
    qui s'y niche est lu à la lecture et résolu à l'exécution comme un renvoi
    à la racine. C'est le seul canal par lequel un champ exposé atteint une
    entrée de nœud sans qu'aucun code ne nomme ce nœud."""
    chaine = noyau.lire(_minimale(
        expose={"fond": {"type": "COMBO", "defaut": "washi", "options": ["washi", "sepia"]}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf", "inputs": {"61.fond": "$fond"}}}]))
    assert chaine.etapes[0].params["inputs"] == {"61.fond": "$fond"}
    assert noyau.resoudre(chaine.etapes[0].params, {"fond": "sepia"}, {}) == {
        "workflow": "wf", "inputs": {"61.fond": "sepia"}}


def test_un_renvoi_inconnu_niche_dans_inputs_est_refuse_avec_son_nom():
    """Découvert à l'exécution, il aurait fait échouer l'étape après avoir
    dépensé les précédentes ; à la lecture, il est nommé."""
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(etapes=[{"id": "un", "rendre": {
            "workflow": "wf", "inputs": {"61.fond": "$inconnu"}}}]))
    assert "$inconnu" in refus.value.detail


def test_une_piece_jointe_facultative_laissee_au_repos_vaut_non_fourni():
    """Une image de référence facultative absente ne doit pas faire échouer
    l'étape qui l'écrit dans ses médias : elle vaut None, et le média est
    simplement absent du run (mesuré le 2026-09-18 : « $image » n'avait rien à
    désigner). Un champ typé sans défaut, lui, reste absent."""
    chaine = noyau.lire(_minimale(
        expose={"image": {"media": "image", "requis": False, "libelle": "Une image"},
                "libre": {"type": "STRING"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf", "media": {"image": "$image"}}}]))
    assert noyau.valeurs(chaine, {})[0] == {"image": None}
    assert noyau.resoudre("$image", {"image": None}, {}) is None
    assert noyau.valeurs(chaine, {"image": "photo.png"})[0] == {"image": "photo.png"}


def test_un_texte_facultatif_vaut_sa_chaine_vide_et_un_booleen_son_faux():
    """« "" » et « false » sont des DÉFAUTS, pas des absences : un appel final
    facultatif laissé vide vaut la chaîne vide dans les valeurs, sinon le
    « $cta » de l'étape n'a rien à désigner et l'étape échoue. Un champ sans
    défaut, lui, reste absent."""
    chaine = noyau.lire(_minimale(
        expose={"cta": {"type": "STRING", "defaut": "", "libelle": "Appel final"},
                "signer": {"type": "BOOLEAN", "defaut": False},
                "libre": {"type": "STRING"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf",
                                        "inputs": {"7.texte": "$cta", "7.signer": "$signer"}}}]))
    assert noyau.valeurs(chaine, {})[0] == {"cta": "", "signer": False}
    assert noyau.valeurs(chaine, {"cta": "Abonnez-vous", "signer": "oui"})[0] == {
        "cta": "Abonnez-vous", "signer": True}
    # …et le renvoi se résout sur la chaîne vide, au lieu de lever.
    assert noyau.resoudre(chaine.etapes[0].params, noyau.valeurs(chaine, {})[0], {})["inputs"] == {
        "7.texte": "", "7.signer": False}


def test_une_etape_facultative_porte_un_renvoi_dans_quand():
    """« Si pas de CTA spécifié, on saute l'étape » (2026-09-15) : « quand »
    est un RENVOI vers un champ ou une étape d'amont — jamais une valeur en
    dur, jamais l'aval."""
    chaine = noyau.lire(_minimale(
        expose={"cta": {"type": "STRING", "defaut": "", "libelle": "Appel final"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf"}},
                {"id": "appel", "quand": "$cta",
                 "rendre": {"workflow": "wf", "media": {"video": "$un.livrable"}}}],
        livrable="$appel.livrable"))
    assert chaine.etapes[0].quand == "" and chaine.etapes[1].quand == "$cta"
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(etapes=[{"id": "un", "quand": "toujours",
                                      "rendre": {"workflow": "wf"}}]))
    assert "quand" in refus.value.detail
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(etapes=[{"id": "un", "quand": "$deux.livrable",
                                      "rendre": {"workflow": "wf"}},
                                     {"id": "deux", "rendre": {"workflow": "wf"}}],
                             livrable="$deux.livrable"))
    assert "APRÈS" in refus.value.detail


# -- $technique.<chemin> : ce que la technique dit de son nœud ------------------

def _a_techniques():
    """Un plan dont une étape lit, dans le fichier de la technique choisie, ce
    que son nœud impose (« $technique.budget.queue_s »)."""
    return noyau.lire(_minimale(
        expose={"duration_s": {"type": "FLOAT", "defaut": 30, "min": 5, "max": 90},
                "technique": {"type": "COMBO", "options_depuis": {"techniques": True}}},
        etapes=[{"id": "plan", "rendre": {"workflow": "wf",
                                          "inputs": {"62.duree_s": "$duration_s",
                                                     "62.queue_s": "$technique.budget.queue_s"}}},
                {"id": "peinture", "rendre": {"role": "peinture", "technique": "$technique"}}],
        livrable="$peinture.livrable"))


def _technique(nom, **plus):
    return noyau.lire_technique({"technique": nom, "roles": {"peinture": {"workflow": "g"}},
                                 "controles": {}, **plus})


def test_un_renvoi_vers_le_fichier_de_la_technique_choisie_se_resout():
    """2026-09-19 : le plan doit connaître la FIN FIXE que le nœud de la
    technique impose pour se tailler dans la durée — et c'est la technique qui
    le sait, là où elle déclare ses entrées. « $technique » reste son nom ;
    « $technique.budget.queue_s » descend dans son fichier, posé parmi les
    résultats sous le nom du champ qui la choisit, avant la première étape."""
    chaine = _a_techniques()
    techniques = {"a": _technique("a", budget={"queue_s": 7.0}, par_defaut=True),
                  "b": _technique("b", budget={"queue_s": 2.0})}
    noyau.verifier_techniques(chaine, techniques)
    valeurs, _ = noyau.valeurs(chaine, {"technique": "b"}, {}, techniques)
    depart = noyau.resultats_initiaux(chaine, techniques["b"])
    assert list(depart) == ["technique"] and depart["technique"]["budget"] == {"queue_s": 2.0}
    assert noyau.resoudre("$technique", valeurs, depart) == "b"
    assert noyau.resoudre(chaine.etapes[0].params, valeurs, depart)["inputs"] == {
        "62.duree_s": 30.0, "62.queue_s": 2.0}
    # Sans technique choisie (une chaîne sans ce champ) : rien à poser.
    assert noyau.resultats_initiaux(noyau.lire(_minimale()), None) == {}
    assert noyau.resultats_initiaux(chaine, None) == {}


def test_une_technique_qui_ne_porte_pas_le_chemin_est_refusee_a_la_lecture():
    """La technique est choisie à l'appel : celle qui ne porterait pas la
    section ferait échouer l'étape sous elle seule, après les étapes d'avant.
    Refusé quand la chaîne et ses techniques sont lues ensemble, en nommant le
    chemin — pour un renvoi de la chaîne comme pour un renvoi de la technique
    vers son propre fichier."""
    chaine = _a_techniques()
    sans = _technique("sans")
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.verifier_techniques(chaine, {"a": _technique("a", budget={"queue_s": 7.0}),
                                           "sans": sans})
    assert "budget.queue_s" in refus.value.detail and "'sans'" in refus.value.detail
    incomplete = _technique("incomplete", budget={"autre": 1})
    with pytest.raises(WorkflowMappingError, match="budget.queue_s"):
        noyau.verifier_techniques(chaine, {"incomplete": incomplete})
    # Une technique qui lit son propre fichier par le même renvoi : même règle.
    reflexive = noyau.lire_technique({
        "technique": "reflexive", "budget": {"queue_s": 5.0},
        "roles": {"peinture": {"workflow": "g", "inputs": {"61.fin": "$technique.budget.fin_s"}}}})
    with pytest.raises(WorkflowMappingError, match="budget.fin_s"):
        noyau.verifier_techniques(chaine, {"reflexive": reflexive})
    # Le fichier est gardé tel quel : une section de plus est une clé de plus.
    assert _technique("a", budget={"queue_s": 7.0}, notes={"x": "y"}).donnees["notes"] == {"x": "y"}


def test_les_entrees_du_role_passent_sous_celles_de_l_etape():
    """Ce que la technique met derrière son rôle est résolu ici et passe SOUS
    ce que l'étape écrit elle-même : le plan garde le dernier mot. La même
    lecture sert à l'aperçu (strict=False : ce qui n'existe pas encore reste
    tel quel, jamais inventé)."""
    role = noyau.Role(nom="peinture", workflow="g",
                      inputs={"61.fond": "$fond", "61.plan": "$intention.recit.plan"})
    valeurs = {"fond": "sepia"}
    assert noyau.entrees_du_role(role, {"inputs": {"61.fond": "washi"}}, valeurs,
                                 {"intention": {"recit": {"plan": "p"}}}) == {
        "61.fond": "washi", "61.plan": "p"}
    assert noyau.entrees_du_role(role, {}, valeurs, {}, strict=False) == {
        "61.fond": "sepia", "61.plan": "$intention.recit.plan"}
    with pytest.raises(WorkflowMappingError):
        noyau.entrees_du_role(role, {}, valeurs, {})


# -- memoire : le résultat d'une étape, gardé par clé --------------------------

def test_une_etape_declare_de_quoi_sa_memoire_est_faite():
    """« Le plan est gardé par clé : même image, mêmes réglages, même graine →
    même plan » (2026-09-19). La clé est une liste NON VIDE de renvois, lus à
    la lecture comme les autres ; une valeur en dur ne distingue rien, une clé
    vide dirait « toujours le même »."""
    chaine = noyau.lire(_minimale(
        expose={"duration_s": {"type": "FLOAT", "defaut": 5}, "seed": {"type": "INT", "defaut": 7}},
        etapes=[{"id": "analyse", "rendre": {"workflow": "wf"}},
                {"id": "plan", "rendre": {"workflow": "wf", "duration_s": "$duration_s",
                                          "memoire": {"cle": ["$analyse.recit.empreinte",
                                                              "$seed", "$duration_s"]}}}],
        livrable="$plan.livrable"))
    assert chaine.etapes[0].memoire is None
    assert chaine.etapes[1].memoire == ("$analyse.recit.empreinte", "$seed", "$duration_s")

    def _avec(memoire):
        return _minimale(etapes=[{"id": "plan", "rendre": {"workflow": "wf", "memoire": memoire}}],
                         livrable="$plan.livrable")
    for faux, dit in (("$seed", "attend un objet"), ({"cle": []}, "non vide"),
                      ({"cle": "$seed"}, "non vide"),
                      ({"cle": ["$duration_s"], "x": 1}, "attend un objet"),
                      ({"cle": ["$duration_s", 71]}, "en dur")):
        with pytest.raises(WorkflowMappingError) as refus:
            noyau.lire(_avec(faux))
        assert dit in refus.value.detail, (faux, refus.value.detail)
    # …et un renvoi de la clé vers l'aval ou l'inconnu est refusé comme les autres.
    with pytest.raises(WorkflowMappingError, match="APRÈS"):
        noyau.lire(_minimale(etapes=[{"id": "plan", "rendre": {
            "workflow": "wf", "memoire": {"cle": ["$suite.recit.x"]}}},
            {"id": "suite", "rendre": {"workflow": "wf"}}], livrable="$suite.livrable"))
    with pytest.raises(WorkflowMappingError, match="inconnu"):
        noyau.lire(_avec({"cle": ["$inconnu"]}))
    # Un autre genre ne se souvient de rien.
    with pytest.raises(WorkflowMappingError, match="memoire"):
        noyau.lire(_minimale(etapes=[{"id": "un", "extraire_queue": {
            "video": "x.mp4", "images": 3, "memoire": {"cle": ["$duration_s"]}}}],
            livrable="$un.fichier"))


# -- une étape sautée nomme ce qui reste sans effet -----------------------------

def test_une_etape_sautee_nomme_les_champs_qu_elle_seule_lisait():
    """Une police d'appel sans appel partait nulle part sans le dire
    (2026-09-19). Les champs sans effet : ceux que l'étape sautée seule lit —
    dans ses paramètres, ou dans ce que la technique met derrière son rôle —,
    non vides ; jamais le renvoi de « quand » (c'est lui qui est vide), jamais
    un champ qu'une autre étape lit aussi, jamais un champ laissé vide."""
    chaine = noyau.lire(_minimale(
        expose={"cta": {"type": "STRING", "defaut": ""},
                "police": {"type": "STRING", "defaut": ""},
                "fps": {"type": "INT", "defaut": 30},
                "technique": {"type": "COMBO", "options_depuis": {"techniques": True}}},
        etapes=[{"id": "video", "rendre": {"workflow": "wf", "fps": "$fps"}},
                {"id": "appel", "quand": "$cta",
                 "rendre": {"role": "appel", "technique": "$technique", "fps": "$fps",
                            "media": {"video": "$video.livrable"}}}],
        livrable="$appel.livrable"))
    technique = noyau.lire_technique({"technique": "t", "roles": {"appel": {
        "workflow": "g", "inputs": {"7.texte": "$cta", "7.police": "$police"}}}})
    appel = chaine.etapes[1]
    assert noyau.champs_lus_par(appel, technique) == {"technique", "fps", "video", "cta", "police"}
    assert noyau.sans_effet_si_sautee(chaine, appel, technique,
                                      {"cta": "", "police": "Garamond", "fps": 30,
                                       "technique": "t"}) == ["police"]
    assert noyau.sans_effet_si_sautee(chaine, appel, technique,
                                      {"cta": "", "police": "", "fps": 30, "technique": "t"}) == []
    # Sans technique (un rôle sans personne derrière) : ce que l'étape lit seule.
    assert noyau.sans_effet_si_sautee(chaine, appel, None, {"cta": "", "police": "G"}) == []


def test_un_champ_de_chaine_peut_dependre_d_un_autre_par_selon():
    """2026-09-24 : la palette qu'on choisit s'efface devant celle qu'une charte
    impose — la chaîne le déclare (« selon »), le lanceur ne montre le champ
    que sous les valeurs dites. Une forme fausse ou un champ inconnu refusent."""
    brut = {"version": 1, "chaine": "c", "resume": "r",
            "expose": {"charte": {"type": "COMBO", "defaut": "aucune", "options": ["aucune", "x"], "libelle": "Charte"},
                       "palette": {"type": "COMBO", "defaut": "libre", "options": ["libre", "chaude"], "libelle": "Palette",
                                   "selon": {"champ": "charte", "valeurs": ["aucune"]}}},
            "etapes": [{"id": "rendu", "rendre": {"workflow": "g", "prompt": "$palette", "seed": 1,
                                                   "inputs": {"1.x": "$charte"}}}],
            "livrable": "$rendu.livrable"}
    chaine = noyau.lire(brut, "c")
    assert chaine.champs["palette"].selon == {"champ": "charte", "valeurs": ["aucune"]}
    assert chaine.champs["charte"].selon is None
    import copy
    faux = copy.deepcopy(brut)
    faux["expose"]["palette"]["selon"] = {"champ": "inconnu", "valeurs": ["a"]}
    with pytest.raises(WorkflowMappingError, match="n'expose pas"):
        noyau.lire(faux, "c")
    faux["expose"]["palette"]["selon"] = {"champ": "charte"}
    with pytest.raises(WorkflowMappingError, match="selon"):
        noyau.lire(faux, "c")
    faux["expose"]["palette"]["selon"] = {"champ": "palette", "valeurs": ["libre"]}
    with pytest.raises(WorkflowMappingError, match="lui-même"):
        noyau.lire(faux, "c")
