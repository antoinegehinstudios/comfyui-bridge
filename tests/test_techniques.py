"""Les TECHNIQUES : une chaîne qui ne nomme aucune façon de peindre.

Antoine, 2026-09-16 : « la mention de brume ne doit pas être tenue par le
workflow de la passerelle : cela veut dire qu'il porte une dépendance à la brume
et devra se faire doublon pour faire autrement ». Une chaîne est un PLAN dont
les étapes nomment des RÔLES ; une technique dit quel graphe tient chaque rôle,
ce qu'il reçoit, quels réglages elle ajoute et quels contrôles elle porte. Une
technique de plus est un fichier de plus.

Éprouvé ici sur des techniques d'essai — deux façons inventées de peindre, avec
un réglage propre à chacune et un réglage de MÊME NOM aux valeurs différentes,
qui est le cas qui coûte cher.
"""

import contextlib
import json
import pathlib
import shutil
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core import chaine as noyau  # noqa: E402
from comfyui_bridge.core.errors import WorkflowMappingError  # noqa: E402
from test_chaines_api import BackendQuiLivre, _job  # noqa: E402
from test_tranches import (BUDGET_POUR_TROIS, CADENCE, GRAPHE_TRANCHABLE,  # noqa: E402
                           HAUTEUR, LARGEUR, SANS_FFMPEG, BackendQuiTranche)

# Deux graphes d'essai : le même nœud « 61 », deux façons de peindre.
GRAPHE_AU_TRAIT = {
    "61": {"class_type": "PeintureAuTrait",
           "inputs": {"fond": "papier", "grain": "fin", "largeur": 64}},
    "9": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "cortex/trait", "images": ["61", 0]}},
}
GRAPHE_AU_VOILE = {
    "61": {"class_type": "PeintureAuVoile",
           "inputs": {"fond": "voile-clair", "epaisseur": 0.5}},
    "9": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "cortex/voile", "images": ["61", 0]}},
}

# Le PLAN : il nomme un rôle et un contrôle, jamais un graphe.
CHAINE_A_TECHNIQUES = {
    "version": 1, "chaine": "chaine-a-techniques",
    "resume": "un plan, deux façons de le peindre",
    "expose": {
        "largeur": {"type": "INT", "defaut": 64, "min": 16, "max": 256,
                    "libelle": "Largeur", "unite": "px"},
        "technique": {"type": "COMBO", "defaut": "trait",
                      "options_depuis": {"techniques": True}, "libelle": "Technique"},
    },
    "etapes": [
        {"id": "peinture", "rendre": {"role": "peinture", "technique": "$technique",
                                      "width": "$largeur", "height": "$largeur"}},
        {"id": "tenue", "verifier": {"technique": "$technique", "controles": "tenue"}},
    ],
    "livrable": "$peinture.livrable",
}

TECHNIQUE_TRAIT = {
    "version": 1, "technique": "trait", "libelle": "Au trait",
    "resume": "un trait sec sur un papier",
    "expose": {
        "fond": {"type": "COMBO", "defaut": "papier", "options": ["papier", "sepia"],
                 "libelle": "Fond de départ"},
        "grain": {"type": "COMBO", "defaut": "fin", "options": ["fin", "gros"],
                  "libelle": "Grain du trait"},
    },
    "roles": {"peinture": {"workflow": "graphe-au-trait",
                           "inputs": {"61.fond": "$fond", "61.grain": "$grain",
                                      "61.largeur": "$largeur"}}},
    "controles": {"tenue": [{"id": "le_trait_est_nomme", "valeur": "$peinture.recit.hook",
                             "op": "exists"}]},
}

TECHNIQUE_VOILE = {
    "version": 1, "technique": "voile", "libelle": "Au voile",
    "resume": "un voile qui se lève",
    "expose": {
        # LE MÊME NOM, D'AUTRES VALEURS : c'est le cas qui coûte cher — une
        # liste figée sur la première technique refusait la valeur de l'autre.
        "fond": {"type": "COMBO", "defaut": "voile-clair",
                 "options": ["voile-clair", "voile-sombre"], "libelle": "Teinte du voile"},
        "epaisseur": {"type": "FLOAT", "defaut": 0.5, "min": 0, "max": 1,
                      "libelle": "Épaisseur du voile"},
    },
    "roles": {"peinture": {"workflow": "graphe-au-voile",
                           "inputs": {"61.fond": "$fond", "61.epaisseur": "$epaisseur"}}},
    "controles": {"tenue": [{"id": "le_voile_est_mesure",
                             "valeur": "$peinture.recit.temps_retenue",
                             "op": "gte", "attendu": 1}]},
}


