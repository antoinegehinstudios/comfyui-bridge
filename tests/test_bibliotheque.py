"""La bibliothèque de blocs réutilisables : inclusion et vérification des ports."""

import io
import json

import pytest

from comfyui_bridge.adapter import bibliotheque
from comfyui_bridge.core.errors import WorkflowMappingError

COMMUN = {"fragment": "commun", "sorties": {"modele": "1", "vae": "2"},
          "contenu": {"1": {"class_type": "Charger", "inputs": {}},
                      "2": {"class_type": "Vae", "inputs": {}}}}

AMORCE = {"fragment": "amorce", "sorties": {"derniere_image": "3"},
          "contenu": {"3": {"class_type": "Decode", "inputs": {}}}}

BLOC = {
    "bloc": "maillon",
    "fragment": "segment",
    "besoin": {"modele": "MODEL", "vae": "VAE"},
    "attend": {"derniere_image": "IMAGE"},
    "sorties": {"derniere_image": "9"},
    "contenu": {"9": {"class_type": "Decode",
                      "inputs": {"src": ["$precedent.derniere_image", 0]}}},
}


def test_utiliser_remplace_le_bloc_par_son_contenu():
    montage = [COMMUN, AMORCE, {"utiliser": "maillon"}]
    rendu = bibliotheque.resoudre(montage, {"maillon": BLOC})
    assert rendu[2]["fragment"] == "segment"
    assert rendu[2]["contenu"] == BLOC["contenu"]
    assert rendu[2]["sorties"] == BLOC["sorties"]


def test_utiliser_dans_une_boucle_voit_ce_qui_precede_la_boucle():
    # Le bloc repete se raccorde a l'amorce : la verification de ses ports doit
    # le savoir, sinon elle refuserait un montage pourtant complet.
    montage = [COMMUN, AMORCE,
               {"pour": {"jusqu_a": "n", "chaque": 1}, "faire": [{"utiliser": "maillon"}]}]
    rendu = bibliotheque.resoudre(montage, {"maillon": BLOC})
    assert rendu[2]["faire"][0]["fragment"] == "segment"


def test_un_port_non_servi_est_refuse_avec_son_nom():
    sans_vae = {"fragment": "commun", "sorties": {"modele": "1"},
                "contenu": {"1": {"class_type": "Charger", "inputs": {}}}}
    with pytest.raises(WorkflowMappingError) as e:
        bibliotheque.resoudre([sans_vae, AMORCE, {"utiliser": "maillon"}], {"maillon": BLOC})
    assert "vae" in str(e.value)


def test_ce_qui_precede_doit_offrir_ce_que_le_bloc_attend():
    muet = {"fragment": "amorce", "contenu": {"3": {"class_type": "Decode", "inputs": {}}}}
    with pytest.raises(WorkflowMappingError) as e:
        bibliotheque.resoudre([COMMUN, muet, {"utiliser": "maillon"}], {"maillon": BLOC})
    assert "derniere_image" in str(e.value)


def test_un_bloc_inconnu_dit_ceux_qui_existent():
    with pytest.raises(WorkflowMappingError) as e:
        bibliotheque.resoudre([COMMUN, {"utiliser": "absent"}], {"maillon": BLOC})
    assert "absent" in str(e.value)


def test_une_inclusion_peut_renommer_le_fragment_sans_toucher_au_bloc_partage():
    montage = [COMMUN, AMORCE, {"utiliser": "maillon", "fragment": "autre"}]
    rendu = bibliotheque.resoudre(montage, {"maillon": BLOC})
    assert rendu[2]["fragment"] == "autre"
    assert BLOC["fragment"] == "segment"           # le bloc partagé n'a pas bougé


def test_apres_un_si_le_bloc_se_raccorde_au_dernier_fragment_de_chaque_branche():
    """« Au premier tour l'amorce, ensuite le segment » : le bloc posé après le
    « si » verra l'un OU l'autre — chacun doit offrir ce qu'il attend, et une
    branche muette se dit par son nom."""
    segment = {"fragment": "segment", "sorties": {"derniere_image": "5"},
               "contenu": {"5": {"class_type": "Decode", "inputs": {}}}}
    montage = [COMMUN, {"pour": {"jusqu_a": "n", "chaque": 1}, "faire": [
        {"si": {"parametre": "tour", "op": "eq", "valeur": 0}, "alors": [AMORCE], "sinon": [segment]},
        {"utiliser": "maillon"}]}]
    rendu = bibliotheque.resoudre(montage, {"maillon": BLOC})
    assert rendu[1]["faire"][1]["fragment"] == "segment"
    # Hors boucle (où le bloc ne peut pas se suivre lui-même), une branche
    # muette est refusée, et nommée.
    muet = {"fragment": "segment", "contenu": {"5": {"class_type": "Decode", "inputs": {}}}}
    hors_boucle = [COMMUN, {"si": {"parametre": "x", "op": "eq", "valeur": 0},
                            "alors": [AMORCE], "sinon": [muet]}, {"utiliser": "maillon"}]
    with pytest.raises(WorkflowMappingError) as e:
        bibliotheque.resoudre(hors_boucle, {"maillon": BLOC})
    assert "'segment'" in str(e.value) and "derniere_image" in str(e.value)


def test_les_blocs_livres_avec_le_paquet_sont_trouvables():
    # Une machine neuve doit avoir de quoi monter une chaîne sans rien copier.
    assert bibliotheque.charger() != {}


def test_un_bloc_de_la_machine_prime_sur_celui_du_paquet(tmp_path):
    livres = bibliotheque.charger()
    nom = sorted(livres)[0]
    dossier = tmp_path / bibliotheque.DOSSIER
    dossier.mkdir()
    io.open(dossier / "x.json", "w", encoding="utf-8").write(
        json.dumps({"bloc": nom, "contenu": {"1": {"class_type": "AMoi", "inputs": {}}}}))
    a_moi = bibliotheque.charger(tmp_path)[nom]
    assert a_moi["contenu"]["1"]["class_type"] == "AMoi"
