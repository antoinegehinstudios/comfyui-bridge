"""Un moteur MORT ne se confond pas avec un moteur occupé.

Mesuré le 2026-09-22 : le moteur s'est arrêté en cours de run, silencieusement
(pas une ligne dans son journal — une faute du pilote graphique, déjà vue). La
passerelle a attendu son budget ENTIER (une heure) avant de conclure, puis les
productions suivantes n'avaient plus de moteur du tout. Or la différence se lit
sur la socket : un moteur saturé ne répond pas, un moteur arrêté REFUSE la
connexion.
"""

import asyncio
import socket
import urllib.error

import pytest

from comfyui_bridge.adapter.comfy_http import MOTEUR_ABSENT_S, _connexion_refusee  # noqa: E402


def test_refusee_ou_muette_ne_sont_pas_la_meme_chose():
    refus = ConnectionRefusedError(10061, "l'ordinateur cible l'a expressément refusée")
    refus.winerror = 10061
    assert _connexion_refusee(urllib.error.URLError(refus)) is True
    assert _connexion_refusee(refus) is True
    # …et tout le reste est un silence, pas une mort : un moteur qui calcule ne
    # répond pas, et le run doit continuer d'être suivi.
    assert _connexion_refusee(urllib.error.URLError(socket.timeout("trop long"))) is False
    assert _connexion_refusee(TimeoutError("trop long")) is False
    assert _connexion_refusee(ValueError("autre chose")) is False
    assert MOTEUR_ABSENT_S >= 30.0            # un redémarrage volontaire ne doit pas conclure


class _Moteur:
    """Un profil de moteur, géré ou non."""

    def __init__(self, manage: bool) -> None:
        self.manage = manage
        self.name = "essai"


class _Conteneur:
    def __init__(self, manage: bool, tmp_path) -> None:
        self.engine = _Moteur(manage)
        self.engine_state: dict = {}

        class _S:
            comfyui_base_url = "http://127.0.0.1:9"
            hermes_db = tmp_path / "h.sqlite3"
        self.settings = _S()


def _veiller_une_fois(monkeypatch, conteneur, vivant, releve):
    """Dérouler le veilleur jusqu'à ce qu'il ait eu ses trois regards."""
    from comfyui_bridge.api import veille_moteur as V

    monkeypatch.setattr(V, "PAS_S", 0.01)
    import comfyui_bridge.adapter.engines as E
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: vivant())
    monkeypatch.setattr(E, "ensure_engine", releve)

    async def scenario():
        garde = asyncio.create_task(V.veiller(conteneur, pas=0.01, coups=3))
        await asyncio.sleep(0.3)
        garde.cancel()
    asyncio.run(scenario())


def test_un_moteur_qui_repond_n_est_jamais_releve(monkeypatch, tmp_path):
    """Relever un moteur vivant tuerait le run qui tourne (mesuré le
    2026-09-20 : deux productions d'Antoine perdues ainsi)."""
    releves = []
    _veiller_une_fois(monkeypatch, _Conteneur(True, tmp_path), lambda: True,
                      lambda *a, **k: releves.append(a) or {"state": "started"})
    assert releves == []


def test_un_moteur_mort_et_a_nous_est_releve(monkeypatch, tmp_path):
    releves = []

    def relever(profil, delai, dossier):
        releves.append(profil.name)
        return {"state": "started", "started": True}

    conteneur = _Conteneur(True, tmp_path)
    _veiller_une_fois(monkeypatch, conteneur, lambda: False, relever)
    assert releves and releves[0] == "essai"
    assert conteneur.engine_state.get("state") == "started"


def test_un_moteur_mort_qui_n_est_pas_a_nous_n_est_pas_touche(monkeypatch, tmp_path):
    """Un moteur lancé par quelqu'un d'autre ne s'attrape pas : on le dit, on
    ne le relance pas à sa place."""
    releves = []
    conteneur = _Conteneur(False, tmp_path)
    _veiller_une_fois(monkeypatch, conteneur, lambda: False,
                      lambda *a, **k: releves.append(a) or {"state": "started"})
    assert releves == [] and conteneur.engine_state == {}


def test_un_releve_qui_echoue_ne_tue_pas_le_service(monkeypatch, tmp_path):
    def relever(*_a, **_k):
        raise RuntimeError("le moteur n'a pas voulu")

    conteneur = _Conteneur(True, tmp_path)
    _veiller_une_fois(monkeypatch, conteneur, lambda: False, relever)
    assert conteneur.engine_state == {}          # rien de faux n'est écrit


@pytest.mark.parametrize("vivant", [True, False])
def test_le_veilleur_ne_leve_jamais(monkeypatch, tmp_path, vivant):
    """Quoi qu'il regarde, il ne doit pas casser la passerelle sous lui."""
    _veiller_une_fois(monkeypatch, _Conteneur(True, tmp_path), lambda: vivant,
                      lambda *a, **k: {"state": "started"})
