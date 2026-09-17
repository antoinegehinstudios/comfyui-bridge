"""LE SOCLE INK — le témoin exécutable de ce qui ne change pas.

Antoine, 2026-09-15, sur les vidéos ink livrées à midi : « c'est parfait ;
assure-toi que les étapes sont avérément indépendantes ; les styles, ambiances,
type de tracé sont des paramètres ajustables ; ce qui n'est pas soumis à un
paramètre est un plan agnostique qui donne les étapes ; tous les réglages par
défaut sont ceux de ce style ink ; cette base ne doit pas changer ».

Antoine, 2026-09-16 : LA CHAÎNE NE NOMME AUCUNE TECHNIQUE — « la mention de
brume ne doit pas être tenue par le workflow de la passerelle : cela veut dire
qu'il porte une dépendance à la brume et devra se faire doublon pour faire
autrement ». Le socle se lit donc à deux endroits depuis ce jour : le PLAN dans
la chaîne (onze étapes, leurs genres, les rôles qu'elle nomme, ses contrôles
communs) et l'ENCRE dans sa technique (défauts, options, quel graphe tient
chaque rôle et ce qu'il reçoit, ses contrôles « plan_tenu »). Rien n'a bougé
dans ce qui est épinglé : ce qui était écrit dans la chaîne est écrit dans la
technique, mot pour mot, et l'encre reste la technique PAR DÉFAUT.

Une modification qui fait échouer ce fichier est une décision à écrire ici,
jamais un accident. Une technique de plus est un FICHIER de plus : elle ne
touche à rien de ce qui est épinglé.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
TECHNIQUES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "techniques-exemples"

# LE PLAN AGNOSTIQUE : les étapes et leurs genres, dans cet ordre.
PLAN = [("analyse", "rendre"), ("culture", "rendre"), ("intention", "rendre"),
        ("plan_valide", "verifier"), ("deroulement", "rendre"),
        # DÉCISION ÉCRITE, 2026-09-17 au soir (Antoine : « il ne faut plus que
        # maestro annonce des erreurs quand la vidéo est très bien, c'est
        # l'utilisateur qui juge ») : la peinture se CONSTATE, elle ne se refuse
        # plus ; le plan, lui, se juge toujours avant de peindre.
        ("plan_tenu", "constater"),
        ("raccord", "extraire_queue"), ("conclusion", "rendre"), ("appel", "rendre"),
        ("montage", "recoller"), ("controle", "verifier")]

# Les trois étapes que le plan confie à une TECHNIQUE, par leur rôle.
# DÉCISION ÉCRITE, 2026-09-16 au soir (Antoine : « les paramètres — le style de
# tracé, le fond, l'ambiance, la technique de style… — ne vivent pas dans le
# workflow mais se réconcilient avec le workflow quand le paramètre l'appelle ») :
# l'appel final est un rôle comme les deux autres — le plan ne nomme plus
# aucun graphe de peinture ni d'écriture.
ROLES_DU_PLAN = {"deroulement": "deroulement", "conclusion": "conclusion", "appel": "appel"}

# LES DÉFAUTS COMMUNS — ceux du plan, que toute technique partage.
DEFAUTS_DU_PLAN = {
    "duration_s": 45, "contemplation_s": 4, "conclusion_s": 8, "cta": "", "cta_police": "",
    "style_narratif": "reseau-social", "style_approche": "peinture-calme",
    # « conduite » et « bords » ne sont plus ici : ce sont des réglages de la
    # PEINTURE (2026-09-16 au soir), exposés par les techniques qui les lisent.
    # DÉCISION ÉCRITE, 2026-09-15 au soir (Antoine : « valeurs par défaut :
    # portrait, 720p, 30 i/s ») : le format n'est pas un trait du style ink, c'est
    # un réglage d'usage — orientation et résolution, vocabulaire « formats » de
    # la réconciliation. Le défaut passe de 704×1280 à 25 i/s (midi) à 720×1280
    # à 30 i/s ; le nœud d'encre accepte un pas de 8 en largeur (720 = 90 × 8).
    "width": 720, "height": 1280, "fps": 30, "seed": 71,
    # DÉCISION ÉCRITE, 2026-09-16 : la technique est un CHOIX, et l'encre est
    # celle qui s'ouvre — c'est le rendu livré le 2026-09-15 à midi. Depuis le
    # soir, ce n'est plus la chaîne qui la nomme : le champ « technique » n'a
    # PAS de défaut, c'est l'encre qui se dit « par_defaut » dans son fichier.
}

# LES DÉFAUTS DU STYLE INK — ceux des vidéos livrées le 2026-09-15 à midi.
# DÉCISION ÉCRITE, 2026-09-17 (Antoine : « les champs doivent être nettoyés
# pour enlever ceux en doublon ou fantômes ») : « rendu », « bords » et
# « conduite » ne sont plus EXPOSÉS — leurs autres options étaient « le rendu
# d'avant » ou « essai » —, ni « washi-sans-lampe », ni l'ambiance
# « selon-le-fond », ni le négatif « selon-l-oeuvre ». Ce que le mode emploie
# reste en LITTÉRAL dans le graphe (LITTERAUX_DU_GRAPHE) et les nœuds gardent
# leurs défauts d'identité : le rendu livré le 15 à midi n'a pas bougé.
DEFAUTS_INK = {"fond": "washi", "ambiance": "lanterne", "encre": "lavis", "negatif": "non"}
LITTERAUX_DU_GRAPHE = {"rendu": "ink-bleed", "conduite": "le plan", "bords": "fondus"}

BORNES_DU_PLAN = {"contemplation_s": (3, 5), "conclusion_s": (0, 30), "duration_s": (5, 79)}
OPTIONS_INK = {
    "fond": ["washi", "sepia", "gris-atelier"],
    "ambiance": ["lanterne", "chandelle", "atelier"],
    "encre": ["lavis", "trait-sec", "encre-dense"],
    # « selon-l-oeuvre » revenu le 17 au soir : retiré le matin comme inemployé,
    # c'est le mode du nœud pour une œuvre sombre (une scène de nuit peinte en
    # positif ne se lit qu'à la couleur — Antoine, Bloodborne). Le défaut reste « non ».
    "negatif": ["non", "oui", "selon-l-oeuvre"],
}
# LE VOCABULAIRE DES CATÉGORIES DE CHAMPS, déclaré une fois (réconciliation),
# dans cet ordre ; chaque champ exposé en nomme une et porte son aide.
CATEGORIES_DE_CHAMPS = ["oeuvre", "recit", "format", "technique", "matiere", "lumiere", "avance"]
CATEGORIES_DU_PLAN = {
    "image": "oeuvre",
    "duration_s": "recit", "contemplation_s": "recit", "conclusion_s": "recit",
    "cta": "recit", "cta_police": "recit", "style_narratif": "recit", "style_approche": "recit",
    "width": "format", "height": "format", "fps": "format",
    "seed": "avance", "technique": "technique",
}
CATEGORIES_DE_L_ENCRE = {"fond": "matiere", "encre": "matiere", "negatif": "matiere",
                         "ambiance": "lumiere"}

# CE QUE CHAQUE ÉTAPE DU PLAN APPELLE, ET CE QU'ELLE REÇOIT DE L'AMONT.
CONTRAT_DU_PLAN = {
    "analyse": ("image-iconographe", {"media": {"image": "$image"}}),
    "culture": ("image-iconologue", {"1.markers_json": "$analyse.recit.markers_json",
                                     "1.anchors_json": "$analyse.recit.anchors_json",
                                     "1.empreinte_iconographe": "$analyse.recit.empreinte"}),
    "intention": ("image-intention", {"70.style_approche": "$style_approche",
                                      "62.markers_json": "$analyse.recit.markers_json",
                                      "62.culture_json": "$culture.recit.culture_json",
                                      "62.anchors_json": "$culture.recit.anchors_json"}),
}

# CE QUE L'ENCRE MET DERRIÈRE CHAQUE RÔLE : le graphe, et ses entrées de nœud.
CONTRAT_DE_L_ENCRE = {
    "deroulement": ("video-reveal-cinematic-dirige", {
        "61.markers_json": "$analyse.recit.markers_json",
        "61.direction_json": "$intention.recit.direction_json",
        "61.fond": "$fond", "61.ambiance": "$ambiance", "61.encre": "$encre",
        "61.contemplation_s": "$contemplation_s", "61.negatif": "$negatif"}),
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


@pytest.fixture(scope="module")
def technique_encre():
    return json.loads((TECHNIQUES / "encre.json").read_text(encoding="utf-8"))


# -- le plan -------------------------------------------------------------------


def test_le_plan_est_agnostique_et_ne_nomme_aucune_technique(ink):
    """Les onze étapes, dans cet ordre, avec leurs genres — et les deux étapes
    qui peignent nomment un RÔLE, jamais un graphe. C'est ce qui fait qu'une
    technique de plus est un fichier de plus, et non une chaîne de plus."""
    chaine = noyau.lire(ink, "video-revelation")
    assert [(e.id, e.genre) for e in chaine.etapes] == PLAN
    assert ink["livrable"] == "$montage.livrable"
    for ident, role in ROLES_DU_PLAN.items():
        etape = [e for e in chaine.etapes if e.id == ident][0]
        assert etape.role == role and etape.workflow is None, ident
        assert etape.technique == "$technique", ident
    # …et le contrôle de la peinture prend sa liste chez la technique.
    plan_tenu = [e for e in chaine.etapes if e.id == "plan_tenu"][0]
    assert plan_tenu.params == {"technique": "$technique", "controles": "plan_tenu"}
    # Aucun nom de graphe de peinture ni d'écriture ne reste dans le plan, et
    # aucun nom de technique non plus — hors des notes, qui racontent l'histoire.
    sans_notes = json.dumps({k: v for k, v in ink.items() if k != "notes"}, ensure_ascii=False)
    for graphe in ("video-reveal-cinematic-dirige", "video-reveal-closing",
                   "video-reveal-brume-dirige", "video-reveal-brume-closing",
                   "video-appel-final"):
        assert graphe not in sans_notes, graphe
    for mot in ("encre", "brume"):
        assert mot not in sans_notes.lower(), mot
    # Le champ de technique ne porte AUCUN défaut : c'est la technique qui se dit.
    assert "defaut" not in ink["expose"]["technique"]


def test_les_defauts_communs_sont_ceux_du_plan(ink):
    """Les réglages du PLAN — durée, contemplation, conclusion, appel, structure,
    approche, conduite, bords, format, graine — et le choix de la technique, qui
    s'ouvre sur l'encre."""
    expose = ink["expose"]
    assert {k: expose["image"][k] for k in ("media", "requis", "libelle", "categorie")} == {
        "media": "image", "requis": True, "libelle": "L'image à révéler", "categorie": "oeuvre"}
    defauts = {k: v["defaut"] for k, v in expose.items() if k not in ("image", "technique")}
    assert defauts == DEFAUTS_DU_PLAN
    for champ, (mini, maxi) in BORNES_DU_PLAN.items():
        assert (expose[champ]["min"], expose[champ]["max"]) == (mini, maxi), champ
    assert "conduite" not in expose and "bords" not in expose        # chez les techniques
    # DÉCISION ÉCRITE, 2026-09-17 : la structure du récit ne liste que celles
    # qui portent une accroche — le plan l'exige (plan_valide), et dix-neuf
    # structures du catalogue sur vingt et une faisaient échouer le mode.
    assert expose["style_narratif"]["options_depuis"] == {"menu": "style_narratif",
                                                          "requiert": {"temps": "hook"}}
    assert expose["style_approche"]["options_depuis"] == {"menu": "style_approche"}
    # La liste des techniques n'est écrite nulle part : elle est celle des fichiers.
    assert expose["technique"]["options_depuis"] == {"techniques": True}
    assert "options" not in expose["technique"]


def test_chaque_etape_ne_recoit_que_le_contrat_declare(ink):
    """Les étapes sont indépendantes : chacune n'apprend de l'amont que par ce
    que la chaîne déclare — un champ exposé, le récit ou le livrable d'une
    étape PRÉCÉDENTE."""
    etapes = {e["id"]: e for e in ink["etapes"]}
    for ident, (workflow, attendu) in CONTRAT_DU_PLAN.items():
        rendre = etapes[ident]["rendre"]
        assert rendre["workflow"] == workflow, ident
        recu = dict(rendre.get("inputs") or {})
        if "media" in attendu:
            assert rendre["media"] == attendu["media"], ident
            attendu = {k: v for k, v in attendu.items() if k != "media"}
        assert recu == attendu, ident
    assert etapes["raccord"]["extraire_queue"] == {"video": "$deroulement.livrable", "images": 50}
    # Les étapes à rôle ne portent QUE ce que le plan sait : le média et le format.
    assert etapes["deroulement"]["rendre"]["media"] == {"image": "$image"}
    assert "inputs" not in etapes["deroulement"]["rendre"]
    assert etapes["conclusion"]["rendre"]["media"] == {"video": "$raccord.depot"}
    assert "inputs" not in etapes["conclusion"]["rendre"]
    assert etapes["appel"]["rendre"]["media"] == {"video": "$conclusion.livrable"}
    assert "inputs" not in etapes["appel"]["rendre"]
    assert etapes["appel"]["quand"] == "$cta"
    # DÉCISION ÉCRITE, 2026-09-16 (Antoine : « à la toute fin, tu éprouveras avec
    # une vidéo 4K/60 fps de 20 s ») : L'APPEL NE CHARGE PLUS LA CONCLUSION
    # ENTIÈRE. Le nœud la lit paresseusement et ne rend que les images qu'il
    # écrit ; le montage joint donc la conclusion SANS celles que l'appel a
    # reprises, puis l'appel — sinon on les verrait deux fois. Sans CTA, l'étape
    # est sautée : son « sinon » rend un livrable nul (la part est ignorée) et
    # zéro image reprise (rien n'est rogné), et le montage retombe à deux parts.
    assert etapes["appel"]["sinon"] == {"livrable": None, "recit": {"images_reprises": 0}}
    assert etapes["montage"]["recoller"]["parts"] == [
        "$deroulement.livrable",
        {"fichier": "$conclusion.livrable",
         "sauf_les_dernieres": "$appel.recit.images_reprises"},
        "$appel.livrable"]
    # et aucun renvoi ne vise l'aval : la lecture le prouve
    noyau.lire(ink, "video-revelation")


def test_les_controles_communs_gardent_le_plan_et_le_livrable(ink):
    """Le plan se juge avant de peindre, et le livrable après le montage : ces
    deux-là ne dépendent d'aucune technique et restent dans la chaîne."""
    etapes = {e["id"]: e for e in ink["etapes"]}
    plan = etapes["plan_valide"]["verifier"]
    assert [c["id"] for c in plan["controles"]] == CONTROLES_PLAN_VALIDE
    # …ET ce que la technique choisie exige du plan, jugé au même moment :
    # un plan à un tracé sur six temps a été peint trente-deux minutes en 720p
    # avant que l'encre le refuse (2026-09-17, job 464e6a4c).
    assert (plan["technique"], plan["controles_de_la_technique"]) == ("$technique", "plan")
    assert [c["id"] for c in etapes["controle"]["verifier"]] == [
        "le_montage_a_ses_parts", "livrable_pese"]
    # Deux parts (sans appel) ou trois (avec) : le contrôle dit l'un ET l'autre
    # depuis que l'appel est une part à lui, et non la conclusion réécrite.
    parts = [c for c in etapes["controle"]["verifier"]
             if c["id"] == "le_montage_a_ses_parts"][0]
    assert (parts["op"], parts["attendu"]) == ("between", [2, 3])


