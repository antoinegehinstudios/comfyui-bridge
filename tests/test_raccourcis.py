"""Les raccourcis d'un mode : un ensemble de réglages enregistré, et son aperçu.

Éprouvés là où ils vivent — par l'API, sur la passerelle d'essai de
`test_chaines_api` (un catalogue de chaînes inventées, un backend qui écrit de
VRAIES vidéos, des menus déclarés). Refaire ici un atelier à part l'aurait fait
diverger au premier changement de contrat ; les seuls tests sans app sont ceux
du stockage lui-même, qui n'a pas besoin d'un serveur pour être vrai.
"""

import json
import pathlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from comfyui_bridge.adapter import raccourcis  # noqa: E402
from test_chaines_api import SANS_FFMPEG, _job, atelier  # noqa: E402,F401


def _base(client) -> pathlib.Path:
    return pathlib.Path(client.app.state.container.settings.hermes_db).parent


def _poser(client, mode: str, **corps) -> dict:
    r = client.post(f"/v1/workflows/{mode}/raccourcis", json=corps)
    assert r.status_code == 201, r.text
    return r.json()


# -- la vitrine ----------------------------------------------------------------


def test_le_catalogue_publie_une_liste_de_raccourcis_meme_vide(atelier):
    """Un lanceur bâtit son groupe « mode + ses raccourcis » sur cette clé : sans
    elle, il devrait demander mode par mode pour découvrir qu'il n'y a rien."""
    d = atelier.get("/v1/workflows").json()["workflows"]
    assert d["chaine-simple"]["raccourcis"] == []        # une chaîne
    assert d["sd15-txt2img"]["raccourcis"] == []         # …et un graphe


# -- enregistrer ---------------------------------------------------------------


@SANS_FFMPEG
def test_une_livraison_devient_un_raccourci_avec_son_apercu(atelier):
    """Le geste que le lanceur relaie après un run réussi : « garde ça ». Les
    réglages viennent de la demande du run, et l'aperçu de ce qu'il a livré —
    un lanceur ne fabrique pas d'image, il désigne."""
    job = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "chaine-recollee", "secondes": 2, "label": "joint"}))
    assert job["status"] == "succeeded", job.get("problem")

    vue = _poser(atelier, "chaine-recollee", job_id=job["id"],
                 titre="Deux clips, à l'aise", resume="Un peu plus long.")
    assert vue["id"] == "deux-clips-a-l-aise"
    assert vue["workflow"] == "chaine-recollee" and vue["job_id"] == job["id"]
    # La demande du run, sans ce que la passerelle possède elle-même.
    assert vue["valeurs"] == {"secondes": 2.0}
    assert vue["cree_le"].endswith("+00:00") and vue["ordre"] == 100
    # Ce qui DIFFÈRE du mode, habillé : « 2 s », pas « secondes=2.0 ».
    assert vue["ecarts"] == [{"champ": "secondes", "libelle": "Durée d'un clip",
                              "valeur": 2.0, "libelle_valeur": "2 s"}]

    assert vue["apercu_url"] == f"/v1/workflows/chaine-recollee/raccourcis/{vue['id']}/apercu"
    g = atelier.get(vue["apercu_url"])
    assert g.status_code == 200
    # WebP animé quand cet ffmpeg sait l'écrire, GIF sinon — et le type annoncé
    # est celui du fichier servi.
    if g.headers["content-type"].startswith("image/webp"):
        assert g.content[:4] == b"RIFF" and g.content[8:12] == b"WEBP"
    else:
        assert g.content[:6] in (b"GIF87a", b"GIF89a")

    # Le catalogue le montre sous SON mode, et sous lui seul.
    d = atelier.get("/v1/workflows").json()["workflows"]
    assert [r["id"] for r in d["chaine-recollee"]["raccourcis"]] == [vue["id"]]
    assert d["chaine-simple"]["raccourcis"] == []
    # Rien de partiel ne traîne : fiche et aperçu sont posés de côté puis
    # remplacés d'un coup (une fiche relue en cours d'écriture serait tronquée).
    dossier = _base(atelier) / "raccourcis" / "chaine-recollee"
    assert not [f for f in dossier.iterdir() if ".part" in f.name]