def _ecrire(tmp: pathlib.Path, techniques=(TECHNIQUE_TRAIT, TECHNIQUE_VOILE)) -> None:
    (tmp / "graphe-au-trait.json").write_text(json.dumps(GRAPHE_AU_TRAIT), encoding="utf-8")
    (tmp / "graphe-au-voile.json").write_text(json.dumps(GRAPHE_AU_VOILE), encoding="utf-8")
    (tmp / "chaine-a-techniques.json").write_text(json.dumps(CHAINE_A_TECHNIQUES),
                                                  encoding="utf-8")
    (tmp / "techniques").mkdir(exist_ok=True)
    for technique in techniques:
        (tmp / "techniques" / f"{technique['technique']}.json").write_text(
            json.dumps(technique, ensure_ascii=False), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "menus": {"fond": {"libelle": "Le fond",
                           "libelles": {"papier": {"libelle": "Papier nu"},
                                        "voile-clair": {"libelle": "Voile clair"}}}},
        "workflows": {
            "chaine-a-techniques": {"kind": "image",
                                    "chaine": str(tmp / "chaine-a-techniques.json"),
                                    "titre": "Chaîne à techniques",
                                    "categorie": "essais", "ordre": 1},
            "graphe-au-trait": {"kind": "image", "workflow": str(tmp / "graphe-au-trait.json"),
                                "bindings": {"filename_prefix": {"node": "9",
                                                                 "input": "filename_prefix"}}},
            "graphe-au-voile": {"kind": "image", "workflow": str(tmp / "graphe-au-voile.json"),
                                "bindings": {"filename_prefix": {"node": "9",
                                                                 "input": "filename_prefix"}}},
        },
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def atelier():
    """Une passerelle dont le plan se peint de deux façons."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_techniques_"))
    _ecrire(tmp)
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=0)
    app = create_app(settings)
    faux = BackendQuiLivre(settings.comfy_output_dir)
    app.state.container.orchestrator._backend = faux
    with TestClient(app) as client:
        client.faux = faux
        client.tmp = tmp
        yield client


# -- lire une technique --------------------------------------------------------


def test_une_technique_dit_quel_graphe_tient_chaque_role():
    lue = noyau.lire_technique(TECHNIQUE_TRAIT)
    assert lue.nom == "trait" and lue.libelle == "Au trait"
    assert lue.roles["peinture"].workflow == "graphe-au-trait"
    assert lue.roles["peinture"].inputs["61.grain"] == "$grain"
    assert [c["id"] for c in lue.controles["tenue"]] == ["le_trait_est_nomme"]
    assert sorted(lue.champs) == ["fond", "grain"]


def test_une_technique_qui_ne_tient_pas_est_refusee_en_le_disant():
    """Ce qu'une lecture peut prouver faux, elle le refuse — nommé. Découvert au
    cinquième run d'une chaîne, cela aurait fait perdre les quatre précédents."""
    with pytest.raises(WorkflowMappingError, match="ne se nomme pas"):
        noyau.lire_technique({"libelle": "Sans nom"})
    with pytest.raises(WorkflowMappingError, match="roles"):
        noyau.lire_technique({"technique": "x"})
    sans_graphe = {"technique": "x", "roles": {"peinture": {"inputs": {}}}}
    with pytest.raises(WorkflowMappingError, match="ne nomme aucun .*workflow"):
        noyau.lire_technique(sans_graphe)
    sans_op = {"technique": "x", "roles": {"peinture": {"workflow": "g"}},
               "controles": {"tenue": [{"id": "c", "valeur": "$peinture.recit.x"}]}}
    with pytest.raises(WorkflowMappingError, match="opérateur"):
        noyau.lire_technique(sans_op)
    clef_en_trop = {"technique": "x", "roles": {"peinture": {"workflow": "g", "couleur": "bleu"}}}
    with pytest.raises(WorkflowMappingError, match="couleur"):
        noyau.lire_technique(clef_en_trop)


def test_un_renvoi_qui_ne_designe_rien_est_refuse_quand_les_deux_sont_lus():
    """Une technique ignore quelle chaîne l'emploie : ce contrôle-là attend de
    les voir ensemble. Un rôle absent, une liste de contrôles absente, un renvoi
    vers l'aval — tout cela se prouve avant de dépenser la première étape."""
    chaine = noyau.lire(CHAINE_A_TECHNIQUES)
    noyau.verifier_techniques(chaine, {"trait": noyau.lire_technique(TECHNIQUE_TRAIT)})

    sans_role = noyau.lire_technique({**TECHNIQUE_TRAIT, "technique": "autre",
                                      "roles": {"fermeture": {"workflow": "g"}}})
    with pytest.raises(WorkflowMappingError, match="rôle 'peinture'"):
        noyau.verifier_techniques(chaine, {"autre": sans_role})

    sans_liste = noyau.lire_technique({**TECHNIQUE_TRAIT, "technique": "muette",
                                       "controles": {}})
    with pytest.raises(WorkflowMappingError, match="contrôles 'tenue'"):
        noyau.verifier_techniques(chaine, {"muette": sans_liste})

    renvoi_faux = noyau.lire_technique({
        **TECHNIQUE_TRAIT, "technique": "fausse",
        "roles": {"peinture": {"workflow": "g", "inputs": {"61.x": "$jamais_declare"}}}})
    with pytest.raises(WorkflowMappingError, match="jamais_declare"):
        noyau.verifier_techniques(chaine, {"fausse": renvoi_faux})

    vers_l_aval = noyau.lire_technique({
        **TECHNIQUE_TRAIT, "technique": "pressee",
        "roles": {"peinture": {"workflow": "g", "inputs": {"61.x": "$tenue.controles"}}}})
    with pytest.raises(WorkflowMappingError, match="APRÈS"):
        noyau.verifier_techniques(chaine, {"pressee": vers_l_aval})


# -- le plan se juge aussi sur ce que la technique exige de lui ----------------

# La même chaîne, dont l'étape de contrôle JOINT sa propre liste à celle que la
# technique porte sous le nom demandé.
CHAINE_QUI_JUGE_AUSSI_POUR_LA_TECHNIQUE = {
    **CHAINE_A_TECHNIQUES, "resume": "un plan jugé aussi sur ce que la technique exige",
    "etapes": [
        CHAINE_A_TECHNIQUES["etapes"][0],
        {"id": "tenue", "verifier": {
            "controles": [{"id": "la_peinture_est_la", "valeur": "$peinture.livrable",
                           "op": "exists"}],
            "technique": "$technique", "controles_de_la_technique": "tenue"}},
    ],
}
TECHNIQUE_MUETTE = {**TECHNIQUE_VOILE, "controles": {"tenue": []}}          # n'exige rien
TECHNIQUE_EXIGEANTE = {
    **TECHNIQUE_VOILE, "technique": "exigeante", "libelle": "Exigeante",
    "controles": {"tenue": [{"id": "cinq_temps_au_moins",
                             "valeur": "$peinture.recit.temps_retenue",
                             "op": "gte", "attendu": 5}]},
}


def test_une_etape_joint_ses_controles_a_ceux_que_la_technique_exige():
    """2026-09-17 : un plan à un tracé sur six temps a été peint trente-deux
    minutes en 720p avant que l'encre le refuse — la part des tracés se lit
    dans le plan. Une étape « verifier » peut donc joindre sa liste à celle que
    la technique choisie porte sous un nom ; une technique qui n'exige rien le
    dit d'une liste VIDE (permise à elle seule : une étape qui ne juge rien n'a
    pas à exister), et la lecture prouve toujours que chacune porte le nom."""
    chaine = noyau.lire(CHAINE_QUI_JUGE_AUSSI_POUR_LA_TECHNIQUE)
    etape = chaine.etapes[1]
    assert etape.controles_nommes == "tenue"
    assert [c["id"] for c in etape.controles_propres] == ["la_peinture_est_la"]
    # Les deux formes d'avant ne bougent pas.
    empruntee = noyau.lire(CHAINE_A_TECHNIQUES).etapes[1]
    assert empruntee.controles_nommes == "tenue" and empruntee.controles_propres == ()
    ecrite = noyau.lire({**CHAINE_A_TECHNIQUES, "etapes": [
        CHAINE_A_TECHNIQUES["etapes"][0],
        {"id": "tenue", "verifier": [{"id": "c", "valeur": "$peinture.livrable", "op": "exists"}]},
    ]}).etapes[1]
    assert ecrite.controles_nommes is None and [c["id"] for c in ecrite.controles_propres] == ["c"]

    muette = noyau.lire_technique(TECHNIQUE_MUETTE)
    assert muette.controles["tenue"] == ()
    noyau.verifier_techniques(chaine, {"trait": noyau.lire_technique(TECHNIQUE_TRAIT),
                                       "voile": muette})
    sans = noyau.lire_technique({**TECHNIQUE_TRAIT, "technique": "sans", "controles": {}})
    with pytest.raises(WorkflowMappingError, match="contrôles 'tenue'"):
        noyau.verifier_techniques(chaine, {"sans": sans})

    def _avec(verifier):
        return {**CHAINE_A_TECHNIQUES, "etapes": [CHAINE_A_TECHNIQUES["etapes"][0],
                                                  {"id": "tenue", "verifier": verifier}]}
    liste = [{"id": "c", "valeur": "$peinture.livrable", "op": "exists"}]
    with pytest.raises(WorkflowMappingError, match="liste de contrôles"):
        noyau.lire(_avec([]))                                       # une étape ne juge pas « rien »
    with pytest.raises(WorkflowMappingError, match="controles_de_la_technique"):
        noyau.lire(_avec({"controles": liste, "technique": "$technique"}))
    with pytest.raises(WorkflowMappingError, match="ne va qu'avec une liste"):
        noyau.lire(_avec({"controles": "tenue", "technique": "$technique",
                          "controles_de_la_technique": "tenue"}))
    with pytest.raises(WorkflowMappingError, match="encore"):
        noyau.lire(_avec({"controles": liste, "technique": "$technique",
                          "controles_de_la_technique": "tenue", "encore": 1}))
    with pytest.raises(WorkflowMappingError, match="opérateur"):
        noyau.lire(_avec({"controles": [{"id": "c", "valeur": "$peinture.livrable"}],
                          "technique": "$technique", "controles_de_la_technique": "tenue"}))


def test_le_plan_est_refuse_sur_ce_que_la_technique_exige_avant_la_suite():
    """En marche : l'étape juge la liste de la chaîne PUIS celle de la technique
    choisie, dans cet ordre ; une technique muette laisse la seule liste de la
    chaîne ; une technique exigeante arrête la chaîne à cette étape, en nommant
    son contrôle."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_techniques_plan_"))
    _ecrire(tmp, techniques=(TECHNIQUE_TRAIT, TECHNIQUE_MUETTE, TECHNIQUE_EXIGEANTE))
    (tmp / "chaine-a-techniques.json").write_text(
        json.dumps(CHAINE_QUI_JUGE_AUSSI_POUR_LA_TECHNIQUE, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=0)
    app = create_app(settings)
    app.state.container.orchestrator._backend = BackendQuiLivre(settings.comfy_output_dir)
    with TestClient(app) as client:
        def _controles(job):
            return [(c["id"], c["ok"]) for c in
                    [e for e in job["etapes"] if e["id"] == "tenue"][0]["resultat"]["controles"]]

        trait = _job(client, client.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                                             "technique": "trait"}))
        assert trait["status"] == "succeeded", trait.get("problem")
        assert _controles(trait) == [("la_peinture_est_la", True), ("le_trait_est_nomme", True)]

        voile = _job(client, client.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                                             "technique": "voile"}))
        assert voile["status"] == "succeeded", voile.get("problem")
        assert _controles(voile) == [("la_peinture_est_la", True)]

        refus = _job(client, client.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                                             "technique": "exigeante"}))
        assert refus["status"] == "failed"
        assert refus["problem"]["problem_kind"] == "controle-echoue"
        assert refus["problem"]["etape"] == "tenue"
        assert "cinq_temps_au_moins" in refus["problem"]["detail"]
        assert [(c["id"], c["ok"]) for c in refus["problem"]["controles"]] == [
            ("la_peinture_est_la", True), ("cinq_temps_au_moins", False)]


# -- les valeurs ---------------------------------------------------------------


def test_les_champs_admis_sont_ceux_du_plan_et_de_toutes_les_techniques():
    """Toutes, et pas seulement celle qu'on emploie : un raccourci enregistré
    sous l'une doit pouvoir se rejouer sous l'autre sans être refusé champ par
    champ. Ce qui n'est pas pour la technique choisie est ÉCARTÉ, et dit."""
    chaine = noyau.lire(CHAINE_A_TECHNIQUES)
    techniques = {"trait": noyau.lire_technique(TECHNIQUE_TRAIT),
                  "voile": noyau.lire_technique(TECHNIQUE_VOILE)}
    assert sorted(noyau.champs_admis(chaine, techniques)) == [
        "epaisseur", "fond", "grain", "largeur", "technique"]

    valeurs, ecartes = noyau.valeurs(chaine, {"grain": "gros", "epaisseur": 0.9}, {}, techniques)
    assert ecartes == ["epaisseur"]                       # réglage de l'autre technique
    assert valeurs["grain"] == "gros" and "epaisseur" not in valeurs
    assert valeurs["fond"] == "papier"                    # le défaut de la technique choisie

    sous_voile, ecartes = noyau.valeurs(chaine, {"technique": "voile", "grain": "gros"},
                                        {}, techniques)
    assert ecartes == ["grain"]
    assert sous_voile["fond"] == "voile-clair" and sous_voile["epaisseur"] == 0.5
    # …et un champ que PERSONNE n'expose reste refusé.
    with pytest.raises(Exception, match="profondeur"):
        noyau.valeurs(chaine, {"profondeur": 3}, {}, techniques)


def test_les_options_d_un_champ_commun_sont_celles_de_la_technique_choisie():
    """« fond » n'accepte pas la même chose des deux côtés : c'est la technique
    employée qui dit lesquelles, sinon la liste se figeait sur la première."""
    chaine = noyau.lire(CHAINE_A_TECHNIQUES)
    techniques = {"trait": noyau.lire_technique(TECHNIQUE_TRAIT),
                  "voile": noyau.lire_technique(TECHNIQUE_VOILE)}
    valeurs, _ = noyau.valeurs(chaine, {"technique": "voile", "fond": "voile-sombre"},
                               {}, techniques)
    assert valeurs["fond"] == "voile-sombre"
    with pytest.raises(Exception, match="menu"):
        noyau.valeurs(chaine, {"technique": "voile", "fond": "sepia"}, {}, techniques)
    with pytest.raises(Exception, match="menu"):
        noyau.valeurs(chaine, {"technique": "trait", "fond": "voile-sombre"}, {}, techniques)
    # Les défauts suivent de même.
    assert noyau.defauts(chaine, techniques["voile"])["fond"] == "voile-clair"
    assert noyau.defauts(chaine, techniques["trait"])["fond"] == "papier"


# -- ce que la passerelle publie -----------------------------------------------


def test_c_est_la_technique_qui_se_dit_par_defaut_pas_la_chaine():
    """Antoine, 2026-09-16 au soir : les techniques ne vivent pas dans le
    workflow. Une chaîne qui écrirait « trait » dans le défaut de son champ
    porterait la dépendance qu'on lui refuse : le champ n'a pas de défaut, et
    c'est la technique qui se dit « par_defaut » — elle comble le champ dans
    les valeurs et dans les défauts publiés."""
    sans_defaut = json.loads(json.dumps(CHAINE_A_TECHNIQUES))
    del sans_defaut["expose"]["technique"]["defaut"]
    chaine = noyau.lire(sans_defaut, "chaine-a-techniques")
    voile = noyau.lire_technique({**TECHNIQUE_VOILE, "par_defaut": True})
    trait = noyau.lire_technique(TECHNIQUE_TRAIT)
    techniques = {"trait": trait, "voile": voile}
    noyau.verifier_techniques(chaine, techniques)
    assert voile.par_defaut is True and trait.par_defaut is False
    assert noyau.technique_choisie(chaine, {}, techniques).nom == "voile"
    assert noyau.technique_choisie(chaine, {"technique": "trait"}, techniques).nom == "trait"
    assert noyau.defauts(chaine, voile)["technique"] == "voile"
    valeurs, _ = noyau.valeurs(chaine, {}, {}, techniques)
    assert valeurs["technique"] == "voile" and valeurs["fond"] == "voile-clair"
    # Aucune ne se dit par défaut : la première par son nom, sans surprise.
    muettes = {"trait": trait, "voile": noyau.lire_technique(TECHNIQUE_VOILE)}
    assert noyau.technique_choisie(chaine, {}, muettes).nom == "trait"
    # Deux qui se le disent : refusées à la lecture, en les nommant.
    deux = {"trait": noyau.lire_technique({**TECHNIQUE_TRAIT, "par_defaut": True}), "voile": voile}
    with pytest.raises(WorkflowMappingError, match="se disent par défaut"):
        noyau.verifier_techniques(chaine, deux)
    # Et « par_defaut » est vrai ou faux, rien d'autre.
    with pytest.raises(WorkflowMappingError, match="par_defaut"):
        noyau.lire_technique({**TECHNIQUE_TRAIT, "par_defaut": "oui"})


def test_le_formulaire_s_ouvre_sur_la_technique_qui_se_dit_par_defaut():
    """Le champ de technique n'a pas de défaut écrit dans la chaîne : /io s'ouvre
    sur la technique qui se dit « par_defaut », ses champs avec elle, et la
    vitrine le dit — sans quoi un menu s'ouvrirait sur la première venue."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_techniques_defaut_"))
    _ecrire(tmp, techniques=(TECHNIQUE_TRAIT, {**TECHNIQUE_VOILE, "par_defaut": True}))
    sans_defaut = json.loads(json.dumps(CHAINE_A_TECHNIQUES))
    del sans_defaut["expose"]["technique"]["defaut"]
    (tmp / "chaine-a-techniques.json").write_text(json.dumps(sans_defaut), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=0)
    with TestClient(create_app(settings)) as client:
        io = client.get("/v1/workflows/chaine-a-techniques/io").json()
        champs = {e["param"]: e for e in io["intent_inputs"]}
        assert champs["technique"]["value"] == "voile"
        assert champs["fond"]["value"] == "voile-clair"          # ouvert comme le voile le déclare
        vitrine = client.get("/v1/workflows").json()["workflows"]["chaine-a-techniques"]
        assert [(t["valeur"], t["par_defaut"]) for t in vitrine["techniques"]] ==             [("trait", False), ("voile", True)]
        assert vitrine["defaults"]["technique"] == "voile"
        # Sans nommer de technique, c'est le voile qui peint.
        apercu = client.post("/v1/preview", json={"workflow": "chaine-a-techniques"}).json()
        peinture = [e for e in apercu["etapes"] if e["id"] == "peinture"][0]
        assert peinture["workflow"] == "graphe-au-voile"


def test_le_catalogue_publie_les_techniques_d_un_mode(atelier):
    """Un lanceur qui tiendrait sa propre liste de techniques la verrait vieillir
    au premier fichier ajouté."""
    entree = atelier.get("/v1/workflows").json()["workflows"]["chaine-a-techniques"]
    assert entree["techniques"] == [
        {"valeur": "trait", "libelle": "Au trait", "resume": "un trait sec sur un papier",
         "par_defaut": False},
        {"valeur": "voile", "libelle": "Au voile", "resume": "un voile qui se lève",
         "par_defaut": False}]
    # Les étapes annoncent le graphe de la technique par DÉFAUT, et le rôle.
    peinture = [e for e in entree["etapes"] if e["id"] == "peinture"][0]
    assert peinture["workflow"] == "graphe-au-trait" and peinture["role"] == "peinture"
    # Un graphe ordinaire n'a pas de techniques : la clé reste vide.
    assert atelier.get("/v1/workflows").json()["workflows"]["graphe-au-trait"].get(
        "techniques", []) == []


def test_io_dit_quels_champs_dependent_de_la_technique(atelier):
    """Le lanceur n'écrit rien : un champ porte « selon » (montré quand la
    technique courante est dans la liste) et, quand deux techniques exposent le
    même nom, les options et le défaut de CHACUNE."""
    io = atelier.get("/v1/workflows/chaine-a-techniques/io").json()
    champs = {e["field"]: e for e in io["intent_inputs"]}
    assert [e["field"] for e in io["intent_inputs"]] == [
        "largeur", "technique", "fond", "grain", "epaisseur"]   # les communs d'abord

    assert "selon" not in champs["largeur"] and "selon" not in champs["technique"]
    # La liste des techniques vient des fichiers, habillée de leurs libellés.
    assert champs["technique"]["options"] == ["trait", "voile"]
    assert [c["libelle"] for c in champs["technique"]["choix"]] == ["Au trait", "Au voile"]

    assert champs["grain"]["selon"] == {"champ": "technique", "valeurs": ["trait"]}
    assert "selon_options" not in champs["grain"]           # une seule la connaît
    assert champs["epaisseur"]["selon"] == {"champ": "technique", "valeurs": ["voile"]}

    fond = champs["fond"]
    # Le champ s'ouvre tel que la technique PAR DÉFAUT le déclare — pris chez la
    # première venue, il montrait la valeur de l'une sous la liste de l'autre.
    assert fond["value"] == "papier" and fond["libelle"] == "Fond de départ"
    assert fond["options"] == ["papier", "sepia"]
    assert fond["selon"] == {"champ": "technique", "valeurs": ["trait", "voile"]}
    assert fond["selon_defauts"] == {"trait": "papier", "voile": "voile-clair"}
    assert [c["valeur"] for c in fond["selon_options"]["trait"]] == ["papier", "sepia"]
    assert [c["valeur"] for c in fond["selon_options"]["voile"]] == ["voile-clair",
                                                                     "voile-sombre"]
    # Les libellés déclarés habillent ces listes comme n'importe quel menu.
    assert fond["selon_options"]["trait"][0]["libelle"] == "Papier nu"
    assert fond["selon_options"]["voile"][0]["libelle"] == "Voile clair"


# -- un run, de bout en bout ---------------------------------------------------


def test_chaque_technique_appelle_son_graphe_et_joue_ses_controles(atelier):
    """Le même plan, deux peintures : le graphe appelé, le contrôle joué et le
    journal changent avec la technique — la chaîne, elle, n'a pas bougé."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-techniques"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert atelier.faux.runs == ["graphe-au-trait"]
    assert [e["workflow"] for e in job["etapes"]] == ["graphe-au-trait", None]
    tenue = [e for e in job["etapes"] if e["id"] == "tenue"][0]
    assert [c["id"] for c in tenue["resultat"]["controles"]] == ["le_trait_est_nomme"]
    assert "étape peinture : technique trait → graphe-au-trait" in "\n".join(job["logs"])

    autre = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                                           "technique": "voile"}))
    assert autre["status"] == "succeeded", autre.get("problem")
    assert atelier.faux.runs == ["graphe-au-trait", "graphe-au-voile"]
    tenue = [e for e in autre["etapes"] if e["id"] == "tenue"][0]
    assert [c["id"] for c in tenue["resultat"]["controles"]] == ["le_voile_est_mesure"]
    assert "étape peinture : technique voile → graphe-au-voile" in "\n".join(autre["logs"])