# -- la technique encre --------------------------------------------------------


def test_l_encre_tient_les_roles_du_plan(technique_encre):
    """Ce qui était écrit dans la chaîne est écrit ici, mot pour mot : quel graphe
    tient chaque rôle, et ce qu'il reçoit. La fermeture reçoit ainsi
    « fermeture_json » du récit du déroulement au lieu de relire le disque."""
    lue = noyau.lire_technique(technique_encre, "encre")
    assert lue.nom == "encre" and lue.libelle == "Encre"
    assert sorted(lue.roles) == sorted(CONTRAT_DE_L_ENCRE)
    for role, (workflow, inputs) in CONTRAT_DE_L_ENCRE.items():
        assert lue.roles[role].workflow == workflow, role
        assert lue.roles[role].inputs == inputs, role


def test_les_defauts_de_l_encre_sont_ceux_du_style_livre(technique_encre):
    """Tous les réglages par défaut de l'encre sont ceux du rendu livré le
    2026-09-15 : cette base ne change pas. Un style de plus est une OPTION de
    plus, jamais un défaut de moins — et depuis le 2026-09-17, une option
    « d'avant » ou « essai » n'est plus une option : ce que le mode n'emploie
    pas ne s'expose pas (voir DEFAUTS_INK)."""
    expose = technique_encre["expose"]
    assert {k: v["defaut"] for k, v in expose.items()} == DEFAUTS_INK
    for champ, options in OPTIONS_INK.items():
        assert expose[champ]["options"][0] == options[0], champ      # le défaut en tête
        assert expose[champ]["options"] == options, champ           # ni plus, ni moins


