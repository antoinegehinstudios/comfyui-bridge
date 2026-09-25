"""SANS CASSE : chaque mode, réécrit sur ses réconciliants, se déplie en la chaîne d'AVANT — aux écarts DÉCLARÉS près.

Antoine, 2026-09-25 : « identifie les divergents qui sont silotés, pour adapter la couche en silo ; toujours
résultant par des tests qui prouvent que le flux n'est pas cassé ». Les six chaînes publiées ont été figées
AVANT le chantier (`tests/donnees/chaines-avant-reconciliants/`, fusion 0054155). Ce témoin déplie chaque
source d'aujourd'hui et la compare à sa chaîne d'avant, chemin par chemin : tout écart doit figurer dans la
liste ci-dessous, avec sa raison — un écart de plus, ou un de moins, fait échouer le témoin. Chaque
réécriture de VALEUR est prouvée équivalente sur tous les états que le nœud de la charte peut écrire.

Ce témoin est aussi le rapport d'impact du réconciliant : le modifier change les modes qui le branchent, et
c'est ici que ce changement se déclare, mode par mode.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core import reconciliant

RACINE = pathlib.Path(__file__).resolve().parents[1]
AVANT = RACINE / "tests" / "donnees" / "chaines-avant-reconciliants"
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
DONNEES = RACINE / "_data"
MODES = ("image-creation", "image-visuel-social", "video-depuis-un-texte", "video-revelation", "video-affiche",
         "video-prolongement")


def ecarts(a, b, chemin=""):
    """(chemin, genre) de chaque écart ; une liste d'étapes ou de contrôles se compare par « id » — un ajout ou un
    retrait est un écart, et l'ORDRE de ce que les deux ont en commun en est un aussi (« [ordre] »)."""
    if isinstance(a, dict) and isinstance(b, dict):
        sortie = []
        for k in list(a) + [k for k in b if k not in a]:
            if k not in b:
                sortie.append((f"{chemin}.{k}", "retiré"))
            elif k not in a:
                sortie.append((f"{chemin}.{k}", "ajouté"))
            else:
                sortie += ecarts(a[k], b[k], f"{chemin}.{k}")
        return sortie
    if isinstance(a, list) and isinstance(b, list):
        ids_a = [x.get("id") for x in a if isinstance(x, dict)]
        ids_b = [x.get("id") for x in b if isinstance(x, dict)]
        if a and b and len(ids_a) == len(a) and len(ids_b) == len(b) and all(ids_a) and all(ids_b):
            sortie = [(f"{chemin}[ordre]", "ordre")] if [i for i in ids_a if i in ids_b] != [i for i in ids_b if i in ids_a] else []
            da, db = {x["id"]: x for x in a}, {x["id"]: x for x in b}
            for i in ids_a:
                sortie += [(f"{chemin}[{i}]", "retiré")] if i not in db else ecarts(da[i], db[i], f"{chemin}[{i}]")
            sortie += [(f"{chemin}[{i}]", "ajouté") for i in ids_b if i not in da]
            return sortie
        if len(a) != len(b):
            return [(chemin, "changé")]
        return [e for i, (x, y) in enumerate(zip(a, b)) for e in ecarts(x, y, f"{chemin}[{i}]")]
    return [] if a == b else [(chemin, "changé")]


def avant(nom):
    return json.loads((AVANT / f"{nom}.json").read_text(encoding="utf-8"))


def apres(nom):
    return reconciliant.deplier(json.loads((EXEMPLES / f"{nom}.json").read_text(encoding="utf-8")))


# Les valeurs « sans charte » sont celles d'UN réconciliant pour tous les modes : chacun reçoit les clés que les
# autres avaient déjà. Aucune n'est lue par une étape qui tourne sans charte (prouvé plus bas), sauf
# « texte_laisse_au_logo », que le visuel social lit désormais — nulle sans charte, comme le « » d'avant.
SANS_CHARTE_IMAGE = {f".etapes[contrainte].sinon.recit.{k}" for k in (
    "prompt", "part_min_dans_palette", "texte_laisse_au_logo", "carton_fond_fichier", "carton_logo_fichier",
    "carton_debut_s", "carton_s", "carton_logo_largeur")}
SANS_CHARTE_VIDEO = {f".etapes[contrainte].sinon.recit.{k}" for k in (
    "repli", "positif", "couleurs_en", "colorway_en", "logo_raison", "references_transmises", "imposes")}
