"""CHAQUE CHAMP PÈSE — le témoin statique des fantômes.

Antoine, 2026-09-19, après deux productions demandées à 10 s livrées à 72,9 s
puis 88,7 s : « préciser la durée n'a eu aucun poids dans la vidéo ; il faut
assurer que tout paramètre a son poids ; TOUS les paramètres doivent être ici
car ils ont un réel impact, les paramètres fantômes sont à bannir ».

Ce témoin lit les chaînes PUBLIÉES sur ce poste (`_data/chaines/*.json`), leurs
techniques (`_data/techniques/*.json`), les graphes qu'elles enchaînent
(`_data/workflows/*.json`) et les raccourcis enregistrés (`_data/raccourcis/`),
sans moteur ni serveur, et exige :

* qu'un champ exposé — par la chaîne ou par une technique — soit LU par au
  moins une étape (dans ses paramètres, son « quand », ou ce que la technique
  met derrière son rôle), et que tout renvoi « <nœud>.<entrée> » qui le porte
  vise une entrée qui EXISTE, en littéral, dans le graphe du rôle ou de
  l'étape — sinon la passerelle refuse l'override au run (`apply_overrides`),
  après avoir dépensé les étapes d'avant ;
* qu'un raccourci soit valide, ou publié PÉRIMÉ avec sa raison — jamais un 422
  silencieux au lancement ;
* qu'aucun champ ne soit « dérivé » : un champ du formulaire est un champ que
  quelqu'un lit.

Sur un poste sans `_data/`, il n'y a rien à regarder — et c'est dit (skip),
jamais un quitus.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1]
DONNEES = RACINE / "_data"


def _json(chemin: pathlib.Path):
    return json.loads(chemin.read_text(encoding="utf-8-sig"))


def _chaines_publiees():
    """Les chaînes que la réconciliation de ce poste PUBLIE (une catégorie), avec
    le nom sous lequel elle les publie."""
    reconciliation = DONNEES / "reconciliation.local.json"
    if not reconciliation.is_file() or not (DONNEES / "chaines").is_dir():
        pytest.skip("pas de _data sur ce poste : rien à regarder")
    entrees = _json(reconciliation).get("workflows") or {}
    chaines = []
    for nom, entree in entrees.items():
        if not isinstance(entree, dict) or not entree.get("chaine") or not entree.get("categorie"):
            continue
        chemin = pathlib.Path(str(entree["chaine"]))
        if not chemin.is_absolute():
            chemin = (reconciliation.parent / chemin).resolve()
        if chemin.is_file():
            chaines.append((nom, chemin, entrees))
    if not chaines:
        pytest.skip("aucune chaîne publiée sur ce poste")
    return chaines


def _techniques():
    dossier = DONNEES / "techniques"
    if not dossier.is_dir():
        return {}
    return {f.stem: noyau.lire_technique(_json(f), f.stem) for f in sorted(dossier.glob("*.json"))}


def _graphe(entrees: dict, nom: str) -> dict | None:
    """Le graphe d'une entrée de réconciliation, tel qu'il est sur disque —
    None pour une chaîne ou une entrée sans fichier."""
    entree = entrees.get(nom) or {}
    chemin = entree.get("workflow")
    if not chemin:
        return None
    fichier = pathlib.Path(str(chemin))
    if not fichier.is_absolute():
        fichier = (DONNEES / fichier).resolve()
    if not fichier.is_file():
        return None
    brut = _json(fichier)
    return brut if isinstance(brut, dict) else None


def _entree_absente(graphe: dict | None, cle: str) -> str | None:
    """Pourquoi « <nœud>.<entrée> » ne peut pas être écrit dans ce graphe — ou
    None quand il le peut. Un graphe absent du poste n'est pas jugé ici."""
    if graphe is None:
        return None
    noeud, _, entree = str(cle).rpartition(".")
    if noeud not in graphe or not isinstance(graphe[noeud].get("inputs"), dict):
        return f"le nœud {noeud!r} n'existe pas dans le graphe"
    if entree not in graphe[noeud]["inputs"]:
        return (f"le nœud {noeud!r} ({graphe[noeud].get('class_type')}) n'a pas d'entrée "
                f"{entree!r} en littéral — l'override serait refusé au run")
    return None


