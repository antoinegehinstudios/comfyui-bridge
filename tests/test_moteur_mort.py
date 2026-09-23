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
import types
import urllib.error

import pytest

from comfyui_bridge.adapter import comfy_http as C  # noqa: E402
from comfyui_bridge.adapter.comfy_http import MOTEUR_ABSENT_S, _connexion_refusee  # noqa: E402
from comfyui_bridge.core.errors import BackendExecutionError  # noqa: E402


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


# --- La garde « moteur absent » mesure-t-elle du TEMPS, ou des tours ? -------
#
# Prix MESURÉ d'un sondage sur un port FERMÉ sous Windows : le dépôt le savait
# déjà ailleurs (scripts/lancer-console.ps1 : « Sur un port ferme,
# Invoke-WebRequest met 2 s a echouer »). C'est lui qui fait qu'un tour de
# boucle ne vaut pas le sommeil qu'il annonce.
COUT_D_UN_SONDAGE_REFUSE_S = 2.05


class _Horloge:
    """Une horloge de laboratoire : elle n'avance que si on la fait avancer.

    Le sommeil ne dort pas — le test doit rester instantané — mais le temps,
    lui, passe : c'est précisément ce que la garde doit lire.
    """

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, secondes: float) -> None:
        self.t += secondes


def _un_refus():
    faute = ConnectionRefusedError(10061, "aucun processus n'écoute sur ce port")
    faute.winerror = 10061
    return urllib.error.URLError(faute)


def _attendre(monkeypatch, sonde, budget_s=3600.0, pas_s=1.0):
    """Dérouler l'attente des sorties sur une horloge à nous — ni réseau, ni moteur."""
    horloge = _Horloge()
    monkeypatch.setattr(C, "time", horloge)        # le module, pas l'horloge du système

    class _Reglages:
        comfyui_total_timeout_s = budget_s
        comfyui_poll_interval_s = pas_s

    faux = types.SimpleNamespace(_settings=_Reglages(),
                                 _get_json=lambda chemin, delai: sonde(horloge),
                                 _client=None)
    return horloge, faux


def test_la_garde_moteur_absent_compte_des_secondes_pas_des_tours(monkeypatch):
    """Mesuré : le job 8c099b8d a annoncé « connexion refusée depuis 60 s »
    après 191,4 s réelles (19:34:59.763 → 19:38:11.190). La garde comptait ses
    tours en les croyant d'une seconde, quand un refus en coûte trois."""
    def refuse(horloge):
        horloge.t += COUT_D_UN_SONDAGE_REFUSE_S
        raise _un_refus()

    horloge, faux = _attendre(monkeypatch, refuse)
    with pytest.raises(BackendExecutionError) as leve:
        C.ComfyUIHttpBackend._await_outputs(faux, "8c099b8d", 1.0)
    assert "n'écoute plus" in leve.value.detail
    # Le temps réellement passé avant de conclure : une minute, pas trois.
    assert MOTEUR_ABSENT_S <= horloge.t < 1.5 * MOTEUR_ABSENT_S     # avant : 191,4 s
    # …et ce qu'elle ANNONCE est ce qui s'est écoulé : à un tour près (le premier refus
    # n'est pas encore du temps de refus) et à l'entier inférieur.
    annonce = int(leve.value.detail.split("depuis ")[1].split(" s")[0])
    un_tour = 1.0 + COUT_D_UN_SONDAGE_REFUSE_S
    assert horloge.t - un_tour - 1 <= annonce <= horloge.t


def test_un_sondage_qui_aboutit_remet_le_refus_a_zero(monkeypatch):
    """Deux demi-minutes de refus séparées par une réponse ne font pas une
    minute d'absence : entre les deux, le moteur était là."""
    tours = {"n": 0}

    def refuse_sauf_une_fois(horloge):
        tours["n"] += 1
        if tours["n"] == 15:                       # le moteur répond, une fois
            return {}
        horloge.t += COUT_D_UN_SONDAGE_REFUSE_S
        raise _un_refus()

    horloge, faux = _attendre(monkeypatch, refuse_sauf_une_fois, budget_s=80.0)
    with pytest.raises(BackendExecutionError) as leve:
        C.ComfyUIHttpBackend._await_outputs(faux, "p", 1.0)
    assert "n'écoute plus" not in leve.value.detail       # budget épuisé, pas moteur absent
    assert "timed out" in leve.value.detail


# --- « Muet » ou « refusé » : le message final ne doit pas confondre ---------


def test_le_message_d_expiration_dit_un_port_ferme_et_non_un_silence(monkeypatch):
    """Les trois échecs du 2026-09-22 (28c3f739, b4ce44e1, a108ec26) avaient le
    port FERMÉ toute l'heure. Les appeler « muets » a envoyé l'enquête chercher
    une saturation qui n'a jamais existé."""
    def refuse(horloge):
        horloge.t += COUT_D_UN_SONDAGE_REFUSE_S
        raise _un_refus()

    # Budget court : la garde « moteur absent » ne doit pas s'en mêler ici.
    horloge, faux = _attendre(monkeypatch, refuse, budget_s=30.0)
    with pytest.raises(BackendExecutionError) as leve:
        C.ComfyUIHttpBackend._await_outputs(faux, "p", 1.0)
    dit = leve.value.detail
    assert "timed out" in dit and "connexion refusée" in dit
    assert "muet" not in dit


def test_un_moteur_qui_accepte_mais_ne_repond_pas_reste_dit_muet(monkeypatch):
    """L'autre moitié de la distinction : celui-là est bien silencieux."""
    def silence(horloge):
        horloge.t += 5.0                           # l'attente de lecture, jusqu'au délai
        raise TimeoutError("read timed out")

    horloge, faux = _attendre(monkeypatch, silence, budget_s=30.0)
    with pytest.raises(BackendExecutionError) as leve:
        C.ComfyUIHttpBackend._await_outputs(faux, "p", 1.0)
    dit = leve.value.detail
    assert "serveur muet" in dit and "refusée" not in dit


def test_quand_les_deux_arrivent_la_dominante_est_dite_la_premiere():
    """Le compte de chacune, et laquelle explique l'échec."""
    deux = C._dire_les_sondes(1500, 12)
    assert deux.startswith("surtout connexion refusée (1500 sondages)")
    assert "aussi serveur muet (12)" in deux
    assert C._dire_les_sondes(3, 40).startswith("surtout serveur muet (40 sondages)")
    assert C._dire_les_sondes(7, 0) == "connexion refusée sur 7 sondages"
    assert C._dire_les_sondes(0, 7) == "serveur muet sur 7 sondages"
    assert C._dire_les_sondes(0, 0) == ""          # rien à dire, rien de dit