CONFORMITE_SANS_CHARTE = {f".etapes[conformite].sinon.recit.{k}" for k in (
    "logo_espace_libre_tenu", "logo_position_tenue")}
# Les aides des constats de la charte : une seule pour l'image (le standard et le visuel disaient la même chose en
# deux mots différents).
AIDE = ".etapes[constat_de_la_charte].constater[{}].aide"
ECARTS_DECLARES = {
    "image-creation": {
        **{e: "ajouté" for e in SANS_CHARTE_IMAGE | CONFORMITE_SANS_CHARTE},
        ".etapes[conformite].sinon.recit.logo_texte_non_reecrit": "ajouté",
        AIDE.format("la_charte_demandee_est_appliquee"): "changé",
        AIDE.format("le_logo_impose_est_pose"): "changé",
    },
    "image-visuel-social": {
        **{e: "ajouté" for e in SANS_CHARTE_IMAGE | CONFORMITE_SANS_CHARTE},
        # le nom laissé au logo : ce que le nœud laisse (le même câblage que la vidéo), prouvé équivalent plus bas
        ".etapes[livraison].composer.textes[0].laisser_au_logo": "changé",
        ".etapes[livraison].composer.textes[1].laisser_au_logo": "changé",
        # le constat du nom écrit par le logo : sous la décision (« logo_pose »), prouvé équivalent plus bas
        ".etapes[constat_de_la_charte].constater[ce_que_le_logo_ecrit_n_est_pas_reecrit].valeur.si": "changé",
        **{AIDE.format(c): "changé" for c in ("la_charte_demandee_est_appliquee", "la_palette_de_la_charte_est_tenue",
                                               "le_logo_impose_est_pose", "le_logo_impose_est_intact")},
    },
    "video-depuis-un-texte": {
        # la charte EN PREMIER, rangée sous « charte » ; le logo est celui du réconciliant (menu « logo_charte »,
        # n'existe que sous une charte), « où le logo vit » aussi
        ".expose.charte.categorie": "changé",
        ".expose.logo.options": "retiré", ".expose.logo.options_depuis": "ajouté", ".expose.logo.libelle": "changé",
        ".expose.logo.selon": "ajouté", ".expose.logo_ou.selon": "ajouté",
        **{e: "ajouté" for e in SANS_CHARTE_VIDEO},
        # les constats de la charte quittent « constat » pour l'étape du réconciliant, qui n'a lieu que sous une
        # charte (sans elle, ils étaient « tenus » d'office : des contrôles qui ne pouvaient pas échouer)
        **{f".etapes[constat].constater[{c}]": "retiré" for c in (
            "la_charte_demandee_est_appliquee", "la_palette_de_la_charte_est_tenue", "le_logo_impose_est_pose",
            "le_logo_impose_est_intact", "le_logo_impose_tient_sa_taille", "l_espace_libre_du_logo_est_tenu",
            "le_logo_est_a_sa_place", "le_texte_du_wordmark_n_est_pas_reecrit",
            "les_regles_mesurables_de_la_charte_sont_tenues", "la_video_pese")},
        ".etapes[constat_de_la_charte]": "ajouté",
        # le socle : le poids du fichier livré se contrôle en dernier (il se constatait)
        ".etapes[controle]": "ajouté",
        ".gabarit": "ajouté",
    },
    "video-revelation": {".gabarit": "ajouté"},
    "video-affiche": {".gabarit": "ajouté"},
    "video-prolongement": {
        **{f".expose.{c}.{q}": "ajouté" for c in ("video", "suite", "duration_s", "seed") for q in ("categorie", "aide")},
        ".etapes[controle].verifier[livrable_pese]": "ajouté",
        ".gabarit": "ajouté",
    },
}


@pytest.mark.parametrize("nom", MODES)
def test_chaque_mode_deplie_est_celui_d_avant_aux_ecarts_declares_pres(nom):
    trouves = dict(ecarts(avant(nom), apres(nom)))
    assert trouves == ECARTS_DECLARES[nom], (
        "écarts non déclarés : " + json.dumps(sorted(set(trouves.items()) - set(ECARTS_DECLARES[nom].items())), ensure_ascii=False)
        + " ; déclarés mais absents : " + json.dumps(sorted(set(ECARTS_DECLARES[nom].items()) - set(trouves.items())), ensure_ascii=False))
    # l'ordre des champs : inchangé, sauf la vidéo, dont la charte vient désormais EN PREMIER
    ordre_avant, ordre_apres = list(avant(nom)["expose"]), list(apres(nom)["expose"])
    if nom == "video-depuis-un-texte":
        assert ordre_apres[:3] == ["charte", "logo", "logo_ou"]
        assert [c for c in ordre_apres[3:]] == [c for c in ordre_avant if c not in ("charte", "logo", "logo_ou")]
    else:
        assert ordre_apres == ordre_avant


