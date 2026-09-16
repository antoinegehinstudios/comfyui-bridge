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


def test_le_catalogue_publie_les_techniques_d_un_mode(atelier):
    """Un lanceur qui tiendrait sa propre liste de techniques la verrait vieillir
    au premier fichier ajouté."""
    entree = atelier.get("/v1/workflows").json()["workflows"]["chaine-a-techniques"]
    assert entree["techniques"] == [
        {"valeur": "trait", "libelle": "Au trait", "resume": "un trait sec sur un papier"},
        {"valeur": "voile", "libelle": "Au voile", "resume": "un voile qui se lève"}]
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
