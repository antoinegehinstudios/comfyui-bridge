"""Le GABARIT d'un cas de création : manifesté par la chaîne, vérifié à la lecture.

Antoine, 2026-09-24 : « standardise par un template un cas de création, en
manifestant le template à suivre ». Ce que ces tests tiennent : le gabarit
« creation » se lit et dit ses étapes ; les deux chaînes de référence le
manifestent et le suivent ; une chaîne qui le manifeste sans le suivre est
refusée en nommant chaque écart (une étape manquante, déplacée, d'un autre
genre, un rendu qui nomme un graphe au lieu d'un rôle, un champ imposé non
déclaré, un constat requis absent, un livrable autre) ; une chaîne sans gabarit
n'est pas jugée ; un gabarit inconnu refuse.
"""

import copy
import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core import gabarit
from comfyui_bridge.core import reconciliant
from comfyui_bridge.core.errors import WorkflowMappingError

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"


@pytest.fixture(scope="module")
def creation():
    return gabarit.lire("creation")


@pytest.fixture(scope="module")
def standard():
    # la chaîne telle qu'elle s'exécute : son réconciliant « charte » déplié
    return reconciliant.deplier(json.loads((EXEMPLES / "image-creation.json").read_text(encoding="utf-8")))


def test_le_gabarit_creation_se_lit_et_dit_son_plan(creation):
    assert creation["gabarit"] == "creation"
    assert [e["id"] for e in creation["etapes"]] == ["contrainte", "direction", "rendu", "livraison", "conformite",
                                                       "constat", "constat_de_la_charte", "controle"]
    assert creation["premier_champ"] == "charte" and creation["imposes_par"]["au_moins"] == ["palette"]
    assert creation["livrable"] == "$livraison.livrable"
    assert (EXEMPLES / "GABARIT-creation.md").is_file()


def test_les_deux_chaines_de_reference_le_manifestent_et_le_suivent(creation):
    for nom in ("image-creation", "image-visuel-social"):
        brut = reconciliant.deplier(json.loads((EXEMPLES / f"{nom}.json").read_text(encoding="utf-8")))
        chaine = noyau.lire(brut, nom)
        assert chaine.gabarit == "creation", nom
        assert gabarit.ecarts(chaine, creation) == [], nom
        gabarit.verifier(chaine, creation)


def _lire(brut):
    return noyau.lire(brut, "essai")


def test_une_chaine_qui_s_ecarte_est_refusee_en_nommant_chaque_ecart(creation, standard):
    # une étape déplacée (le contrôle avant le constat de la charte : les renvois tiennent, l'ordre non)
    faux = copy.deepcopy(standard)
    faux["etapes"] = faux["etapes"][:-2] + [faux["etapes"][-1], faux["etapes"][-2]]
    with pytest.raises(WorkflowMappingError, match="vient avant"):
        gabarit.verifier(_lire(faux), creation)
    # une étape manquante
    faux = copy.deepcopy(standard)
    faux["etapes"] = [e for e in faux["etapes"] if e["id"] != "conformite"]
    charte_ = next(e for e in faux["etapes"] if e["id"] == "constat_de_la_charte")
    charte_["constater"] = [c for c in charte_["constater"] if "conformite" not in json.dumps(c)]
    ecarts = gabarit.ecarts(_lire(faux), creation)
    assert any("'conformite'" in e and "manque" in e for e in ecarts), ecarts
    # un rendu qui nomme un graphe au lieu d'un rôle
    faux = copy.deepcopy(standard)
    rendu = next(e for e in faux["etapes"] if e["id"] == "rendu")
    rendu["rendre"] = {"workflow": "image-z-image-turbo", "seed": "$seed", "inputs": {"27.text": "$direction.recit.prompt"}}
    faux["expose"].pop("technique")
    charte_ = next(e for e in faux["etapes"] if e["id"] == "constat_de_la_charte")
    charte_["constater"] = [c for c in charte_["constater"] if "$technique" not in json.dumps(c)]
    ecarts = gabarit.ecarts(_lire(faux), creation)
    assert any("rôle 'image'" in e for e in ecarts) and any("champ qui choisit la technique" in e for e in ecarts), ecarts
    # un champ imposé non déclaré, et la charte qui n'est plus en tête
    faux = copy.deepcopy(standard)
    faux["expose"]["palette"].pop("impose_par")
    expose = faux["expose"]
    faux["expose"] = {k: expose[k] for k in list(expose)[1:]} | {"charte": expose["charte"]}
    ecarts = gabarit.ecarts(_lire(faux), creation)
    assert any("impose_par" in e for e in ecarts) and any("premier champ" in e for e in ecarts), ecarts
    # un constat requis absent, un livrable autre, une étape en trop d'un genre non libre
    faux = copy.deepcopy(standard)
    constat = next(e for e in faux["etapes"] if e["id"] == "constat_de_la_charte")
    constat["constater"] = [c for c in constat["constater"] if c["id"] != "les_interdits_de_la_charte_pesent"]
    faux["livrable"] = "$rendu.livrable"
    faux["etapes"].insert(3, {"id": "retouche", "rendre": {"workflow": "g", "seed": "$seed"}})
    ecarts = gabarit.ecarts(_lire(faux), creation)
    assert any("les_interdits_de_la_charte_pesent" in e for e in ecarts), ecarts
    assert any("livrable" in e for e in ecarts) and any("'retouche'" in e for e in ecarts), ecarts
    # une étape sous « charte » qui aurait lieu toujours
    faux = copy.deepcopy(standard)
    next(e for e in faux["etapes"] if e["id"] == "conformite").pop("quand")
    assert any("sous le champ 'charte'" in e for e in gabarit.ecarts(_lire(faux), creation))
    # un constat de plus (genre libre) est admis
    ok = copy.deepcopy(standard)
    ok["etapes"].insert(6, {"id": "constat_du_message", "constater": [
        {"id": "x", "valeur": "$direction.recit.zone_de_texte", "op": "exists", "aide": "a"}]})
    assert gabarit.ecarts(_lire(ok), creation) == []