@pytest.mark.parametrize("nom", MODES)
def test_la_chaine_depliee_se_lit_et_suit_son_gabarit_au_socle(nom):
    from comfyui_bridge.core import gabarit
    chaine = noyau.lire(apres(nom), nom)
    suivi = gabarit.lire(chaine.gabarit)
    assert gabarit.suit_le_socle(suivi) and gabarit.ecarts(chaine, suivi) == [], nom


def test_les_valeurs_sans_charte_ajoutees_ne_sont_lues_par_aucune_etape_qui_tourne_sans_charte():
    """Une clé du « sinon » que personne ne lit ne change rien ; celle qu'on lit, on la prouve à part."""
    for nom in ("image-creation", "image-visuel-social", "video-depuis-un-texte"):
        chaine = apres(nom)
        ajoutees = {e.rsplit(".", 1)[1] for e in ECARTS_DECLARES[nom] if e.startswith(".etapes[contrainte].sinon.recit.")}
        sans_charte = [e for e in chaine["etapes"] if e.get("quand") != {"valeur": "$charte", "op": "ne", "attendu": "aucune"}]
        lues = {r.split(".", 3)[2] for e in sans_charte for r in noyau.renvois(e) if r.startswith("contrainte.recit.")}
        permises = {"texte_laisse_au_logo"} if nom == "image-visuel-social" else set()
        assert lues & ajoutees <= permises, (nom, lues & ajoutees)


def test_chaque_cle_du_recit_lue_sans_charte_a_sa_valeur_sans_charte():
    """Sans charte, l'étape « contrainte » n'a pas lieu et son « sinon » répond : une clé lue par une étape qui
    tourne sans charte et absente du « sinon » ferait échouer la livraison (« n'a rien à désigner »)."""
    for nom in ("image-creation", "image-visuel-social", "video-depuis-un-texte"):
        chaine = apres(nom)
        etapes = {e["id"]: e for e in chaine["etapes"]}
        sinon = etapes["contrainte"]["sinon"]["recit"]
        for e in chaine["etapes"]:
            if e.get("quand") == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}:
                continue
            for r in noyau.renvois(e):
                if r.startswith("contrainte.recit."):
                    assert r.split(".", 3)[2] in sinon, (nom, e["id"], r)


def _recits_d_une_image():
    """Tous les états qu'écrit le nœud de la charte pour une IMAGE FIXE (sans durée : un logo posé l'est en zone
    émetteur, pendant l'image — `_decider_et_placer_le_logo`) : fichier du logo ou non, posé ou non, texte ou non."""
    for fichier in (None, "E:/logos/wordmark.png"):
        for pose in (False, True):
            for texte in (None, "GRABUGE FEST"):
                pose_effectif = bool(fichier) and pose
                recit = {"logo_fichier": fichier, "logo_texte": texte, "logo_pose": pose_effectif,
                         "logo_fichier_pendant": fichier if pose_effectif else None,
                         "texte_laisse_au_logo": texte if pose_effectif else None}
                for lu in (True, False):
                    yield {"contrainte": {"recit": recit}, "conformite": {"recit": {"logo_texte_non_reecrit": lu}}}


def test_les_reecritures_du_visuel_social_sont_equivalentes_sur_tous_les_etats_du_noeud():
    textes_avant = next(e for e in avant("image-visuel-social")["etapes"] if e["id"] == "livraison")["composer"]["textes"]
    textes_apres = next(e for e in apres("image-visuel-social")["etapes"] if e["id"] == "livraison")["composer"]["textes"]
    constat = lambda c: next(x for x in next(e for e in c["etapes"] if e["id"] == "constat_de_la_charte")["constater"]  # noqa: E731
                             if x["id"] == "ce_que_le_logo_ecrit_n_est_pas_reecrit")["valeur"]
    for resultats in _recits_d_une_image():
        for t_avant, t_apres in zip(textes_avant, textes_apres):
            v_avant = noyau.resoudre(t_avant["laisser_au_logo"], {}, resultats)
            v_apres = noyau.resoudre(t_apres["laisser_au_logo"], {}, resultats)
            # la pose des textes ne lit que le texte non vide (`textes.py` : str(x or "").strip()) : « » et nul se valent
            assert str(v_avant or "").strip() == str(v_apres or "").strip(), resultats
        assert (noyau.resoudre(constat(avant("image-visuel-social")), {}, resultats)
                == noyau.resoudre(constat(apres("image-visuel-social")), {}, resultats)), resultats