def test_un_raccourci_garde_les_sources_de_sa_livraison(atelier):
    """Un raccourci enregistré depuis une livraison rejoue CETTE livraison :
    ses pièces jointes sont ses sources, à part de ses réglages. Sans elles, le
    lanceur ouvrait le formulaire sur la dernière image déposée dans la session
    — celle d'un autre mode, parfois — et c'est elle qui partait (2026-09-20,
    Antoine : « ce n'est pas la bonne source qui est enregistrée, mais la
    dernière produite »)."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos",
                                                         "image_2": "la-piece.png"}))
    assert job["status"] == "succeeded", job.get("problem")
    vue = _poser(atelier, "chaine-piece-au-repos", job_id=job["id"], titre="Avec sa pièce")
    assert vue["sources"] == {"image_2": "la-piece.png"}
    assert "image_2" not in vue["valeurs"]                # une source n'est pas un réglage
    assert vue["ecarts"] == []                            # …et ne fait pas un écart
    assert "perime" not in vue
    # Relu seul et listé sous le mode : les mêmes sources.
    assert atelier.get(f"/v1/workflows/chaine-piece-au-repos/raccourcis/{vue['id']}"
                       ).json()["sources"] == {"image_2": "la-piece.png"}
    liste = atelier.get("/v1/workflows").json()["workflows"]["chaine-piece-au-repos"]["raccourcis"]
    assert [r["sources"] for r in liste] == [{"image_2": "la-piece.png"}]

    # Une livraison sans pièce jointe : aucune source — la clé est là, vide.
    nu = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos"}))
    assert _poser(atelier, "chaine-piece-au-repos", job_id=nu["id"], titre="Sans pièce"
                  )["sources"] == {}

    # Le corps recouvre : une source nommée, validée par le mode (une pièce
    # jointe qu'il expose, un nom de fichier) ; vide, elle est retirée.
    autre = _poser(atelier, "chaine-piece-au-repos", job_id=job["id"], titre="Autre pièce",
                   sources={"image_2": "une-autre.png"})
    assert autre["sources"] == {"image_2": "une-autre.png"}
    detachee = _poser(atelier, "chaine-piece-au-repos", job_id=job["id"], titre="Sans image",
                      sources={"image_2": ""})
    assert detachee["sources"] == {}
    refus = atelier.post("/v1/workflows/chaine-piece-au-repos/raccourcis",
                         json={"titre": "Pas une pièce", "job_id": job["id"],
                               "sources": {"largeur": "x.png"}})
    assert refus.status_code == 422 and "largeur" in refus.json()["detail"]
    assert "image_2" in refus.json()["detail"]            # ce que le mode expose est nommé

    # À la modification : une nouvelle livraison apporte ses sources, et
    # « sources » remplace tout.
    r = atelier.put(f"/v1/workflows/chaine-piece-au-repos/raccourcis/{vue['id']}",
                    json={"job_id": nu["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["sources"] == {} and r.json()["job_id"] == nu["id"]
    r = atelier.put(f"/v1/workflows/chaine-piece-au-repos/raccourcis/{vue['id']}",
                    json={"sources": {"image_2": "reprise.png"}})
    assert r.status_code == 200 and r.json()["sources"] == {"image_2": "reprise.png"}
    assert atelier.put(f"/v1/workflows/chaine-piece-au-repos/raccourcis/{vue['id']}",
                       json={"sources": {"image_9": "x.png"}}).status_code == 422

    # Une fiche d'avant les sources n'en a pas : publiée avec la clé, vide.
    raccourcis.ecrire(_base(atelier), "chaine-piece-au-repos", {
        "id": "d-avant", "workflow": "chaine-piece-au-repos", "titre": "D'avant",
        "valeurs": {}, "ordre": 100})
    assert atelier.get("/v1/workflows/chaine-piece-au-repos/raccourcis/d-avant"
                       ).json()["sources"] == {}
    # Une source dont le mode n'expose plus la pièce jointe : publié PÉRIMÉ,
    # nommée — jamais tu.
    raccourcis.ecrire(_base(atelier), "chaine-piece-au-repos", {
        "id": "piece-disparue", "workflow": "chaine-piece-au-repos", "titre": "Pièce disparue",
        "valeurs": {}, "sources": {"image_9": "x.png"}, "ordre": 100})
    perime = atelier.get("/v1/workflows/chaine-piece-au-repos/raccourcis/piece-disparue"
                         ).json()["perime"]
    assert perime["champs"] == ["image_9"]
    assert "« image_9 » : le mode n'expose plus cette pièce jointe" in perime["raison"]


def test_au_demarrage_une_fiche_d_avant_les_sources_est_completee(atelier):
    """Une fiche écrite par la passerelle d'AVANT les sources (2026-09-20) n'a
    pas la clé : au démarrage, elle reprend, une fois, les pièces jointes de sa
    livraison — `{}` sans livraison, ou quand la livraison a quitté le magasin
    ou le mode le catalogue, et c'est dit sur /v1/recovered. Rien de l'ancienne
    méthode ne reste sur le disque ; une fiche déjà complète n'est pas relue."""
    from fastapi.testclient import TestClient
    from comfyui_bridge.api.main import create_app
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos",
                                                         "image_2": "la-piece.png"}))
    assert job["status"] == "succeeded", job.get("problem")
    base = _base(atelier)
    commun = {"workflow": "chaine-piece-au-repos", "valeurs": {}, "ordre": 100,
              "cree_le": "2026-09-19T10:00:00+00:00"}
    raccourcis.ecrire(base, "chaine-piece-au-repos",
                      {**commun, "id": "d-avant-avec-livraison", "titre": "Avec", "job_id": job["id"]})
    raccourcis.ecrire(base, "chaine-piece-au-repos",
                      {**commun, "id": "d-avant-sans-livraison", "titre": "Sans", "job_id": None})
    raccourcis.ecrire(base, "chaine-piece-au-repos",
                      {**commun, "id": "d-avant-livraison-perdue", "titre": "Perdue", "job_id": "jamais-vu"})
    raccourcis.ecrire(base, "chaine-piece-au-repos",
                      {**commun, "id": "deja-complete", "titre": "Complète", "job_id": job["id"],
                       "sources": {"image_2": "gardee.png"}})
    raccourcis.ecrire(base, "mode-disparu",
                      {**commun, "workflow": "mode-disparu", "id": "d-un-mode-disparu",
                       "titre": "Disparu", "job_id": job["id"]})

    # La même passerelle, redémarrée sur les mêmes données.
    with TestClient(create_app(atelier.app.state.container.settings)) as relancee:
        faits = {f["id"]: f for f in relancee.get("/v1/recovered").json()["raccourcis_completes"]}
        assert faits["d-avant-avec-livraison"]["sources"] == {"image_2": "la-piece.png"}
        assert faits["d-avant-avec-livraison"]["raison"].startswith("sources reprises de la livraison")
        assert faits["d-avant-sans-livraison"]["sources"] == {}
        assert faits["d-avant-sans-livraison"]["raison"] == "sans livraison : aucune source"
        assert faits["d-avant-livraison-perdue"]["sources"] == {}
        assert "a quitté le magasin" in faits["d-avant-livraison-perdue"]["raison"]
        assert faits["d-un-mode-disparu"]["sources"] == {}
        assert faits["d-un-mode-disparu"]["workflow"] == "mode-disparu"
        assert "deja-complete" not in faits
        # …et la vue publie ce que le disque porte désormais.
        assert relancee.get("/v1/workflows/chaine-piece-au-repos/raccourcis/d-avant-avec-livraison"
                            ).json()["sources"] == {"image_2": "la-piece.png"}
    # Sur le disque : la clé est là, une fois, et ce qui était complet est intact.
    for ident, attendu in (("d-avant-avec-livraison", {"image_2": "la-piece.png"}),
                           ("d-avant-sans-livraison", {}), ("d-avant-livraison-perdue", {}),
                           ("deja-complete", {"image_2": "gardee.png"})):
        assert raccourcis.lire(base, "chaine-piece-au-repos", ident)["sources"] == attendu, ident
    assert raccourcis.lire(base, "mode-disparu", "d-un-mode-disparu")["sources"] == {}
    assert raccourcis.sans_sources(base, "chaine-piece-au-repos") == []
    assert raccourcis.modes(base) == ["chaine-piece-au-repos", "mode-disparu"]