def test_les_entrees_du_role_atteignent_le_noeud(atelier):
    """Les entrées de nœud sont écrites chez la TECHNIQUE (elle seule connaît ses
    numéros) et résolues avec les valeurs de la chaîne comme des siennes."""
    vus = []
    atelier.faux.avant = lambda plan: vus.append(dict(plan.overrides))
    job = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "chaine-a-techniques", "fond": "sepia", "grain": "gros", "largeur": 96}))
    assert job["status"] == "succeeded", job.get("problem")
    assert vus == [{"61.fond": "sepia", "61.grain": "gros", "61.largeur": 96}]


def test_un_reglage_de_l_autre_technique_est_dit_jamais_refuse(atelier):
    """Un raccourci enregistré sous une technique doit se rejouer sous l'autre.
    Ce qui ne s'applique pas est ÉCARTÉ et DIT : muet, il ressemblait trait pour
    trait à un réglage appliqué, et on cherchait ensuite pourquoi rien n'avait
    changé."""
    r = atelier.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                         "epaisseur": 0.9, "grain": "gros"})
    assert r.status_code == 202
    job = _job(atelier, r)
    assert job["status"] == "succeeded", job.get("problem")
    assert job["params"]["grain"] == "gros" and "epaisseur" not in job["params"]
    assert ("non appliqué — n'est pas un réglage de la technique trait : epaisseur"
            in "\n".join(job["logs"]))
    # …et la valeur hors menu de la technique choisie reste refusée.
    refus = atelier.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                             "technique": "voile", "fond": "sepia"})
    assert refus.status_code == 422 and refus.json()["field"] == "fond"