def test_chaque_champ_porte_sa_categorie_et_son_aide(ink, technique_encre):
    """Antoine, 2026-09-17 : « chacun porte une réconciliation concrète, en
    standardisant par catégorie ». Chaque champ exposé — par le plan comme par
    chaque technique — nomme sa catégorie dans le vocabulaire déclaré une fois,
    et dit ce qu'il fait. Les aides ne vivent plus sur l'entrée de
    réconciliation du mode : six d'entre elles y parlaient de champs que le plan
    n'expose plus (mesuré)."""
    extrait = json.loads((EXEMPLES / "reconciliation.extrait.json").read_text(encoding="utf-8"))
    assert [c["valeur"] for c in extrait["categories_de_champs"]] == CATEGORIES_DE_CHAMPS
    assert all(c["titre"] for c in extrait["categories_de_champs"])
    assert "aides" not in extrait["workflows"]["video-revelation"]
    assert "encre" not in extrait["workflows"]["video-revelation"]["description"].lower()
    for nom, champ in ink["expose"].items():
        assert champ.get("categorie") == CATEGORIES_DU_PLAN[nom], nom
        assert champ.get("aide", "").strip(), nom
    assert {k: v["categorie"] for k, v in technique_encre["expose"].items()} == CATEGORIES_DE_L_ENCRE
    for fichier in sorted(TECHNIQUES.glob("*.json")):
        expose = json.loads(fichier.read_text(encoding="utf-8"))["expose"]
        for nom, champ in expose.items():
            assert champ.get("categorie") in CATEGORIES_DE_CHAMPS, (fichier.name, nom)
            assert champ.get("aide", "").strip(), (fichier.name, nom)
    # Un même nom ne désigne plus deux choses : la teinte de la brume a son nom.
    brume = json.loads((TECHNIQUES / "brume.json").read_text(encoding="utf-8"))
    assert "fond" not in brume["expose"] and "brume" in brume["expose"]
    assert "rendu" not in extrait["menus"] and "conduite" not in extrait["menus"] \
        and "bords" not in extrait["menus"]
    assert list(extrait["menus"]["fond"]["libelles"]) == OPTIONS_INK["fond"]