def test_un_raccourci_s_enregistre_sans_livraison(atelier):
    """Régler sans lancer : on enregistre le réglage, pas le résultat. Sans
    livraison, aucune adresse d'aperçu n'est promise — une vignette absente
    ferait une image cassée par carte."""
    vue = _poser(atelier, "chaine-simple", titre="Large et second",
                 valeurs={"largeur": 96, "mode": "b"})
    assert "apercu_url" not in vue and vue["job_id"] is None
    assert vue["valeurs"] == {"largeur": 96, "mode": "b"} and vue["sources"] == {}
    ecarts = {e["champ"]: e for e in vue["ecarts"]}
    assert ecarts["largeur"]["libelle"] == "Largeur"
    assert ecarts["largeur"]["libelle_valeur"] == "96 px"       # l'unité du champ
    assert ecarts["mode"]["libelle"] == "Mode"
    assert ecarts["mode"]["libelle_valeur"] == "b"              # aucun libellé déclaré
    # « seuil » n'a pas bougé : un raccourci ne liste pas les quinze réglages
    # qu'il n'a pas changés.
    assert "seuil" not in ecarts
    assert atelier.get(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}/apercu"
                       ).status_code == 404


def test_un_menu_declare_habille_la_valeur_d_un_ecart(atelier):
    """« lente » ne se lit pas ; « Contemplation lente » oui. Le libellé vient du
    fournisseur du menu, jamais recopié dans la fiche : recopié, il vieillirait
    au premier renommage."""
    vue = _poser(atelier, "chaine-au-menu", titre="La lente",
                 valeurs={"structure": "lente"})
    assert vue["ecarts"] == [{"champ": "structure", "libelle": "Structure du récit",
                              "valeur": "lente", "libelle_valeur": "Contemplation lente"}]