def test_la_video_garde_chaque_branchement_que_sa_charte_lui_donnait():
    """Ce que la session de la charte a demandé de garder (2026-09-25), épinglé renvoi par renvoi."""
    etapes = {e["id"]: e for e in apres("video-depuis-un-texte")["etapes"]}
    entrees = etapes["contrainte"]["rendre"]["inputs"]
    assert {k: entrees[k] for k in ("1.logo", "1.logo_ou", "1.accroche", "1.appel", "1.texte_position", "1.duree_s")} == {
        "1.logo": "$logo", "1.logo_ou": "$logo_ou", "1.accroche": "$accroche", "1.appel": "$appel",
        "1.texte_position": "$texte_position", "1.duree_s": "$duration_s"}
    recoller = etapes["livraison"]["recoller"]
    for texte, quoi in zip(recoller["textes"], ("accroche", "appel")):
        assert (texte["position"], texte["debut_s"], texte["fin_s"]) == tuple(f"$contrainte.recit.{quoi}_{t}" for t in ("position", "debut_s", "fin_s"))
        assert (texte["police"], texte["couleur"], texte["fond"], texte["laisser_au_logo"]) == (
            "$contrainte.recit.police", "$contrainte.recit.couleur", "$contrainte.recit.fond", "$contrainte.recit.texte_laisse_au_logo")
    fond, logo, carton = recoller["images"]
    assert fond == {"fichier": "$contrainte.recit.carton_fond_fichier", "couvrir": True, "sous_les_textes": True,
                    "debut_s": "$contrainte.recit.carton_debut_s"}
    assert logo["fichier"] == "$contrainte.recit.logo_fichier_pendant" and logo["espace_min"] == "$contrainte.recit.logo_zone"
    assert (carton["fichier"], carton["ancrage"], carton["largeur"], carton["debut_s"]) == (
        "$contrainte.recit.carton_logo_fichier", "centre", "$contrainte.recit.carton_logo_largeur", "$contrainte.recit.carton_debut_s")
    assert recoller["texture"]["fichier"] == "$contrainte.recit.texture_fichier"
    conformite = etapes["conformite"]["rendre"]
    assert conformite["inputs"]["1.logo_attendu"] == "$contrainte.recit.logo_attendu"
    assert conformite["inputs"]["1.carton_s"] == "$contrainte.recit.carton_s"
    sinon = etapes["contrainte"]["sinon"]["recit"]
    assert sinon["logo_attendu"] == "aucun" and sinon["logo_pose"] is False and sinon["texte_laisse_au_logo"] is None


def test_les_constats_de_la_charte_de_la_video_sont_les_memes_mesures():
    """Déplacés dans l'étape du réconciliant, ils mesurent la même chose, sur le même fichier, avec le même
    seuil ; le nom écrit par le logo prend l'id commun et n'est constaté que quand le logo est posé — sans logo,
    le nom reste dans les textes (règle de la session de la charte, 2026-09-24 : « ne laisser le nom au logo que
    si un logo est posé »)."""
    anciens = {c["id"]: c for c in next(e for e in avant("video-depuis-un-texte")["etapes"] if e["id"] == "constat")["constater"]}
    etapes = {e["id"]: e for e in apres("video-depuis-un-texte")["etapes"]}
    nouveaux = {c["id"]: c for c in etapes["constat_de_la_charte"]["constater"]}
    assert etapes["constat_de_la_charte"]["quand"] == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}
    renomme = {"le_texte_du_wordmark_n_est_pas_reecrit": "ce_que_le_logo_ecrit_n_est_pas_reecrit"}
    for ancien_id in ("la_charte_demandee_est_appliquee", "la_palette_de_la_charte_est_tenue", "le_logo_impose_est_pose",
                      "le_logo_impose_est_intact", "le_logo_impose_tient_sa_taille", "l_espace_libre_du_logo_est_tenu",
                      "le_logo_est_a_sa_place", "les_regles_mesurables_de_la_charte_sont_tenues"):
        a, n = anciens[ancien_id], nouveaux[renomme.get(ancien_id, ancien_id)]
        assert (a["valeur"], a["op"], a.get("attendu")) == (n["valeur"], n["op"], n.get("attendu")), ancien_id
        assert n["aide"].strip()
    mot = nouveaux["ce_que_le_logo_ecrit_n_est_pas_reecrit"]
    assert mot["valeur"] == {"si": "$contrainte.recit.logo_pose", "alors": anciens["le_texte_du_wordmark_n_est_pas_reecrit"]["valeur"],
                             "sinon": True}
    assert set(nouveaux) == {"la_charte_demandee_est_appliquee", "la_palette_de_la_charte_est_tenue", "le_logo_impose_est_pose",
                             "le_logo_impose_est_intact", "le_logo_impose_tient_sa_taille", "l_espace_libre_du_logo_est_tenu",
                             "le_logo_est_a_sa_place", "ce_que_le_logo_ecrit_n_est_pas_reecrit",
                             "les_regles_mesurables_de_la_charte_sont_tenues"}
    pese = anciens["la_video_pese"]
    controle = etapes["controle"]["verifier"][0]
    assert controle["id"] == "livrable_pese" and (controle["valeur"], controle["op"], controle["attendu"]) == (
        pese["valeur"], pese["op"], pese["attendu"])