def _cibles_de(champ: str, chaine: noyau.Chaine, techniques: dict) -> list[tuple[str, str, str]]:
    """Où un champ est ÉCRIT dans un graphe : (étape, graphe, « nœud.entrée »),
    par les « inputs » des étapes et ceux des rôles de chaque technique."""
    cibles = []
    for etape in chaine.etapes:
        params = etape.params if isinstance(etape.params, dict) else {}
        for cle, valeur in (params.get("inputs") or {}).items():
            if valeur == f"${champ}" and etape.workflow:
                cibles.append((etape.id, etape.workflow, cle))
        if etape.role is None:
            continue
        for technique in techniques.values():
            role = technique.roles.get(etape.role)
            if role is None:
                continue
            for cle, valeur in role.inputs.items():
                if valeur == f"${champ}":
                    cibles.append((f"{etape.id} ({technique.nom})", role.workflow, cle))
    return cibles


@pytest.mark.parametrize("nom,chemin,entrees", _chaines_publiees(), ids=lambda x: x if isinstance(x, str) else "")
def test_chaque_champ_expose_est_lu_et_atteint_une_entree_qui_existe(nom, chemin, entrees):
    chaine = noyau.lire(_json(chemin), nom)
    techniques = noyau.techniques_pour(chaine, _techniques()) if chaine.champ_de_technique else {}
    noyau.verifier_techniques(chaine, techniques)
    lecteurs = {}
    for etape in chaine.etapes:
        for technique in (list(techniques.values()) or [None]):
            for tete in noyau.champs_lus_par(etape, technique):
                lecteurs.setdefault(tete, set()).add(etape.id)
    fantomes = []
    for champ in noyau.champs_admis(chaine, techniques):
        if champ == chaine.champ_de_technique:
            continue                     # lu par la passerelle : il choisit la technique
        if champ not in lecteurs:
            fantomes.append(f"{nom} : « {champ} » n'est lu par aucune étape ni aucune technique")
    assert not fantomes, "\n".join(fantomes)
    # Ce qui est écrit dans un graphe l'est sur une entrée qui existe.
    manques = []
    for champ in noyau.champs_admis(chaine, techniques):
        for etape, graphe, cle in _cibles_de(champ, chaine, techniques):
            raison = _entree_absente(_graphe(entrees, graphe), cle)
            if raison:
                manques.append(f"{nom}, étape {etape} → {graphe} : « {cle} » ← ${champ} : {raison}")
    # …et les renvois d'amont écrits dans un graphe (« $analyse.recit.x ») aussi.
    for etape in chaine.etapes:
        params = etape.params if isinstance(etape.params, dict) else {}
        for cle, valeur in (params.get("inputs") or {}).items():
            if etape.workflow and isinstance(valeur, str) and valeur.startswith("$"):
                raison = _entree_absente(_graphe(entrees, etape.workflow), cle)
                if raison:
                    manques.append(f"{nom}, étape {etape.id} → {etape.workflow} : « {cle} » ← "
                                   f"{valeur} : {raison}")
        if etape.role is not None:
            for technique in techniques.values():
                role = technique.roles.get(etape.role)
                for cle, valeur in (role.inputs.items() if role else ()):
                    raison = _entree_absente(_graphe(entrees, role.workflow), cle)
                    if raison:
                        manques.append(f"{nom}, étape {etape.id} ({technique.nom}) → "
                                       f"{role.workflow} : « {cle} » ← {valeur} : {raison}")
    assert not sorted(set(manques)), "\n".join(sorted(set(manques)))


