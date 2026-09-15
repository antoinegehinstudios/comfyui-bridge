"""LE SOCLE INK — le témoin exécutable de ce qui ne change pas.

Antoine, 2026-09-15, sur les vidéos ink livrées à midi : « c'est parfait ;
assure-toi que les étapes sont avérément indépendantes ; les styles, ambiances,
type de tracé sont des paramètres ajustables ; ce qui n'est pas soumis à un
paramètre est un plan agnostique qui donne les étapes ; tous les réglages par
défaut sont ceux de ce style ink ; cette base ne doit pas changer ».

Ce fichier ÉPINGLE cette base sur la chaîne de référence commitée : le plan (les
onze étapes, leur ordre, leurs genres), les défauts de chaque champ exposé, les
contrôles qui gardent le plan et la peinture, et le contrat par lequel chaque
étape parle à la suivante. Une modification qui le fait échouer est une
décision à écrire ici, jamais un accident. Un style de plus s'ajoute dans les
catalogues et les tables ; il ne touche à rien de ce qui est épinglé.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"

# LE PLAN AGNOSTIQUE : les étapes et leurs genres, dans cet ordre.
PLAN = [("analyse", "rendre"), ("culture", "rendre"), ("intention", "rendre"),
        ("plan_valide", "verifier"), ("deroulement", "rendre"), ("plan_tenu", "verifier"),
        ("raccord", "extraire_queue"), ("conclusion", "rendre"), ("appel", "rendre"),
        ("montage", "recoller"), ("controle", "verifier")]

# LES DÉFAUTS DU STYLE INK — ceux des vidéos livrées le 2026-09-15 à midi.
DEFAUTS_INK = {
    "duration_s": 45, "contemplation_s": 4, "conclusion_s": 8, "cta": "", "cta_police": "",
    "style_narratif": "reseau-social", "style_approche": "peinture-calme",
    "fond": "washi", "ambiance": "lanterne", "encre": "lavis", "negatif": "non",
    "rendu": "ink-bleed", "conduite": "le plan", "bords": "fondus",
    # DÉCISION ÉCRITE, 2026-09-15 au soir (Antoine : « valeurs par défaut :
    # portrait, 720p, 30 i/s ») : le format n'est pas un trait du style ink, c'est
    # un réglage d'usage — orientation et résolution, vocabulaire « formats » de
    # la réconciliation. Le défaut passe de 704×1280 à 25 i/s (midi) à 720×1280
    # à 30 i/s ; le nœud d'encre accepte un pas de 8 en largeur (720 = 90 × 8).
    "width": 720, "height": 1280, "fps": 30, "seed": 71,
}
BORNES_INK = {"contemplation_s": (3, 5), "conclusion_s": (0, 30), "duration_s": (5, 79)}
OPTIONS_INK = {
    "fond": ["washi", "washi-sans-lampe", "sepia", "gris-atelier"],
    "ambiance": ["lanterne", "chandelle", "atelier", "selon-le-fond"],
    "encre": ["lavis", "trait-sec", "encre-dense"],
    "negatif": ["non", "oui", "selon-l-oeuvre"],
    "rendu": ["ink-bleed", "classique", "front-organique"],
    "conduite": ["le plan", "la camera"],
    "bords": ["fondus", "francs"],
}

# CE QUE CHAQUE ÉTAPE RENDUE APPELLE, ET CE QU'ELLE REÇOIT DE L'AMONT.
CONTRAT_INK = {
    "analyse": ("image-iconographe", {"media": {"image": "$image"}}),
    "culture": ("image-iconologue", {"1.markers_json": "$analyse.recit.markers_json",
                                     "1.anchors_json": "$analyse.recit.anchors_json",
                                     "1.empreinte_iconographe": "$analyse.recit.empreinte"}),
    "intention": ("image-intention", {"70.style_approche": "$style_approche",
                                      "62.markers_json": "$analyse.recit.markers_json",
                                      "62.culture_json": "$culture.recit.culture_json",
                                      "62.anchors_json": "$culture.recit.anchors_json"}),
    "deroulement": ("video-reveal-cinematic-dirige", {
        "61.markers_json": "$analyse.recit.markers_json",
        "61.direction_json": "$intention.recit.direction_json",
        "61.fond": "$fond", "61.ambiance": "$ambiance", "61.encre": "$encre",
        "61.rendu": "$rendu", "61.conduite": "$conduite",
        "61.contemplation_s": "$contemplation_s", "61.negatif": "$negatif",
        "61.bords": "$bords"}),
    "conclusion": ("video-reveal-closing", {
        "6.fond": "$fond", "6.ambiance": "$ambiance",
        "6.depart_s": "$deroulement.recit.duree_retenue_s",
        "6.appel_texte": "$cta", "6.fermeture_json": "$deroulement.recit.fermeture_json"}),
    "appel": ("video-appel-final", {"7.texte": "$cta", "7.police": "$cta_police"}),
}

CONTROLES_PLAN_VALIDE = [
    "le_plan_nomme_son_accroche", "le_plan_nomme_son_climax",
    "l_accroche_ne_devoile_pas_le_climax", "le_plan_a_de_quoi_croitre",
    "l_accroche_est_un_detail", "l_accroche_montre_de_la_matiere",
    "le_climax_est_la_figure_de_l_oeuvre", "le_climax_garde_un_coeur_pour_la_fin",
    "le_trajet_ne_revient_pas", "le_plan_tient_dans_l_approche"]
CONTROLES_PLAN_TENU = [
    "l_accroche_est_vue_a_2_5_s", "le_climax_est_hors_du_cadre_d_ouverture",
    "le_climax_est_tenu", "les_temps_se_suivent", "l_ordre_du_plan_est_suivi",
    "chaque_temps_est_cadre_a_son_heure", "aucun_temps_supprime", "la_camera_glisse",
    "la_camera_ne_saccade_pas", "la_page_ne_s_acheve_pas_d_un_coup",
    "les_temps_sont_lisibles", "l_ordre_d_arrivee_est_celui_du_plan",
    "le_coeur_vient_en_dernier", "aucun_temps_n_est_un_trou",
    "des_traits_et_pas_que_des_blocs", "la_contemplation_ne_fige_pas",
    "la_camera_ne_voit_jamais_de_page_blanche"]


def _brut(nom):
    return json.loads((EXEMPLES / f"{nom}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ink():
    return _brut("video-revelation")


def test_le_plan_est_agnostique_et_partage(ink):
    """Les onze étapes, dans cet ordre, avec leurs genres — et la chaîne de la
    brume, autre technique, porte exactement le même plan : le plan ne dépend
    d'aucun style."""
    for nom in ("video-revelation", "video-revelation-brume"):
        chaine = noyau.lire(_brut(nom), nom)
        assert [(e.id, e.genre) for e in chaine.etapes] == PLAN, nom
    assert ink["livrable"] == "$montage.livrable"