def test_estimer_et_prevoir_resolvent_la_technique(atelier):
    """Avant de dépenser, un appelant doit voir CE QU'IL LANCE : le graphe que la
    technique choisie donne au rôle, pas le rôle."""
    vue = atelier.post("/v1/preview", json={"workflow": "chaine-a-techniques",
                                            "technique": "voile"}).json()
    assert vue["technique"] == "voile"
    peinture = [e for e in vue["etapes"] if e["id"] == "peinture"][0]
    assert peinture["workflow"] == "graphe-au-voile"
    estimation = atelier.post("/v1/estimate", json={"workflow": "chaine-a-techniques",
                                                    "technique": "voile"}).json()
    assert [e["workflow"] for e in estimation["etapes"]] == ["graphe-au-voile"]


def test_un_raccourci_se_lit_avec_la_technique_qu_il_emploie(atelier):
    """Deux techniques exposent « fond » : l'écart d'un raccourci se lit avec
    celle qu'IL emploie. Pris chez n'importe laquelle, « fond » s'affichait sous
    le libellé de l'autre — « Teinte du voile » sur un raccourci au trait."""
    au_trait = atelier.post("/v1/workflows/chaine-a-techniques/raccourcis", json={
        "titre": "Sépia au trait", "valeurs": {"fond": "sepia", "grain": "gros"}})
    assert au_trait.status_code == 201, au_trait.text
    ecarts = {e["champ"]: e for e in au_trait.json()["ecarts"]}
    assert ecarts["fond"]["libelle"] == "Fond de départ"
    assert ecarts["grain"]["libelle"] == "Grain du trait"

    au_voile = atelier.post("/v1/workflows/chaine-a-techniques/raccourcis", json={
        "titre": "Voile sombre",
        "valeurs": {"technique": "voile", "fond": "voile-sombre"}})
    assert au_voile.status_code == 201, au_voile.text
    ecarts = {e["champ"]: e for e in au_voile.json()["ecarts"]}
    assert ecarts["fond"]["libelle"] == "Teinte du voile"
    # « voile-clair » serait le défaut SOUS LE VOILE : jugé contre l'encre, il
    # aurait figuré comme un écart de plus.
    garde = atelier.post("/v1/workflows/chaine-a-techniques/raccourcis", json={
        "titre": "Voile ordinaire", "valeurs": {"technique": "voile", "fond": "voile-clair"}})
    assert [e["champ"] for e in garde.json()["ecarts"]] == ["technique"]