def test_un_graphe_enregistre_aussi_ses_reglages(atelier):
    """Un mode n'est pas forcément une chaîne : le même geste vaut pour un
    graphe, validé contre ce que LUI accepte."""
    vue = _poser(atelier, "sd15-txt2img", titre="Petit format",
                 valeurs={"width": 128})
    assert vue["valeurs"] == {"width": 128}
    assert vue["ecarts"][0]["champ"] == "width"
    assert vue["ecarts"][0]["libelle_valeur"] == "128 px"
    # Un nom que le graphe ne pilote pas : refusé, nommé. (« latent_batch », lui,
    # EST une entrée de ce graphe — c'est le corps plat d'une intention qui ne
    # l'accepte pas, pas le mode.)
    refus = atelier.post("/v1/workflows/sd15-txt2img/raccourcis",
                         json={"titre": "Hors contrat", "valeurs": {"profondeur": 4}})
    assert refus.status_code == 422 and "profondeur" in refus.json()["detail"]


def test_la_passerelle_fait_autorite_sur_ce_qu_on_enregistre(atelier):
    """Ce que le mode refuserait au lancement, il le refuse à l'enregistrement :
    sinon un raccourci gardait une valeur hors bornes et n'échouait qu'au
    moment de lancer, longtemps après avoir été nommé."""
    inconnu = atelier.post("/v1/workflows/chaine-simple/raccourcis",
                           json={"titre": "Faute", "valeurs": {"profondeur": 3}})
    assert inconnu.status_code == 422 and "profondeur" in inconnu.json()["detail"]

    hors = atelier.post("/v1/workflows/chaine-simple/raccourcis",
                        json={"titre": "Trop large", "valeurs": {"largeur": 9999}})
    assert hors.status_code == 422
    assert hors.json()["field"] == "largeur" and hors.json()["libelle"] == "Largeur"

    vide = atelier.post("/v1/workflows/chaine-simple/raccourcis",
                        json={"titre": "  ", "valeurs": {"largeur": 96}})
    assert vide.status_code == 422 and vide.json()["field"] == "titre"

    sans_rien = atelier.post("/v1/workflows/chaine-simple/raccourcis",
                             json={"titre": "Ni l'un ni l'autre"})
    assert sans_rien.status_code == 422

    absent = atelier.post("/v1/workflows/chaine-simple/raccourcis",
                          json={"titre": "Run fantôme", "job_id": "jamais-vu"})
    assert absent.status_code == 404

    # Une livraison d'un AUTRE mode : les deux sont nommés, sinon la validation
    # refusait ensuite champ par champ sans jamais dire la vraie cause.
    ailleurs = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-simple"}))
    croise = atelier.post("/v1/workflows/chaine-au-menu/raccourcis",
                          json={"titre": "Pas le bon", "job_id": ailleurs["id"]})
    assert croise.status_code == 422
    assert "chaine-simple" in croise.json()["detail"]
    assert "chaine-au-menu" in croise.json()["detail"]