def test_l_encre_porte_les_controles_de_la_peinture(technique_encre):
    """« plan_tenu » juge ce que la peinture a MESURÉ contre ce que le plan
    promettait — et ces grandeurs-là sont celles de l'encre, pas du plan : une
    autre technique en mesure d'autres."""
    lue = noyau.lire_technique(technique_encre, "encre")
    assert [c["id"] for c in lue.controles["plan_tenu"]] == CONTROLES_PLAN_TENU
    # Et ce que l'encre exige du PLAN, avant de peindre : un quart de temps
    # tracés au moins — la part des tracés que l'intention écrit dans son récit.
    assert lue.controles["plan"] == (
        {"id": "le_plan_porte_des_traits", "valeur": "$intention.recit.part_des_traces",
         "op": "gte", "attendu": 0.25},
        # …et une accroche que le temps suivant ne recouvre pas : nichée dans
        # lui, sa saignée s'arrête à sa porte dès l'ouverture (job 9b554fd9,
        # vingt-cinq minutes de peinture avant le refus).
        {"id": "l_accroche_n_est_pas_dans_le_temps_suivant",
         "valeur": "$intention.recit.accroche_couverte_par_le_suivant",
         "op": "lte", "attendu": 0.5})
    # Chaque technique dit ce qu'elle exige du plan, fût-ce rien (liste vide).
    for fichier in sorted(TECHNIQUES.glob("*.json")):
        autre = noyau.lire_technique(json.loads(fichier.read_text(encoding="utf-8")), fichier.stem)
        assert "plan" in autre.controles, fichier.name
        assert (autre.controles["plan"] == ()) == (fichier.stem != "encre"), fichier.name