# -- le rendu par tranches d'une étape à rôle ----------------------------------


@pytest.fixture()
def banc_tranche():
    """Un plan dont le rôle est tenu par un graphe qui déborde de la mémoire."""
    pile = contextlib.ExitStack()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_tech_tranches_"))
    (tmp / "video-tranchable.json").write_text(json.dumps(GRAPHE_TRANCHABLE), encoding="utf-8")
    (tmp / "techniques").mkdir()
    (tmp / "techniques" / "lourde.json").write_text(json.dumps({
        "version": 1, "technique": "lourde", "libelle": "Lourde",
        "resume": "une peinture qui ne tient pas en mémoire",
        "roles": {"peinture": {"workflow": "video-tranchable", "inputs": {}}},
        "controles": {"tenue": [{"id": "quelque_chose_est_mesure",
                                 "valeur": "$peinture.recit.encre_dans_le_cadre_min",
                                 "op": "gte", "attendu": 0}]},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "chaine-lourde.json").write_text(json.dumps({
        "version": 1, "chaine": "chaine-lourde", "resume": "un rôle qu'il faut trancher",
        "expose": {
            "secondes": {"type": "FLOAT", "defaut": 2, "min": 1, "max": 4, "libelle": "Durée"},
            "technique": {"type": "COMBO", "defaut": "lourde",
                          "options_depuis": {"techniques": True}, "libelle": "Technique"},
        },
        "etapes": [
            {"id": "peinture", "rendre": {"role": "peinture", "technique": "$technique",
                                          "duration_s": "$secondes", "width": LARGEUR,
                                          "height": HAUTEUR, "fps": CADENCE}},
            {"id": "tenue", "verifier": {"technique": "$technique", "controles": "tenue"}},
        ],
        "livrable": "$peinture.livrable",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "chaine-lourde": {"kind": "video", "chaine": str(tmp / "chaine-lourde.json"),
                              "titre": "Chaîne lourde", "categorie": "essais", "ordre": 1},
            "video-tranchable": {
                "kind": "video", "workflow": str(tmp / "video-tranchable.json"),
                "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
        },
    }, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=BUDGET_POUR_TROIS)
    app = create_app(settings)
    faux = BackendQuiTranche(settings.comfy_output_dir)
    app.state.container.orchestrator._backend = faux
    client = pile.enter_context(TestClient(app))
    client.faux = faux
    yield client
    pile.close()


@SANS_FFMPEG
def test_une_etape_a_role_se_tranche_comme_les_autres(banc_tranche):
    """Le découpage ne voit qu'un graphe résolu : que ce soit la chaîne ou la
    technique qui l'ait nommé ne change rien."""
    job = _job(banc_tranche, banc_tranche.post("/v1/render",
                                               json={"workflow": "chaine-lourde"}))
    assert job["status"] == "succeeded", job.get("problem")
    peinture = [e for e in job["etapes"] if e["id"] == "peinture"][0]
    assert peinture["tranches"] == 3 and len(peinture["job_ids"]) == 3
    assert banc_tranche.faux.tranches_recues == [(0, 3), (1, 3), (2, 3)]
    journal = "\n".join(job["logs"])
    assert "étape peinture : technique lourde → video-tranchable" in journal
    assert "3 tranches recollées" in journal


# -- les copies de référence ---------------------------------------------------


def test_les_copies_de_reference_des_techniques_suivent_les_donnees():
    """Une technique modifiée dans `_data/` sans sa copie de référence laisserait
    le savoir sur la machine et le paquet sur l'ancienne version : à la prochaine
    installation, c'est l'ancienne qui repartirait."""
    racine = pathlib.Path(__file__).resolve().parents[1]
    donnees = racine / "_data" / "techniques"
    if not donnees.is_dir():
        pytest.skip("pas de _data/techniques sur ce poste : rien à comparer")
    reference = racine / "comfyui_bridge" / "adapter" / "resources" / "techniques-exemples"
    ecarts = []
    for fichier in sorted(donnees.glob("*.json")):
        jumeau = reference / fichier.name
        if not jumeau.exists():
            ecarts.append(f"{fichier.name} : aucune copie de référence")
            continue
        if json.loads(fichier.read_text(encoding="utf-8")) != \
                json.loads(jumeau.read_text(encoding="utf-8")):
            ecarts.append(f"{fichier.name} : la copie de référence diverge des données")
    assert not ecarts, " ; ".join(ecarts)


# -- la réconciliation concrète d'un champ : catégorie, aide, filtre ----------


def test_chaque_champ_publie_sa_categorie_et_son_aide_et_un_menu_se_filtre():
    """Antoine, 2026-09-17 : « chacun porte une réconciliation concrète, en
    standardisant par catégorie ». Le champ la porte chez son propriétaire (la
    chaîne, la technique) ; /io la publie telle quelle, GET /v1/workflows publie
    le vocabulaire déclaré une fois ; et un champ qui tire sa liste d'un menu
    peut exiger ce que la ligne doit porter — la liste ne montre plus de
    fantômes. L'aide du champ l'emporte sur celle de l'entrée du mode."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_categories_"))
    chaine = json.loads(json.dumps(CHAINE_A_TECHNIQUES))
    chaine["expose"]["largeur"].update({"categorie": "format", "aide": "Le petit côté."})
    chaine["expose"]["technique"].update({"categorie": "technique"})
    chaine["expose"]["structure"] = {
        "type": "COMBO", "defaut": "avec-accroche", "categorie": "recit",
        "options_depuis": {"menu": "structure", "requiert": {"temps": "hook"}},
        "libelle": "Structure du récit", "aide": "Seules celles qui ont une accroche."}
    trait = json.loads(json.dumps(TECHNIQUE_TRAIT))
    trait["expose"]["grain"].update({"categorie": "matiere", "aide": "Le grain du trait."})
    _ecrire(tmp, techniques=(trait, TECHNIQUE_VOILE))
    (tmp / "chaine-a-techniques.json").write_text(json.dumps(chaine, ensure_ascii=False),
                                                  encoding="utf-8")
    (tmp / "structures.json").write_text(json.dumps({"styles": {
        "avec-accroche": {"libelle": "Avec accroche", "temps": [{"nom": "hook"}, {"nom": "corps"}]},
        "sans-accroche": {"libelle": "Sans accroche", "temps": [{"nom": "continu"}]}}},
        ensure_ascii=False), encoding="utf-8")
    reconciliation = json.loads((tmp / "reconciliation.local.json").read_text(encoding="utf-8"))
    reconciliation["categories_de_champs"] = [
        {"valeur": "recit", "titre": "Récit"}, {"valeur": "format", "titre": "Format"},
        {"valeur": "technique", "titre": "Technique"}, {"valeur": "matiere", "titre": "Matière"}]
    reconciliation["menus"]["structure"] = {"libelle": "Structure", "source_fichier": {
        "chemin": str(tmp / "structures.json"), "table": "styles", "libelle": "libelle"}}
    reconciliation["workflows"]["chaine-a-techniques"]["aides"] = {"largeur": "L'aide de l'entrée, moins proche."}
    (tmp / "reconciliation.local.json").write_text(json.dumps(reconciliation, ensure_ascii=False),
                                                   encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=0)
    with TestClient(create_app(settings)) as client:
        vitrine = client.get("/v1/workflows").json()
        assert [c["valeur"] for c in vitrine["categories_de_champs"]] == [
            "recit", "format", "technique", "matiere"]
        assert vitrine["categories_de_champs"][3]["titre"] == "Matière"
        champs = {e["field"]: e for e in client.get("/v1/workflows/chaine-a-techniques/io").json()
                  ["intent_inputs"]}
        assert champs["largeur"]["categorie"] == "format"
        assert champs["largeur"]["aide"] == "Le petit côté."          # la sienne, pas celle de l'entrée
        assert champs["technique"]["categorie"] == "technique"
        assert champs["grain"]["categorie"] == "matiere" and champs["grain"]["aide"] == "Le grain du trait."
        assert "categorie" not in champs["fond"]                       # rien d'inventé
        assert champs["structure"]["options"] == ["avec-accroche"]      # le fantôme est parti
        assert champs["structure"]["categorie"] == "recit"
        # Une valeur filtrée hors de la liste est refusée comme toute valeur hors menu.
        refus = client.post("/v1/render", json={"workflow": "chaine-a-techniques",
                                                "structure": "sans-accroche"})
        assert refus.status_code == 422 and refus.json()["field"] == "structure"
    shutil.rmtree(tmp, ignore_errors=True)


def test_le_vocabulaire_des_categories_se_verifie_a_la_lecture(tmp_path):
    """Une valeur en double, une entrée sans valeur, une liste qui n'en est pas
    une : refusées en le disant, avant le premier formulaire."""
    from comfyui_bridge.adapter.catalog import _categories_de_champs
    assert _categories_de_champs(None, tmp_path) == []
    assert _categories_de_champs([{"valeur": "recit"}], tmp_path) == [
        {"valeur": "recit", "titre": "recit", "resume": "", "repliee": False}]
    assert _categories_de_champs([{"valeur": "fin", "titre": "Fin", "repliee": True}], tmp_path)[0]["repliee"] is True
    with pytest.raises(WorkflowMappingError, match="en double"):
        _categories_de_champs([{"valeur": "recit"}, {"valeur": "recit"}], tmp_path)
    with pytest.raises(WorkflowMappingError, match="valeur"):
        _categories_de_champs([{"titre": "Sans valeur"}], tmp_path)
    with pytest.raises(WorkflowMappingError, match="liste"):
        _categories_de_champs({"recit": "Récit"}, tmp_path)
    # Et un champ qui pose « requiert » sans menu, ou pas en objet, est refusé.
    with pytest.raises(WorkflowMappingError, match="requiert"):
        noyau.lire({"version": 1, "chaine": "c", "expose": {
            "s": {"type": "COMBO", "defaut": "a", "options": ["a"], "requiert": 1,
                  "options_depuis": {"menu": "m", "requiert": "hook"}}},
            "etapes": [{"id": "u", "rendre": {"workflow": "w"}}], "livrable": "$u.livrable"})