def test_deux_raccourcis_du_meme_titre_ne_s_ecrasent_pas(atelier):
    """Deux essais du même nom sont deux essais : le second prend « -2 »."""
    premier = _poser(atelier, "chaine-simple", titre="Mon réglage",
                     valeurs={"largeur": 96})
    second = _poser(atelier, "chaine-simple", titre="Mon réglage",
                    valeurs={"largeur": 128})
    assert (premier["id"], second["id"]) == ("mon-reglage", "mon-reglage-2")
    liste = atelier.get("/v1/workflows/chaine-simple/raccourcis").json()["raccourcis"]
    assert [r["valeurs"]["largeur"] for r in liste] == [96, 128]


# -- modifier, retirer ---------------------------------------------------------


def test_modifier_un_raccourci_garde_son_adresse(atelier):
    """L'identifiant est l'adresse que le lanceur a en main : renommer ne la
    change pas. Les valeurs, elles, REMPLACENT tout — fusionnées, elles
    auraient gardé un réglage que l'utilisateur venait d'effacer."""
    vue = _poser(atelier, "chaine-simple", titre="À revoir",
                 valeurs={"largeur": 96, "mode": "b"}, resume="Un premier jet.")
    r = atelier.put(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}",
                    json={"titre": "Revu et corrigé", "valeurs": {"largeur": 128}})
    assert r.status_code == 200, r.text
    apres = r.json()
    assert apres["id"] == vue["id"] and apres["titre"] == "Revu et corrigé"
    assert apres["valeurs"] == {"largeur": 128}
    assert apres["resume"] == "Un premier jet."        # non nommé, non touché
    assert [e["champ"] for e in apres["ecarts"]] == ["largeur"]
    assert atelier.get(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}"
                       ).json()["titre"] == "Revu et corrigé"

    refus = atelier.put(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}",
                        json={"valeurs": {"profondeur": 3}})
    assert refus.status_code == 422 and "profondeur" in refus.json()["detail"]
    assert atelier.put("/v1/workflows/chaine-simple/raccourcis/jamais-ecrit",
                       json={"titre": "x"}).status_code == 404


def test_retirer_un_raccourci_le_retire_partout(atelier):
    """Retiré, il ne doit plus être ni lisible, ni listé par la vitrine. Retirer
    ce qui n'existe pas n'est pas une erreur : c'est déjà l'état demandé."""
    vue = _poser(atelier, "chaine-simple", titre="Éphémère", valeurs={"largeur": 96})
    assert vue["id"] == "ephemere"
    r = atelier.delete(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}")
    assert r.status_code == 200 and r.json()["removed"] is True
    perdu = atelier.get(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}")
    # Un 404 qui se distingue d'un mode inconnu (400) : le mode est bon, c'est
    # le réglage enregistré qui manque.
    assert perdu.status_code == 404
    assert perdu.json()["type"].endswith("/raccourci-not-found")
    assert atelier.get("/v1/workflows").json()["workflows"]["chaine-simple"]["raccourcis"] == []
    assert atelier.delete(f"/v1/workflows/chaine-simple/raccourcis/{vue['id']}"
                          ).json()["removed"] is False


# -- le stockage, sans app -----------------------------------------------------


def test_l_identifiant_se_lit_dans_l_adresse_et_ne_s_ecrase_pas():
    """Tiré du titre, parce qu'un numéro n'aurait rien dit à qui ouvre le
    dossier ; sans accent, parce que « Sépia » s'écrivait « S%C3%A9pia »."""
    assert raccourcis.identifiant("Sépia au trait sec, à la chandelle") \
        == "sepia-au-trait-sec-a-la-chandelle"
    assert raccourcis.identifiant("  ¿¡ !!  ") == "raccourci"
    # Coupé à 40 caractères, sans laisser de tiret pendu au bout.
    long = raccourcis.identifiant("Un titre " + "très " * 20 + "long")
    assert len(long) <= 40 and not long.endswith("-")
    assert raccourcis.identifiant("Mon réglage", ["mon-reglage"]) == "mon-reglage-2"
    assert raccourcis.identifiant("Mon réglage",
                                  ["mon-reglage", "mon-reglage-2"]) == "mon-reglage-3"