def test_la_chaine_et_ses_techniques_tiennent_ensemble(ink):
    """Chaque technique tient les deux rôles que le plan nomme et porte la liste
    de contrôles qu'il demande, et aucun de leurs renvois ne vise l'aval : c'est
    ce que la lecture prouve avant de dépenser la moindre seconde de rendu."""
    chaine = noyau.lire(ink, "video-revelation")
    techniques = {}
    for fichier in sorted(TECHNIQUES.glob("*.json")):
        lue = noyau.lire_technique(json.loads(fichier.read_text(encoding="utf-8")), fichier.stem)
        techniques[lue.nom] = lue
    assert "encre" in techniques and len(techniques) >= 2
    noyau.verifier_techniques(chaine, techniques)
    # L'encre est celle qui s'ouvre — parce qu'elle SE DIT par défaut, pas
    # parce que la chaîne la nomme.
    assert techniques["encre"].par_defaut is True
    assert sum(1 for t in techniques.values() if t.par_defaut) == 1
    assert noyau.technique_choisie(chaine, {}, techniques).nom == "encre"
    assert noyau.defauts(chaine, techniques["encre"])["technique"] == "encre"
    assert noyau.valeurs(chaine, {"image": "x.jpg"}, {}, techniques)[0]["technique"] == "encre"
    # Et le socle de l'encre est bien ce que le mode propose par défaut.
    assert {k: v for k, v in noyau.defauts(chaine, techniques["encre"]).items()
            if k in DEFAUTS_INK} == DEFAUTS_INK


