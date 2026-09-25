"""Le RÉCONCILIANT : un seul élément par techno, le seul à lire sa source (2026-09-25).

Antoine : « seul cet élément réconciliant se met à jour, une fois, pour que tout
workflow obtienne la nouveauté » ; « lance le chantier de standardisation et
réconciliation unique par techno ». Ce que ces tests tiennent : le langage d'un
réconciliant (branchements, présence conditionnelle, variantes), le dépliage dans
une chaîne (ses champs en tête, ses étapes à leur place, ses emplacements
réécrits), et chaque refus — une source lue en direct, un emplacement inconnu, un
branchement absent ou inconnu, une collision, deux réconciliants pour une techno.
Les réconciliants d'essai vivent dans un dossier temporaire : le langage ne
dépend d'aucune source réelle.
"""

import copy
import json

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core import reconciliant
from comfyui_bridge.core.errors import WorkflowMappingError

SOURCE = {
    "reconciliant": "marque",
    "version": "1.0.0",
    "resume": "une source d'essai",
    "source": {"techno": "Blason", "graphes": ["lire-blason", "mesurer-blason"], "menus": ["marque"],
               "faits": {"menu": "marque", "colonne": "faits", "lus": ["couleurs"],
                         "libelles": {"mascottes": "les mascottes"}}},
    "branchements": {
        "media": {"requis": True, "valeurs": ["image", "video"], "aide": "ce qui est livré"},
        "prompt": {"requis": True, "aide": "la consigne"},
        "livrable": {"requis": True, "aide": "le fichier livré"},
        "accroche": {"aide": "un texte posé"},
        "duree_s": {"aide": "la durée d'une vidéo"},
    },
    "champs": {
        "marque": {"type": "COMBO", "defaut": "aucune", "options_depuis": {"menu": "marque"}, "libelle": "Marque",
                   "categorie": "charte", "aide": "la marque"},
        "cadre": {"@si": {"media": "video"}, "type": "COMBO", "defaut": "fin", "options": ["fin", "partout"],
                  "libelle": "Où", "categorie": "charte", "aide": "où"},
    },
    "etapes": [
        {"place": "debut", "etape": {
            "id": "lecture", "quand": {"valeur": "$marque", "op": "ne", "attendu": "aucune"},
            "rendre": {"workflow": "lire-blason",
                       "inputs": {"1.marque": "$marque", "1.prompt": "@prompt", "1.accroche": "@accroche",
                                  "1.duree_s": "@duree_s", "1.cadre": {"@selon": "media", "video": "$cadre"}}},
            "sinon": {"livrable": None, "recit": {"positif": "@prompt", "fichier": None,
                                                  "texte": {"@": "accroche", "sinon": ""},
                                                  "raison": {"@selon": "media", "image": "sans marque",
                                                             "@autre": "sans marque : rien n'a eu lieu"}}}}},
        {"place": "apres_livraison", "etape": {
            "id": "mesure", "quand": {"valeur": "$marque", "op": "ne", "attendu": "aucune"},
            "rendre": {"workflow": "mesurer-blason", "inputs": {"1.fichier": "@livrable", "1.marque": "$marque"}}}},
        {"place": "avant_controle", "etape": {
            "id": "constat_de_la_marque", "quand": {"valeur": "$marque", "op": "ne", "attendu": "aucune"},
            "constater": [
                {"id": "la_marque_est_lue", "valeur": "$lecture.recit.tenue", "op": "eq", "attendu": True, "aide": "a"},
                {"@si": "accroche", "id": "le_texte_est_tenu", "valeur": "$mesure.recit.texte", "op": "eq",
                 "attendu": True, "aide": "b"},
                {"@sauf": "duree_s", "id": "une_image_fixe", "valeur": "$mesure.recit.fixe", "op": "eq",
                 "attendu": True, "aide": "c"}]}},
    ],
    "emplacements": {
        "consigne.positif": "$lecture.recit.positif",
        "a_poser": {"@selon": "media", "image": [{"fichier": "$lecture.recit.fichier"}],
                    "video": [{"fichier": "$lecture.recit.fichier"}, {"fichier": "$lecture.recit.carton", "debut_s": -1.5}]},
        "texte": {"police": "$lecture.recit.police", "couleur": "$lecture.recit.couleur"},
    },
}