def test_la_demande_d_un_run_perd_ce_qui_n_est_pas_un_reglage():
    """Un raccourci enregistre des RÉGLAGES, pas une requête : le mode visé et
    l'étiquette de sortie appartiennent à la passerelle, et la pièce jointe
    n'est pas un réglage — c'est une SOURCE, gardée à part."""
    demande = {"workflow": "un-mode", "label": "essai", "kind": "video", "inputs": {"7.x": 1},
               "constraints": [], "media": {"image": "a.png"}, "image": "a.png",
               "image_2": "  ", "duration_s": 45, "fond": "sepia"}
    garde = raccourcis.filtrer_demande(demande, medias=["image", "image_2"])
    assert garde == {"duration_s": 45, "fond": "sepia"}
    # Les sources : les pièces jointes NOMMÉES, par champ ; une pièce au repos
    # (vide) n'en est pas une, un réglage non plus.
    assert raccourcis.sources_de(demande, medias=["image", "image_2"]) == {"image": "a.png"}
    assert raccourcis.sources_de(demande, medias=[]) == {}
    assert raccourcis.sources_de(None, medias=["image"]) == {}


def test_les_ecarts_ne_disent_que_ce_qui_change():
    """Lister les quinze réglages d'un raccourci ne dirait rien ; ce qui le
    distingue, c'est ce qui diffère du mode. Le défaut déclaré (« 1 ») et la
    même valeur typée (1.0) sont le MÊME réglage."""
    decrit = {
        "duree": {"libelle": "Durée", "unite": "s", "libelles_valeurs": {}},
        "fond": {"libelle": "Fond de départ", "unite": None,
                 "libelles_valeurs": {"sepia": "Sépia"}},
        "boucle": {"libelle": "En boucle", "unite": None, "libelles_valeurs": {}},
    }
    lignes = raccourcis.ecarts(
        {"duree": 12.0, "fond": "sepia", "boucle": True, "tenue": 1.0},
        {"duree": 45, "fond": "washi", "boucle": False, "tenue": 1},
        lambda champ: decrit.get(champ, {}))
    assert lignes == [
        {"champ": "duree", "libelle": "Durée", "valeur": 12.0, "libelle_valeur": "12 s"},
        {"champ": "fond", "libelle": "Fond de départ", "valeur": "sepia",
         "libelle_valeur": "Sépia"},
        {"champ": "boucle", "libelle": "En boucle", "valeur": True,
         "libelle_valeur": "oui"},
    ]
    # Un champ sans description reste nommé par sa clé, jamais tu.
    seul = raccourcis.ecarts({"inconnu": 3}, {}, lambda champ: {})
    assert seul == [{"champ": "inconnu", "libelle": "inconnu", "valeur": 3,
                     "libelle_valeur": "3"}]


def test_une_fiche_s_ecrit_d_un_coup_et_se_relit(tmp_path):
    """Écrite en place, une fiche relue pendant sa réécriture serait tronquée —
    c'est déjà arrivé à l'aperçu animé d'un mode (0 octet servi en 200)."""
    fiche = {"id": "second", "workflow": "un-mode", "titre": "Le second",
             "valeurs": {"fond": "sépia"}, "ordre": 100}
    chemin = raccourcis.ecrire(tmp_path, "un-mode", fiche)
    assert chemin == tmp_path / "raccourcis" / "un-mode" / "second.json"
    assert "sépia" in chemin.read_text(encoding="utf-8")       # écrit lisible, pas en é
    assert json.loads(chemin.read_text(encoding="utf-8")) == fiche
    assert raccourcis.lire(tmp_path, "un-mode", "second") == fiche
    assert raccourcis.lire(tmp_path, "un-mode", "jamais-ecrit") is None
    assert not [f for f in chemin.parent.iterdir() if ".part" in f.name]

    raccourcis.ecrire(tmp_path, "un-mode", {"id": "premier", "titre": "Le premier",
                                            "ordre": 1})
    # Le rang déclaré d'abord, puis le titre.
    assert [f["id"] for f in raccourcis.lister(tmp_path, "un-mode")] == ["premier", "second"]

    # Retiré, il l'est avec son aperçu : laissé derrière, celui-ci réapparaîtrait
    # sous le prochain raccourci qui reprendrait le même identifiant.
    (raccourcis.cible_apercu(tmp_path, "un-mode", "second")).with_suffix(".webp") \
        .write_bytes(b"RIFF....WEBP")
    assert raccourcis.apercu_fichier(tmp_path, "un-mode", "second")[1] == "image/webp"
    assert raccourcis.retirer(tmp_path, "un-mode", "second") is True
    assert raccourcis.apercu_fichier(tmp_path, "un-mode", "second") is None
    assert raccourcis.retirer(tmp_path, "un-mode", "second") is False