def test_aucun_mode_ne_lit_plus_une_source_qu_a_travers_son_reconciliant():
    """La SOURCE de chaque mode ne nomme ni un graphe, ni un menu, ni le récit d'une étape d'une source : elle
    branche des réconciliants et lit leurs emplacements. Une nouveauté d'Héraldiste, d'Iconographe ou
    d'Iconologue se câble dans UN fichier."""
    registre = reconciliant.tous()
    graphes = {g for r in registre.values() for g in r["source"]["graphes"]}
    menus = {m for r in registre.values() for m in r["source"]["menus"]}
    for nom in MODES:
        source = json.loads((EXEMPLES / f"{nom}.json").read_text(encoding="utf-8"))
        texte = json.dumps(source, ensure_ascii=False)
        assert not any(f'"{g}"' in texte for g in graphes), nom
        assert not any(f'"menu": "{m}"' in texte for m in menus), nom
        assert "$contrainte.recit." not in texte and "$conformite.recit." not in texte, nom
        assert "$analyse.recit." not in texte and "$culture.recit." not in texte, nom
    lus = {nom: sorted(json.loads((EXEMPLES / f"{nom}.json").read_text(encoding="utf-8")).get("reconciliants") or {})
           for nom in MODES}
    assert lus == {"image-creation": ["charte"], "image-visuel-social": ["charte"], "video-depuis-un-texte": ["charte"],
                   "video-revelation": ["analyse", "culture"], "video-affiche": [], "video-prolongement": []}


def test_sur_ce_poste_le_catalogue_declare_son_socle_et_dit_ses_divergents():
    """Le catalogue du poste exige le socle de toute création publiée ; les graphes seuls qu'il publie encore sont
    dits « hors socle » (l'inventaire du 2026-09-25 : image → vidéo, vidéo longue, son, 3D) ; les chaînes
    publiées portent la provenance de leurs réconciliants."""
    reconciliation = DONNEES / "reconciliation.local.json"
    if not reconciliation.is_file():
        pytest.skip("pas de _data sur ce poste : rien à regarder")
    from comfyui_bridge.adapter import catalog as cat
    c = cat.load_catalog(reconciliation, DONNEES / "workflows", data_dir=DONNEES)
    assert c.socle == "socle"
    publies = {n: s for n, s in c._specs.items() if s.categorie is not None}
    for nom, spec in publies.items():
        if spec.est_chaine:
            from comfyui_bridge.core import gabarit
            assert gabarit.suit_le_socle(gabarit.lire(spec.gabarit)) and not spec.hors_socle, nom
        else:
            assert spec.hors_socle and "graphe seul" in spec.hors_socle, nom
    for nom in ("image-creation", "image-visuel-social", "video-depuis-un-texte"):
        if nom in publies:
            assert set(publies[nom].reconciliants) == {"charte"}, nom
            assert publies[nom].reconciliants["charte"]["techno"] == "Héraldiste", nom
            assert publies[nom].presentation["reconciliants"] == publies[nom].reconciliants, nom
    if "video-revelation" in publies:
        assert set(publies["video-revelation"].reconciliants) == {"analyse", "culture"}