CHAINE = {
    "version": 1, "chaine": "essai", "resume": "r",
    "reconciliants": {"marque": {"media": "image", "prompt": "$prompt", "livrable": "$livraison.livrable"}},
    "expose": {"prompt": {"type": "STRING", "defaut": "", "libelle": "Consigne", "categorie": "sujet", "aide": "a"}},
    "etapes": [
        {"id": "rendu", "rendre": {"workflow": "peindre", "prompt": "$marque.consigne.positif"}},
        {"id": "livraison", "composer": {"image": "$rendu.livrable", "images": "$marque.a_poser",
                                         "textes": [{"texte": "$prompt", "police": "$marque.texte.police"}]}},
        {"id": "constat", "constater": [{"id": "x", "valeur": "$livraison.mesure.width", "op": "gte", "attendu": 1,
                                         "aide": "a"}]},
        {"id": "controle", "verifier": [{"id": "livrable_pese", "valeur": "$livraison.mesure.bytes", "op": "gte",
                                         "attendu": 1, "aide": "a"}]},
    ],
    "livrable": "$livraison.livrable",
}


@pytest.fixture
def dossier(tmp_path):
    (tmp_path / "marque.json").write_text(json.dumps(SOURCE, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _deplier(chaine, dossier):
    return reconciliant.deplier_avec_provenance(chaine, dossier)


def test_les_reconciliants_du_poste_se_lisent_et_chaque_techno_n_en_a_qu_un():
    tous = reconciliant.tous()
    assert {"charte", "analyse", "culture"} <= set(tous)
    technos = {r["source"]["techno"] for r in tous.values()}
    assert {"Héraldiste", "Iconographe", "Iconologue"} <= technos and len(technos) == len(tous)
    assert tous["culture"]["requiert"] == ["analyse"]
    for role, rec in tous.items():
        assert rec["version"].count(".") == 2 and rec["source"]["graphes"], role


def test_deplier_pose_les_champs_en_tete_les_etapes_a_leur_place_et_reecrit_les_emplacements(dossier):
    deplie, provenance = _deplier(CHAINE, dossier)
    assert "reconciliants" not in deplie
    assert list(deplie["expose"]) == ["marque", "prompt"]                  # « cadre » n'existe qu'en vidéo
    assert [e["id"] for e in deplie["etapes"]] == ["lecture", "rendu", "livraison", "mesure", "constat",
                                                   "constat_de_la_marque", "controle"]
    etapes = {e["id"]: e for e in deplie["etapes"]}
    assert etapes["rendu"]["rendre"]["prompt"] == "$lecture.recit.positif"
    assert etapes["livraison"]["composer"]["images"] == [{"fichier": "$lecture.recit.fichier"}]
    assert etapes["livraison"]["composer"]["textes"][0]["police"] == "$lecture.recit.police"   # un sous-chemin d'emplacement
    # un branchement non donné n'écrit pas l'entrée ; donné, il y est tel quel ; une variante sans branche disparaît
    assert etapes["lecture"]["rendre"]["inputs"] == {"1.marque": "$marque", "1.prompt": "$prompt"}
    assert etapes["lecture"]["sinon"]["recit"] == {"positif": "$prompt", "fichier": None, "texte": "", "raison": "sans marque"}
    assert etapes["mesure"]["rendre"]["inputs"] == {"1.fichier": "$livraison.livrable", "1.marque": "$marque"}
    # présence conditionnelle : sans accroche, pas de constat du texte ; sans durée (une image), le constat de l'image fixe
    assert [c["id"] for c in etapes["constat_de_la_marque"]["constater"]] == ["la_marque_est_lue", "une_image_fixe"]
    assert provenance["marque"] == {"version": "1.0.0", "techno": "Blason", "champs": ["marque"],
                                    "etapes": ["lecture", "mesure", "constat_de_la_marque"],
                                    "emplacements_lus": ["a_poser", "consigne.positif", "texte"], "non_pris": []}
    # la chaîne dépliée est une chaîne ordinaire
    noyau.lire(deplie, "essai")


def test_une_video_prend_ses_variantes(dossier):
    video = copy.deepcopy(CHAINE)
    video["reconciliants"]["marque"].update({"media": "video", "accroche": "$prompt", "duree_s": 6})
    deplie, _ = _deplier(video, dossier)
    assert list(deplie["expose"]) == ["marque", "cadre", "prompt"]
    etapes = {e["id"]: e for e in deplie["etapes"]}
    assert etapes["lecture"]["rendre"]["inputs"] == {"1.marque": "$marque", "1.prompt": "$prompt", "1.accroche": "$prompt",
                                                     "1.duree_s": 6, "1.cadre": "$cadre"}
    assert etapes["lecture"]["sinon"]["recit"]["raison"] == "sans marque : rien n'a eu lieu"          # « @autre »
    assert etapes["lecture"]["sinon"]["recit"]["texte"] == "$prompt"
    assert len(etapes["livraison"]["composer"]["images"]) == 2
    assert [c["id"] for c in etapes["constat_de_la_marque"]["constater"]] == ["la_marque_est_lue", "le_texte_est_tenu"]


def test_sans_controle_final_les_constats_du_reconciliant_viennent_a_la_fin(dossier):
    sans = copy.deepcopy(CHAINE)
    sans["etapes"] = sans["etapes"][:-1]
    deplie, _ = _deplier(sans, dossier)
    assert [e["id"] for e in deplie["etapes"]][-2:] == ["constat", "constat_de_la_marque"]


def test_une_chaine_sans_reconciliant_revient_telle_quelle(dossier):
    sans = {k: v for k, v in CHAINE.items() if k != "reconciliants"}
    sans["etapes"] = [e for e in copy.deepcopy(sans["etapes"]) if e["id"] != "livraison"]
    sans["etapes"][0]["rendre"]["prompt"] = "$prompt"
    sans["etapes"][1]["constater"][0]["valeur"] = "$rendu.mesure.width"
    sans["etapes"][2]["verifier"][0]["valeur"] = "$rendu.mesure.bytes"
    sans["livrable"] = "$rendu.livrable"
    deplie, provenance = _deplier(sans, dossier)
    assert deplie == sans and provenance == {}


@pytest.mark.parametrize("faute,motif", [
    (lambda c: c["etapes"].insert(0, {"id": "lecture", "rendre": {"workflow": "peindre"}}), "réconciliant « marque » — la chaîne ne la redéclare pas"),
    (lambda c: c["expose"].update({"marque": {"type": "STRING", "defaut": "", "libelle": "m", "categorie": "c", "aide": "a"}}),
     "le champ 'marque' est celui du réconciliant"),
    (lambda c: c["etapes"].insert(0, {"id": "a_part", "rendre": {"workflow": "lire-blason"}}), "lit Blason en direct"),
    (lambda c: c["etapes"][0]["rendre"].update({"prompt": "$lecture.recit.positif"}), "passer par ses emplacements"),
    (lambda c: c["etapes"][0]["rendre"].update({"prompt": "$marque.consigne.inconnue"}), "n'a pas d'emplacement « consigne.inconnue »"),
    (lambda c: c["expose"].update({"autre": {"type": "COMBO", "defaut": "a", "options_depuis": {"menu": "marque"},
                                             "libelle": "a", "categorie": "c", "aide": "a"}}), "il n'appartient qu'au réconciliant « marque »"),
    (lambda c: c["reconciliants"]["marque"].pop("prompt"), "exige le\\(s\\) branchement\\(s\\) prompt"),
    (lambda c: c["reconciliants"]["marque"].update({"inconnu": 1}), "ne connaît pas le\\(s\\) branchement\\(s\\) inconnu"),
    (lambda c: c["reconciliants"]["marque"].update({"media": "son"}), "'son' n'est pas l'une de"),
    (lambda c: c["reconciliants"]["marque"].update({"livrable": "$ailleurs.livrable"}), "le branchement « livrable » doit désigner"),
    (lambda c: c["reconciliants"].update({"autre": {}}), "aucun réconciliant « autre »"),
])
def test_chaque_refus_nomme_ce_qui_ne_tient_pas(dossier, faute, motif):
    faux = copy.deepcopy(CHAINE)
    faute(faux)
    with pytest.raises(WorkflowMappingError, match=motif):
        _deplier(faux, dossier)


def test_une_source_se_lit_par_son_reconciliant_meme_quand_la_chaine_ne_le_declare_pas(dossier):
    """Pas de porte dérobée : une chaîne sans réconciliant qui rend le graphe d'une source est refusée aussi."""
    sans = {"version": 1, "chaine": "essai", "expose": {},
            "etapes": [{"id": "a", "rendre": {"workflow": "mesurer-blason"}}], "livrable": "$a.livrable"}
    with pytest.raises(WorkflowMappingError, match="lit Blason en direct"):
        _deplier(sans, dossier)


def test_un_reconciliant_qui_ne_tient_pas_est_refuse_a_la_lecture(tmp_path):
    def ecrire(nom, doc):
        (tmp_path / f"{nom}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    for faute, motif in (
            (lambda r: r.update({"reconciliant": "autre"}), "doit se nommer lui-même"),
            (lambda r: r.update({"version": "1"}), "versionnage sémantique"),
            (lambda r: r.update({"cle_inconnue": 1}), "clé\\(s\\) inconnue\\(s\\)"),
            (lambda r: r["champs"]["marque"].update({"aide": "@acroche"}), "sans être déclaré\\(s\\) : acroche"),
            (lambda r: r["emplacements"].update({"x": {"@selon": "media", "imgae": 1}}), "« imgae » n'est pas une valeur"),
            (lambda r: r["etapes"].append({"place": "au_milieu", "etape": {"id": "x"}}), "chaque étape s'écrit"),
            (lambda r: r["source"].pop("techno"), "nomme la techno")):
        faux = copy.deepcopy(SOURCE)
        faute(faux)
        ecrire("marque", faux)
        with pytest.raises(WorkflowMappingError, match=motif):
            reconciliant.lire("marque", tmp_path)
    ecrire("marque", SOURCE)
    ecrire("double", {**copy.deepcopy(SOURCE), "reconciliant": "double"})
    with pytest.raises(WorkflowMappingError, match="deux réconciliants lisent « Blason »"):
        reconciliant.tous(tmp_path)


def test_un_reconciliant_lit_ce_qu_un_autre_range_s_il_est_declare_apres_lui(tmp_path):
    amont = {"reconciliant": "amont", "version": "1.0.0", "source": {"techno": "A", "graphes": ["ga"]},
             "branchements": {"image": {"requis": True}},
             "etapes": [{"place": "debut", "etape": {"id": "amont", "rendre": {"workflow": "ga", "media": {"image": "@image"}}}}],
             "emplacements": {"reperes": "$amont.recit.reperes"}}
    aval = {"reconciliant": "aval", "version": "1.0.0", "requiert": ["amont"], "source": {"techno": "B", "graphes": ["gb"]},
            "branchements": {"image": {"requis": True}},
            "etapes": [{"place": "debut", "etape": {"id": "aval", "rendre": {"workflow": "gb", "media": {"image": "@image"},
                                                                             "inputs": {"1.reperes": "$amont.reperes"}}}}],
            "emplacements": {"culture": "$aval.recit.culture"}}
    for doc in (amont, aval):
        (tmp_path / f"{doc['reconciliant']}.json").write_text(json.dumps(doc), encoding="utf-8")
    chaine = {"version": 1, "chaine": "c",
              "reconciliants": {"amont": {"image": "$image"}, "aval": {"image": "$image"}},
              "expose": {"image": {"media": "image", "requis": True, "libelle": "i", "categorie": "oeuvre", "aide": "a"}},
              "etapes": [{"id": "fin", "rendre": {"workflow": "g", "media": {"image": "$image"},
                                                  "inputs": {"1.c": "$aval.culture", "1.r": "$amont.reperes"}}}],
              "livrable": "$fin.livrable"}
    deplie, provenance = reconciliant.deplier_avec_provenance(chaine, tmp_path)
    assert [e["id"] for e in deplie["etapes"]] == ["amont", "aval", "fin"]
    assert deplie["etapes"][1]["rendre"]["inputs"] == {"1.reperes": "$amont.recit.reperes"}
    assert deplie["etapes"][2]["rendre"]["inputs"] == {"1.c": "$aval.recit.culture", "1.r": "$amont.recit.reperes"}
    assert provenance["amont"]["non_pris"] == []                    # lu par l'aval ET par la chaîne
    inverse = {**chaine, "reconciliants": {"aval": {"image": "$image"}, "amont": {"image": "$image"}}}
    with pytest.raises(WorkflowMappingError, match="le déclarer AVANT lui"):
        reconciliant.deplier_avec_provenance(inverse, tmp_path)


def test_les_faits_que_la_source_sert_sans_qu_aucun_emplacement_les_prenne_sont_dits():
    """Condition d'Antoine (2026-09-25) : ce que la source sert est exhaustif ; ce que le réconciliant ne
    place pas est DIT — une nouveauté (les mascottes d'Héraldiste) se voit avant d'être câblée."""
    faits = {"couleurs": "red", "mascottes": [{"nom": "Gaston", "principale": True}], "texte": {"famille": "Segoe"},
             "vide": [], "rien": "", "aucun": None}
    dits = reconciliant.faits_non_servis(SOURCE, faits)
    assert dits == [{"fait": "mascottes", "libelle": "les mascottes", "nombre": 1, "noms": ["Gaston"]},
                    {"fait": "texte", "libelle": "texte"}]
    assert reconciliant.faits_non_servis(SOURCE, None) == []
    charte = reconciliant.lire("charte")
    assert set(charte["source"]["faits"]["lus"]) == {"positif_en", "palette_en", "interdits_en", "titres"}
    assert [d["fait"] for d in reconciliant.faits_non_servis(charte, {"palette_en": "brown", "mascottes": [{"nom": "M"}],
                                                                     "elements_graphiques": []})] == ["mascottes"]


# -- les promesses (second temps, 2026-09-25) ---------------------------------------------------
#
# Antoine : « la prise de charte doit y être présente, et à son poids exactement comme c'est défini par le
# template ; le template délivre les choses qu'il porte, sans mentir, sans faux paramètre que le workflow ne sait
# pas tenir ». Un réconciliant PROMET ; une chaîne tient chaque promesse ou la décline, avec sa raison.

PROMETTEUR = {
    **copy.deepcopy(SOURCE),
    "prise": {"champ": "marque", "sauf": ["aucune"]},
    "promesses": {"consigne": {"libelle": "les mots de la marque", "emplacements": ["consigne.positif"]},
                  "pose": {"libelle": "ce que la marque pose", "emplacements": ["a_poser"]},
                  "typo": {"libelle": "la police de la marque", "emplacements": ["texte"]}},
}


@pytest.fixture
def prometteur(tmp_path):
    (tmp_path / "marque.json").write_text(json.dumps(PROMETTEUR, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _chaine_qui_decline(sans):
    chaine = copy.deepcopy(CHAINE)
    chaine["reconciliants"]["marque"]["sans"] = sans
    return chaine


def test_chaque_promesse_est_tenue_ou_declinee_jamais_les_deux_jamais_aucune(prometteur):
    # CHAINE lit consigne.positif, a_poser et texte.police : tout est tenu
    _deplie, provenance = reconciliant.deplier_avec_provenance(CHAINE, prometteur)
    assert provenance["marque"]["declines"] == {} and set(provenance["marque"]["promesses"]) == {"consigne", "pose", "typo"}
    reconciliant.juger_les_promesses("essai", provenance)
    # une promesse tenue ET déclinée : l'un des deux ment
    _deplie, provenance = reconciliant.deplier_avec_provenance(_chaine_qui_decline({"pose": "rien à poser ici"}), prometteur)
    with pytest.raises(WorkflowMappingError, match="ce que la marque pose.*déclinée.*et pourtant tenue.*l'un des deux ment"):
        reconciliant.juger_les_promesses("essai", provenance)
    # une promesse ni tenue ni déclinée : refusée
    muette = copy.deepcopy(CHAINE)
    muette["etapes"][1]["composer"].pop("images")
    _deplie, provenance = reconciliant.deplier_avec_provenance(muette, prometteur)
    with pytest.raises(WorkflowMappingError, match="ce que la marque pose.*ni tenue.*ni déclinée"):
        reconciliant.juger_les_promesses("essai", provenance)
    # déclinée avec sa raison : tenue pour dite
    muette["reconciliants"]["marque"]["sans"] = {"pose": "ce mode ne pose aucun fichier"}
    _deplie, provenance = reconciliant.deplier_avec_provenance(muette, prometteur)
    reconciliant.juger_les_promesses("essai", provenance)
    assert provenance["marque"]["declines"] == {"pose": "ce mode ne pose aucun fichier"}
    assert provenance["marque"]["prise"] == {"champ": "marque", "sauf": ["aucune"]}
    # une promesse tenue par la TECHNIQUE de la chaîne compte comme tenue
    sans_typo = copy.deepcopy(CHAINE)
    sans_typo["etapes"][1]["composer"]["textes"][0].pop("police")
    _deplie, provenance = reconciliant.deplier_avec_provenance(sans_typo, prometteur)
    with pytest.raises(WorkflowMappingError, match="la police de la marque"):
        reconciliant.juger_les_promesses("essai", provenance)
    reconciliant.juger_les_promesses("essai", provenance, {"marque": {"texte"}})


@pytest.mark.parametrize("sans,motif", [
    ({"inconnue": "r"}, "ne promet pas inconnue"),
    ({"pose": "   "}, "chaque raison écrite"),
    (["pose"], "chaque raison écrite"),
])
def test_un_sans_qui_ne_tient_pas_est_refuse(prometteur, sans, motif):
    with pytest.raises(WorkflowMappingError, match=motif):
        reconciliant.deplier_avec_provenance(_chaine_qui_decline(sans), prometteur)


def test_des_promesses_mal_ecrites_sont_refusees_a_la_lecture(tmp_path):
    for faute, motif in (
            (lambda r: r.pop("prise"), "des promesses sans « prise »"),
            (lambda r: r["prise"].update({"champ": "pas_un_champ"}), "« prise » nomme l'un de ses champs"),
            (lambda r: r["promesses"]["pose"].update({"emplacements": ["nulle.part"]}), "qu'il ne déclare pas"),
            (lambda r: r["promesses"]["pose"].pop("libelle"), "dit son « libelle »")):
        faux = copy.deepcopy(PROMETTEUR)
        faute(faux)
        (tmp_path / "marque.json").write_text(json.dumps(faux, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(WorkflowMappingError, match=motif):
            reconciliant.lire("marque", tmp_path)


def test_une_technique_lit_les_emplacements_comme_la_chaine(dossier):
    """La technique d'une chaîne lit « $marque.texte.police » : réécrite pour CETTE chaîne, elle lit le récit de
    l'étape du réconciliant ; qu'elle lise ce récit en direct, ou un emplacement inconnu, est refusé."""
    deplie, _prov = reconciliant.chaine_executable(CHAINE, dossier)
    assert set(deplie["_emplacements"]) == {"marque"} and deplie["_etapes_reconciliees"] == {
        "lecture": "marque", "mesure": "marque", "constat_de_la_marque": "marque"}
    technique = {"technique": "t", "roles": {"peindre": {"workflow": "g", "inputs": {
        "1.police": "$marque.texte.police", "1.a_poser": "$marque.a_poser", "1.x": "$rendu.recit.x"}}}}
    reecrite = reconciliant.reecrire_technique(technique, deplie["_emplacements"], deplie["_etapes_reconciliees"], "t")
    assert reecrite["roles"]["peindre"]["inputs"] == {
        "1.police": "$lecture.recit.police", "1.a_poser": [{"fichier": "$lecture.recit.fichier"}], "1.x": "$rendu.recit.x"}
    assert technique["roles"]["peindre"]["inputs"]["1.police"] == "$marque.texte.police"          # l'original intact
    # un sous-chemin d'emplacement (« texte.police » dans l'emplacement-objet « texte ») compte pour l'emplacement
    assert reconciliant.emplacements_lus(technique, deplie["_emplacements"]) == {"marque": {"texte", "a_poser"}}
    directe = {"technique": "t", "roles": {"peindre": {"workflow": "g", "inputs": {"1.p": "$lecture.recit.police"}}}}
    with pytest.raises(WorkflowMappingError, match="lit le récit de l'étape du réconciliant « marque »"):
        reconciliant.reecrire_technique(directe, deplie["_emplacements"], deplie["_etapes_reconciliees"], "t")
    inconnue = {"technique": "t", "roles": {"peindre": {"workflow": "g", "inputs": {"1.p": "$marque.nulle_part"}}}}
    with pytest.raises(WorkflowMappingError, match="n'a pas d'emplacement « nulle_part »"):
        reconciliant.reecrire_technique(inconnue, deplie["_emplacements"], deplie["_etapes_reconciliees"], "t")
    # et la chaîne lue garde ce qu'il faut à ses techniques
    lue = noyau.lire(deplie, "essai")
    assert set(lue.emplacements) == {"marque"} and lue.etapes_reconciliees["lecture"] == "marque"