@pytest.mark.parametrize("nom,chemin,entrees", _chaines_publiees(), ids=lambda x: x if isinstance(x, str) else "")
def test_aucun_champ_n_est_derive(nom, chemin, entrees):
    """Un champ du contrat d'une chaîne est DÉCLARÉ par son propriétaire — la
    chaîne, ou une technique que la chaîne choisit — et jamais dérivé d'un
    autre (un nombre d'images tiré d'une durée est l'affaire d'un graphe, pas
    d'un formulaire). Une chaîne qui ne choisit aucune technique n'admet aucun
    de leurs champs (mesuré le 2026-09-19 : un mode sans technique publiait les
    papiers et les encres d'un autre parmi ce qu'il accepte)."""
    chaine = noyau.lire(_json(chemin), nom)
    techniques = noyau.techniques_pour(chaine, _techniques())
    declares = set(chaine.champs)
    if chaine.champ_de_technique is not None:
        for technique in techniques.values():
            declares |= set(technique.champs)
    assert set(noyau.champs_admis(chaine, techniques)) == declares
    for champ in noyau.champs_admis(chaine, techniques).values():
        assert champ.media is not None or champ.type in noyau._TYPES, champ.nom


def test_chaque_raccourci_est_valide_ou_publie_perime_avec_sa_raison():
    """Un raccourci vieillit sans qu'on y touche ; la vitrine le juge à la
    lecture par la validation d'une demande. Ici, la même lecture, sans
    serveur : chaque fiche est valide, ou périmée avec ses champs et sa raison
    — jamais un 422 silencieux au lancement."""
    dossier = DONNEES / "raccourcis"
    if not dossier.is_dir():
        pytest.skip("aucun raccourci enregistré sur ce poste")
    chaines = {nom: (chemin, entrees) for nom, chemin, entrees in _chaines_publiees()}
    techniques = _techniques()
    verdicts = []
    for fiche in sorted(dossier.glob("*/*.json")):
        if fiche.name.startswith("."):
            continue
        brut = _json(fiche)
        mode = str(brut.get("workflow") or fiche.parent.name)
        valeurs = brut.get("valeurs") or {}
        if mode not in chaines:
            continue                      # un raccourci de graphe : jugé par l'API
        chaine = noyau.lire(_json(chaines[mode][0]), mode)
        siennes = noyau.techniques_pour(chaine, techniques)
        admis = noyau.champs_admis(chaine, siennes)
        technique = noyau.technique_choisie(chaine, valeurs, siennes)
        retenus = noyau.champs_retenus(chaine, technique)
        fautes = []
        for champ, brute in valeurs.items():
            if champ not in admis:
                fautes.append(f"« {champ} » : le mode ne l'expose plus")
            elif champ not in retenus:
                fautes.append(f"« {champ} » : n'est pas un réglage de la technique "
                              f"{technique.nom if technique else 'choisie'}")
            else:
                try:
                    noyau.valeur_de(retenus[champ], brute)
                except Exception as exc:          # noqa: BLE001 — la raison est le verdict
                    fautes.append(getattr(exc, "detail", str(exc)))
        verdicts.append((mode, fiche.stem, fautes))
    assert verdicts, "aucune fiche de raccourci sous un mode publié"
    # Chaque verdict est DIT : valide, ou périmé avec une raison non vide.
    for mode, ident, fautes in verdicts:
        assert all(str(f).strip() for f in fautes), (mode, ident, fautes)
    perimes = [(mode, ident, fautes) for mode, ident, fautes in verdicts if fautes]
    # Un raccourci périmé n'est pas une faute de la passerelle : il est publié
    # tel, et c'est écrit ici pour que la liste se lise sans lancer le serveur.
    print("\n".join(f"périmé : {mode}/{ident} — {' ; '.join(fautes)}" for mode, ident, fautes in perimes)
          or "aucun raccourci périmé")