def test_sans_gabarit_rien_n_est_juge_et_un_gabarit_inconnu_refuse(standard):
    sans = copy.deepcopy(standard)
    sans.pop("gabarit")
    sans["etapes"] = sans["etapes"][:-2] + [sans["etapes"][-1], sans["etapes"][-2]]   # déplacée : hors gabarit, nul ne s'en plaint
    assert _lire(sans).gabarit is None
    with pytest.raises(WorkflowMappingError, match="aucun fichier"):
        gabarit.lire("gabarit-qui-n-existe-pas")


def _catalogue(tmp_path, chaine, socle=None):
    (tmp_path / "chaine.json").write_text(json.dumps(chaine, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "reconciliation.local.json").write_text(json.dumps({
        **({"socle": socle} if socle else {}),
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {"essai": {"kind": "image", "chaine": str(tmp_path / "chaine.json"), "titre": "Essai",
                                "categorie": "essais", "ordre": 1}}}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "reconciliation.local.json"


def test_le_catalogue_refuse_au_chargement_une_chaine_qui_manifeste_un_gabarit_sans_le_suivre(tmp_path):
    from comfyui_bridge.adapter import catalog as cat
    # la SOURCE (le réconciliant « charte » s'y déplie) : le contrôle posé avant le dernier constat
    faux = json.loads((EXEMPLES / "image-creation.json").read_text(encoding="utf-8"))
    faux["etapes"] = faux["etapes"][:-2] + [faux["etapes"][-1], faux["etapes"][-2]]
    with pytest.raises(WorkflowMappingError, match="gabarit 'creation'"):
        cat.load_catalog(_catalogue(tmp_path, faux), data_dir=tmp_path)


def test_un_catalogue_qui_declare_son_socle_ne_publie_aucune_chaine_qui_ne_le_suit_pas(tmp_path):
    """Antoine, 2026-09-25 : « chaque nouveau flux de création sur maestro suit la même structure à la base ». Le
    catalogue déclare son socle ; une chaîne publiée sans gabarit — ou d'un gabarit qui ne l'étend pas — est
    refusée, en le disant ; sans catégorie (une étape interne), elle n'est pas publiée et n'est pas jugée."""
    from comfyui_bridge.adapter import catalog as cat
    sans = {"version": 1, "chaine": "essai", "resume": "r",
            "expose": {"prompt": {"type": "STRING", "defaut": "", "libelle": "Consigne", "categorie": "sujet", "aide": "a"}},
            "etapes": [{"id": "rendu", "rendre": {"workflow": "sd15-txt2img", "prompt": "$prompt"}},
                       {"id": "controle", "verifier": [{"id": "livrable_pese", "valeur": "$rendu.mesure.bytes",
                                                        "op": "gte", "attendu": 1, "aide": "a"}]}],
            "livrable": "$rendu.livrable"}
    with pytest.raises(WorkflowMappingError, match="sans suivre le socle 'socle'"):
        cat.load_catalog(_catalogue(tmp_path, sans, socle="socle"), data_dir=tmp_path)
    avec = {**sans, "gabarit": "socle"}
    c = cat.load_catalog(_catalogue(tmp_path, avec, socle="socle"), data_dir=tmp_path)
    assert c.socle == "socle" and c.get_spec("essai").gabarit == "socle"
    # le socle juge : un contrôle final qui ne pèse pas le livrable, un champ muet
    faux = copy.deepcopy(avec)
    faux["etapes"][-1]["verifier"][0]["id"] = "autre_chose"
    faux["expose"]["prompt"].pop("aide")
    ecarts = gabarit.ecarts(noyau.lire(faux, "essai"), gabarit.lire("socle"))
    assert any("livrable_pese" in e for e in ecarts) and any("prompt (aide)" in e for e in ecarts), ecarts


def test_une_chaine_qui_lit_une_source_sans_son_reconciliant_est_refusee(tmp_path, standard):
    """Une chaîne DÉJÀ dépliée, écrite telle quelle, lit Héraldiste en direct (son graphe, son menu, le récit de
    son étape) : refusée au chargement — une source ne se lit que par son réconciliant, sinon une nouveauté se
    recâble chaîne par chaîne."""
    from comfyui_bridge.adapter import catalog as cat
    with pytest.raises(WorkflowMappingError, match="lit Héraldiste en direct"):
        cat.load_catalog(_catalogue(tmp_path, standard), data_dir=tmp_path)
