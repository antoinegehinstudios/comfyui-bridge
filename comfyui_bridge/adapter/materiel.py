"""Les limites MATÉRIELLES de ce poste, déclarées une fois.

Antoine, 2026-09-16 : « les limitations matérielles du PC doivent être dans un
fichier de réconciliation, qui permet de faire les calculs pour que le workflow
sache ajuster son nombre d'itérations (au cas où la RAM du PC venait à
changer) ». Le nombre de tranches d'un rendu n'est pas un réglage de flux : il
se DÉDUIT de la mémoire de la machine. Écrit dans le code, il aurait fallu le
rouvrir à chaque barrette ajoutée ; écrit dans une chaîne, il aurait suivi le
flux sur une autre machine, où il aurait été faux.

Le fichier vit dans le dossier de données (`_data/materiel.local.json`, non
versionné : il décrit UNE machine) ; le paquet en garde une copie d'exemple
(`resources/materiel.exemple.json`).

Le BUDGET d'une tranche, en octets d'images de sortie :

    (memoire.totale_octets − memoire.reservee_octets) / memoire.facteur_de_crete

`reservee_octets` est ce que le reste de la machine garde (moteur, modèles,
services, bureau — mesuré à 28–30 Go ici) ; `facteur_de_crete` est combien de
fois le poids des images un run occupe à son pic. Mesuré le 2026-09-15 à
720p, un run seul : 20 Gio d'images passaient avec 36 Go libres, 23,3 Gio
échouaient — un pic à environ 1,6×. Mesuré le 2026-09-16 à 4K/60, tranche
après tranche : 3,2× (9,3 Gio d'images, 30 Gio au pic — le moteur gardait en
cache les images du run précédent, et l'encodage copie) ; 3,5 est déclaré, et
le moteur lancé sans ce cache.

Ce module MESURE aussi la RAM réellement présente, pour pouvoir dire qu'un
fichier ment (« le fichier dit 64 Go, le poste en a 32 »). La mesure n'est
jamais une source du BUDGET : un poste qui répond mal sur sa propre mémoire ne
doit pas faire varier le découpage d'un run à l'autre. Elle sert à deux
choses : avertir, et dire la MARGE DU MOMENT — ce qu'une allocation peut
encore prendre maintenant, RAM physique libre et commit restant (sous Windows,
la limite de commit = RAM + fichier d'échange, et c'est elle qu'une allocation
heurte en premier : le 2026-09-16, 8,6 Gio refusés avec 18 Gio de RAM physique
libre, parce qu'un voisin venait d'engager 26 Go). Avant chaque tranche, le
rendu attend que cette marge tienne le pic attendu (voir chaines.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.errors import WorkflowMappingError

FICHIER = "materiel.local.json"

# Le repli quand rien n'est déclaré : huit gibioctets. Il est DIT, au démarrage
# et dans `/v1/materiel` — un budget deviné en silence aurait fait découper
# (ou pas) sans que personne sache pourquoi.
REPLI_OCTETS = 8 * 2 ** 30

# D'où vient le budget employé. Trois mots, écrits une fois : le journal d'un
# job, la route `/v1/materiel` et le démarrage doivent dire le même.
SURCHARGE = "COMFY_TRANCHE_GO"
DECLARE = FICHIER
REPLI = "repli"


def chemin(data_dir: Path | str | None) -> Path | None:
    return (Path(data_dir) / FICHIER) if data_dir else None


def lire(data_dir: Path | str | None) -> dict[str, Any] | None:
    """Le matériel déclaré, ou ``None`` s'il n'est pas déclaré.

    Ce qui est écrit est VÉRIFIÉ ici, en le nommant : un « totale_octets » à
    zéro ou une réservée plus grande que la totale donnerait un budget négatif,
    donc un nombre de tranches absurde, découvert au premier rendu long.
    """
    f = chemin(data_dir)
    if f is None or not f.is_file():
        return None
    try:
        brut = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"matériel : impossible de lire {f} : {exc}") from exc
    if not isinstance(brut, dict):
        raise WorkflowMappingError(f"matériel : {f} doit être un objet JSON")
    memoire = brut.get("memoire")
    if not isinstance(memoire, dict):
        raise WorkflowMappingError(f"matériel : {f} n'a pas de « memoire »")
    totale = _entier(memoire.get("totale_octets"), "memoire.totale_octets", f)
    reservee = _entier(memoire.get("reservee_octets"), "memoire.reservee_octets", f)
    if reservee >= totale:
        raise WorkflowMappingError(
            f"matériel : {f} réserve {reservee} octets sur {totale} — il ne resterait "
            f"rien à un rendu")
    crete = memoire.get("facteur_de_crete", 1)
    try:
        crete = float(crete)
    except (TypeError, ValueError):
        raise WorkflowMappingError(
            f"matériel : {f} — « facteur_de_crete » doit être un nombre, reçu "
            f"{crete!r}") from None
    if crete < 1:
        raise WorkflowMappingError(
            f"matériel : {f} — « facteur_de_crete » vaut {crete} : un run occupe au moins "
            f"une fois le poids de ses images")
    lu: dict[str, Any] = {"memoire": {"totale_octets": totale, "reservee_octets": reservee,
                                      "facteur_de_crete": crete}}
    graphique = brut.get("memoire_graphique")
    if isinstance(graphique, dict) and graphique.get("totale_octets") is not None:
        lu["memoire_graphique"] = {"totale_octets": _entier(
            graphique.get("totale_octets"), "memoire_graphique.totale_octets", f)}
    if brut.get("coeurs") is not None:
        lu["coeurs"] = _entier(brut.get("coeurs"), "coeurs", f)
    return lu


def _entier(valeur: Any, nom: str, f: Path) -> int:
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or valeur <= 0:
        raise WorkflowMappingError(
            f"matériel : {f} — « {nom} » doit être un nombre d'octets positif, reçu "
            f"{valeur!r}")
    return int(valeur)


def budget_tranche(materiel: dict[str, Any] | None) -> int | None:
    """Le budget d'une tranche, en octets d'images de sortie, ou ``None``."""
    if not materiel:
        return None
    memoire = materiel.get("memoire") or {}
    libre = int(memoire.get("totale_octets", 0)) - int(memoire.get("reservee_octets", 0))
    crete = float(memoire.get("facteur_de_crete") or 1)
    return int(libre / crete) if libre > 0 and crete >= 1 else None


def mesure_du_poste() -> dict[str, Any] | None:
    """Ce que la machine dit d'elle-même — ou ``None`` si elle ne sait pas le dire.

    Deux façons, dans cet ordre : psutil quand il est installé, puis l'API de
    Windows. Aucune n'est requise : la mesure ne sert qu'à AVERTIR qu'un fichier
    ment. Rendre zéro plutôt que rien aurait fait croire à une machine sans
    mémoire.
    """
    mesure: dict[str, Any] | None = None
    try:
        import psutil                                    # noqa: PLC0415
        v = psutil.virtual_memory()
        mesure = {"totale_octets": int(v.total), "libre_octets": int(v.available),
                  "par": "psutil"}
    except Exception:                                    # noqa: BLE001
        # repli: psutil n'est pas une dépendance de ce service ; son absence est
        # normale et n'a rien à dire. La suite essaie l'API du système.
        pass
    if os.name == "nt":
        systeme = _mesure_windows()
        if systeme is not None:
            if mesure is None:
                mesure = systeme
            else:
                # psutil ne dit pas le commit ; l'API du système, si.
                mesure["commit_total_octets"] = systeme["commit_total_octets"]
                mesure["commit_libre_octets"] = systeme["commit_libre_octets"]
    return mesure


def _mesure_windows() -> dict[str, Any] | None:
    """La mémoire vue par Windows : physique ET commit (RAM + fichier d'échange,
    ce qu'une allocation heurte en premier). ``None`` si l'API ne répond pas."""
    try:
        import ctypes                                    # noqa: PLC0415

        class _Etat(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        etat = _Etat()
        etat.dwLength = ctypes.sizeof(_Etat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(etat)):
            return {"totale_octets": int(etat.ullTotalPhys),
                    "libre_octets": int(etat.ullAvailPhys),
                    "commit_total_octets": int(etat.ullTotalPageFile),
                    "commit_libre_octets": int(etat.ullAvailPageFile),
                    "par": "GlobalMemoryStatusEx"}
    except Exception:                                    # noqa: BLE001
        # repli: une API système qui ne répond pas ne doit pas empêcher un
        # rendu ; l'absence de mesure est DITE par l'appelant.
        return None
    return None


def marge_du_moment(mesure: dict[str, Any] | None) -> int | None:
    """Ce qu'une allocation peut encore prendre MAINTENANT : le plus petit de la
    RAM physique libre et du commit restant, quand le poste les dit. ``None``
    quand rien n'est mesurable — et alors personne n'est retenu."""
    if not mesure:
        return None
    valeurs = [int(mesure[cle]) for cle in ("libre_octets", "commit_libre_octets")
               if mesure.get(cle) is not None]
    return min(valeurs) if valeurs else None


def dire_la_marge(mesure: dict[str, Any] | None) -> str:
    """« physique 18,1 Gio, commit 6,8 Gio » — ou ce qu'on sait."""
    if not mesure:
        return "mémoire non mesurable"
    parts = []
    if mesure.get("libre_octets") is not None:
        parts.append(f"physique {int(mesure['libre_octets']) / 2 ** 30:.1f} Gio")
    if mesure.get("commit_libre_octets") is not None:
        parts.append(f"commit {int(mesure['commit_libre_octets']) / 2 ** 30:.1f} Gio")
    return ", ".join(parts) or "mémoire non mesurable"


def etat(materiel: dict[str, Any] | None, surcharge: int = 0) -> dict[str, Any]:
    """Le matériel tel que `/v1/materiel` le rend : déclaré, mesuré, budget,
    provenance — et l'avertissement quand le déclaré dépasse le mesuré."""
    mesure = mesure_du_poste()
    du_fichier = budget_tranche(materiel)
    budget, provenance = _budget_et_provenance(du_fichier, surcharge)
    marge = marge_du_moment(mesure)
    vu: dict[str, Any] = {
        "declare": materiel,
        "mesure": mesure,
        "marge_du_moment_octets": marge,
        "budget_tranche_octets": budget,
        "budget_tranche_gio": round(budget / 2 ** 30, 2) if budget else 0,
        "provenance": provenance,
        "avertissements": [],
    }
    # Le poste garde-t-il EN CE MOMENT plus que le fichier ne déclare ? Le
    # budget est déclaré, pas mesuré — mais un rendu par tranches attend sa
    # place avant chaque tranche, et cet avertissement dit pourquoi il attend.
    reservee = int(((materiel or {}).get("memoire") or {}).get("reservee_octets") or 0)
    totale_vue = int((mesure or {}).get("totale_octets") or 0)
    if marge is not None and totale_vue and reservee:
        garde = totale_vue - marge
        if garde > reservee * 1.1:
            vu["avertissements"].append(
                f"le poste garde en ce moment {garde / 2 ** 30:.0f} Go "
                f"({dire_la_marge(mesure)}), plus que les {reservee / 2 ** 30:.0f} Go déclarés "
                f"réservés : le budget déclaré ne tient pas tant que ça dure — un rendu par "
                f"tranches attend sa place avant chaque tranche")
    if materiel is None and not surcharge:
        vu["avertissements"].append(
            f"aucun fichier matériel ({FICHIER}) : repli "
            f"{REPLI_OCTETS / 2 ** 30:.0f} Gio par tranche")
    declaree = int(((materiel or {}).get("memoire") or {}).get("totale_octets") or 0)
    mesuree = int((mesure or {}).get("totale_octets") or 0)
    # La MARGE : un système ne rend jamais la RAM nominale — une part est prise
    # par le firmware et le graphique intégré. Mesuré ici : 68 631 527 424 octets
    # rendus pour 68 719 476 736 déclarés (64 Gio), soit 0,13 % de moins. Sans
    # cette marge, l'avertissement criait « le fichier dit 64 Go, le poste en a
    # 64 » à chaque appel, et un vrai mensonge (64 déclarés, 32 présents) se
    # serait perdu dans ce bruit.
    if mesuree and declaree and declaree > mesuree * 1.05:
        vu["avertissements"].append(
            f"le fichier dit {declaree / 2 ** 30:.0f} Go, le poste en a "
            f"{mesuree / 2 ** 30:.0f}")
    if mesure is None:
        vu["avertissements"].append(
            "la mémoire du poste n'a pas pu être mesurée : le déclaré n'est pas vérifiable")
    return vu


def _budget_et_provenance(du_fichier: int | None, surcharge: int) -> tuple[int, str]:
    """L'ordre, écrit UNE fois : une surcharge d'essai, sinon le fichier, sinon
    le repli. Deux lectures de cet ordre auraient fini par se contredire."""
    if surcharge and surcharge > 0:
        return int(surcharge), SURCHARGE
    if du_fichier:
        return int(du_fichier), DECLARE
    return REPLI_OCTETS, REPLI


def budget_et_provenance(materiel: dict[str, Any] | None, surcharge: int) -> tuple[int, str]:
    return _budget_et_provenance(budget_tranche(materiel), surcharge)


def dire(budget: int, provenance: str) -> str:
    """Comment le budget se dit — au démarrage comme dans le journal d'un job.

    Sous le gibioctet, il se dit en mébioctets : « 0.0 Gio par tranche » est un
    budget qu'on lit comme une panne, alors que c'est celui d'un essai.
    """
    d_ou = {SURCHARGE: f"d'après la surcharge {SURCHARGE}",
            DECLARE: f"d'après {FICHIER}",
            REPLI: f"par repli (aucun {FICHIER})"}.get(provenance, provenance)
    taille = (f"{budget / 2 ** 30:.1f} Gio" if budget >= 2 ** 30
              else f"{budget / 2 ** 20:.0f} Mio")
    return f"budget de {taille} par tranche, {d_ou}"