# -- les graphes locaux --------------------------------------------------------


def test_le_graphe_local_du_deroulement_porte_les_memes_defauts():
    """Les graphes vivent hors du dépôt (ce qui décrit une machine) ; quand ils
    sont là, leurs littéraux disent la même base que la technique."""
    graphes = RACINE / "_data" / "workflows"
    if not (graphes / "video-reveal-cinematic-dirige.json").is_file():
        pytest.skip("pas de _data/workflows sur ce poste")
    noeud = json.loads((graphes / "video-reveal-cinematic-dirige.json").read_text(encoding="utf-8"))["61"]
    assert noeud["class_type"] == "RevealCinematic"
    for cle, valeur in DEFAUTS_INK.items():
        assert noeud["inputs"][cle] == valeur, cle
    # Ce que le mode emploie sans plus l'exposer : en littéral dans le graphe.
    for cle, valeur in LITTERAUX_DU_GRAPHE.items():
        assert noeud["inputs"][cle] == valeur, cle
    assert noeud["inputs"]["contemplation_s"] == DEFAUTS_DU_PLAN["contemplation_s"]
    fermeture = json.loads((graphes / "video-reveal-closing.json").read_text(encoding="utf-8"))["6"]
    assert fermeture["class_type"] == "InkClosing"
    assert fermeture["inputs"]["hold_s"] == 0.5 and not fermeture["inputs"].get("goutte", False)
    for cle in ("fermeture_json", "appel_texte", "prolongation_s"):
        assert cle in fermeture["inputs"], cle
    appel = json.loads((graphes / "video-appel-final.json").read_text(encoding="utf-8"))["7"]
    assert appel["class_type"] == "InkCaption" and "police" in appel["inputs"]