def test_les_defauts_sont_ceux_du_style_ink(ink):
    """Tous les réglages par défaut sont ceux du rendu ink livré le 2026-09-15 :
    cette base ne change pas. Un style de plus est une OPTION de plus, jamais un
    défaut de moins."""
    expose = ink["expose"]
    assert expose["image"] == {"media": "image", "requis": True, "libelle": "L'image à révéler"}
    defauts = {k: v["defaut"] for k, v in expose.items() if k != "image"}
    assert defauts == DEFAUTS_INK
    for champ, (mini, maxi) in BORNES_INK.items():
        assert (expose[champ]["min"], expose[champ]["max"]) == (mini, maxi), champ
    for champ, options in OPTIONS_INK.items():
        assert expose[champ]["options"][0] == options[0], champ      # le défaut en tête
        assert set(expose[champ]["options"]) >= set(options), champ  # rien de retiré
    assert expose["style_narratif"]["options_depuis"] == {"menu": "style_narratif"}
    assert expose["style_approche"]["options_depuis"] == {"menu": "style_approche"}


def test_chaque_etape_ne_recoit_que_le_contrat_declare(ink):
    """Les étapes sont indépendantes : chacune n'apprend de l'amont que par ce
    que la chaîne déclare — un champ exposé, le récit ou le livrable d'une
    étape PRÉCÉDENTE. La fermeture reçoit ainsi « fermeture_json » du récit du
    déroulement au lieu de relire le disque."""
    etapes = {e["id"]: e for e in ink["etapes"]}
    for ident, (workflow, attendu) in CONTRAT_INK.items():
        rendre = etapes[ident]["rendre"]
        assert rendre["workflow"] == workflow, ident
        recu = dict(rendre.get("inputs") or {})
        if "media" in attendu:
            assert rendre["media"] == attendu["media"], ident
            attendu = {k: v for k, v in attendu.items() if k != "media"}
        assert recu == attendu, ident
    assert etapes["raccord"]["extraire_queue"] == {"video": "$deroulement.livrable", "images": 50}
    assert etapes["conclusion"]["rendre"]["media"] == {"video": "$raccord.depot"}
    assert etapes["appel"]["rendre"]["media"] == {"video": "$conclusion.livrable"}
    assert etapes["appel"]["quand"] == "$cta"
    assert etapes["montage"]["recoller"]["parts"] == ["$deroulement.livrable", "$appel.livrable"]
    # et aucun renvoi ne vise l'aval : la lecture le prouve
    noyau.lire(ink, "video-revelation")


def test_les_deux_controles_gardent_le_plan_et_la_peinture(ink):
    etapes = {e["id"]: e for e in ink["etapes"]}
    assert [c["id"] for c in etapes["plan_valide"]["verifier"]] == CONTROLES_PLAN_VALIDE
    assert [c["id"] for c in etapes["plan_tenu"]["verifier"]] == CONTROLES_PLAN_TENU
    assert [c["id"] for c in etapes["controle"]["verifier"]] == ["les_deux_parts", "livrable_pese"]


def test_le_graphe_local_du_deroulement_porte_les_memes_defauts():
    """Les graphes vivent hors du dépôt (ce qui décrit une machine) ; quand ils
    sont là, leurs littéraux disent la même base que la chaîne."""
    graphes = RACINE / "_data" / "workflows"
    if not (graphes / "video-reveal-cinematic-dirige.json").is_file():
        pytest.skip("pas de _data/workflows sur ce poste")
    noeud = json.loads((graphes / "video-reveal-cinematic-dirige.json").read_text(encoding="utf-8"))["61"]
    assert noeud["class_type"] == "RevealCinematic"
    for cle in ("fond", "ambiance", "encre", "rendu", "conduite", "negatif", "bords"):
        assert noeud["inputs"][cle] == DEFAUTS_INK[cle], cle
    assert noeud["inputs"]["contemplation_s"] == DEFAUTS_INK["contemplation_s"]
    fermeture = json.loads((graphes / "video-reveal-closing.json").read_text(encoding="utf-8"))["6"]
    assert fermeture["class_type"] == "InkClosing"
    assert fermeture["inputs"]["hold_s"] == 0.5 and not fermeture["inputs"].get("goutte", False)
    for cle in ("fermeture_json", "appel_texte", "prolongation_s"):
        assert cle in fermeture["inputs"], cle
    appel = json.loads((graphes / "video-appel-final.json").read_text(encoding="utf-8"))["7"]
    assert appel["class_type"] == "InkCaption" and "police" in appel["inputs"]