# -- périmé, publié périmé -----------------------------------------------------


def test_un_raccourci_qui_a_vieilli_est_publie_perime_avec_sa_raison(atelier):
    """Un raccourci vieillit sans qu'on y touche : un champ que le mode
    n'expose plus, une valeur sortie du menu. Le retirer effacerait ce que
    l'utilisateur avait nommé ; le publier tel quel le laisserait échouer au
    lancement sans un mot (2026-09-19 : trois sur cinq partaient en 422). Il est
    publié PÉRIMÉ — ses champs, la raison — et son lancement tel quel reste
    refusé ; celui qui tient n'a pas la clé."""
    base = _base(atelier)
    raccourcis.ecrire(base, "chaine-simple", {
        "id": "d-un-autre-age", "workflow": "chaine-simple", "titre": "D'un autre âge",
        "valeurs": {"largeur": 96, "profondeur": 3, "mode": "z"}, "ordre": 100})
    raccourcis.ecrire(base, "chaine-simple", {
        "id": "qui-tient", "workflow": "chaine-simple", "titre": "Qui tient",
        "valeurs": {"largeur": 96}, "ordre": 100})
    raccourcis.ecrire(base, "sd15-txt2img", {
        "id": "trop-profond", "workflow": "sd15-txt2img", "titre": "Trop profond",
        "valeurs": {"width": 128, "profondeur": 4}, "ordre": 100})
    vus = {r["id"]: r for r in atelier.get("/v1/workflows").json()
           ["workflows"]["chaine-simple"]["raccourcis"]}
    assert "perime" not in vus["qui-tient"]
    perime = vus["d-un-autre-age"]["perime"]
    assert perime["champs"] == ["mode", "profondeur"]
    assert "« profondeur » : le mode ne l'expose plus" in perime["raison"]
    assert "Mode (mode) : 'z' n'est pas au menu" in perime["raison"]
    # La même chose, lue seule.
    seul = atelier.get("/v1/workflows/chaine-simple/raccourcis/d-un-autre-age").json()
    assert seul["perime"] == perime
    # …et son lancement tel quel reste un refus, nommé.
    refus = atelier.post("/v1/render", json={"workflow": "chaine-simple",
                                             **vus["d-un-autre-age"]["valeurs"]})
    assert refus.status_code == 422 and "profondeur" in refus.json()["detail"]
    # Un graphe juge les siens de même.
    graphe = atelier.get("/v1/workflows").json()["workflows"]["sd15-txt2img"]["raccourcis"][0]
    assert graphe["perime"]["champs"] == ["profondeur"]
    assert "profondeur" in graphe["perime"]["raison"]


def test_la_forme_d_un_raccourci_perime_est_ecrite_une_fois():
    """Rien quand rien n'est reproché ; sinon les champs, sans doublon et dans
    l'ordre, et les raisons à la suite."""
    assert raccourcis.perime([]) is None
    assert raccourcis.perime([("fond", "« fond » : n'est pas un réglage de la technique voile"),
                              ("conduite", "« conduite » : le mode ne l'expose plus"),
                              ("fond", "encore")]) == {
        "champs": ["conduite", "fond"],
        "raison": "« fond » : n'est pas un réglage de la technique voile ; "
                  "« conduite » : le mode ne l'expose plus ; encore"}
