"""« CRÉER UNE IMAGE » — le témoin du flux de création maître (2026-09-24).

Antoine : « je veux à la mise au catalogue maestro la possibilité de créer des
images, avec paramètres, la même logique de templatisation/paramètres que
Révéler une image ; le flux de création propose toujours des paramètres
d'ajustement de style créatif, qui ne mentent pas ou ne sont pas des fonctions
fantômes ; un mode création standard, et un autre, création de visuel pour
média sociaux, type affiche, ou qui montre un message ».

Ce qui est épinglé ici : le même socle pour les deux modes (direction → rendu
par un RÔLE que la technique choisie tient → composer → constater → verifier),
les champs et leurs défauts, les deux techniques de ce plan (rapide par
défaut ; le négatif et le guidage n'existent que sous la soignée — le Turbo,
à cfg 1, les ignorerait), le message posé et non peint, la taille exacte
CONSTATÉE, la copie versionnée identique aux données du poste, et — sur ce
poste — les graphes, les liaisons et les poids qui existent. Aucun nom de flux
dans le code de la passerelle : tout ce mode est des DONNÉES.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
TECHNIQUES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "techniques-exemples"
DONNEES = RACINE / "_data"

SOCLE = [("direction", "rendre"), ("rendu", "rendre"), ("livraison", "composer")]
PLAN_STANDARD = SOCLE + [("constat", "constater"), ("controle", "verifier")]
PLAN_SOCIAL = SOCLE + [("constat", "constater"), ("constat_du_message", "constater"), ("controle", "verifier")]
REGLAGES_DE_STYLE = ("cadrage", "lumiere", "palette", "ambiance")
COMMUNS = {"prompt": "", "style": None, "cadrage": "libre", "lumiere": "libre", "palette": "libre",
           "ambiance": "libre", "width": 1080, "height": 1350, "seed": 71}
TEXTE = {"message": "", "sous_message": "", "police": "Segoe UI Bold", "texte_position": "bas",
         "couleur_texte": "white", "bandeau": False, "taille_message": 0.075}
ENTREES_DE_LA_DIRECTION = {"1.style": "$style", "1.sujet": "$prompt", "1.cadrage": "$cadrage",
                           "1.lumiere": "$lumiere", "1.palette": "$palette", "1.ambiance": "$ambiance",
                           "1.largeur": "$width", "1.hauteur": "$height"}


@pytest.fixture(scope="module")
def standard():
    return json.loads((EXEMPLES / "image-creation.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def social():
    return json.loads((EXEMPLES / "image-visuel-social.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def techniques():
    return {f.stem: noyau.lire_technique(json.loads(f.read_text(encoding="utf-8")), f.stem)
            for f in (TECHNIQUES / "rapide.json", TECHNIQUES / "soignee.json")}


def test_les_deux_modes_partagent_le_meme_socle(standard, social):
    assert [(e.id, e.genre) for e in noyau.lire(standard, "image-creation").etapes] == PLAN_STANDARD
    assert [(e.id, e.genre) for e in noyau.lire(social, "image-visuel-social").etapes] == PLAN_SOCIAL
    for brut in (standard, social):
        assert brut["livrable"] == "$livraison.livrable"
        direction, rendu, livraison = brut["etapes"][0]["rendre"], brut["etapes"][1]["rendre"], brut["etapes"][2]["composer"]
        assert direction["workflow"] == "image-direction"
        for cle, renvoi in ENTREES_DE_LA_DIRECTION.items():
            assert direction["inputs"][cle] == renvoi
        # Le rendu nomme un RÔLE, jamais un graphe ; la graine passe par sa liaison, la taille par la direction.
        assert rendu == {"role": "image", "technique": "$technique", "seed": "$seed"}
        assert (livraison["image"], livraison["largeur"], livraison["hauteur"]) == (
            "$rendu.livrable", "$width", "$height")


def test_les_champs_et_leurs_defauts(standard, social):
    defauts = {k: v.get("defaut") for k, v in standard["expose"].items()}
    assert defauts == {**COMMUNS, "style": "photographie", "technique": None}
    defauts = {k: v.get("defaut") for k, v in social["expose"].items()}
    assert defauts == {**COMMUNS, **TEXTE, "style": "affiche-minimaliste", "technique": None}
    for brut in (standard, social):
        expose = brut["expose"]
        assert expose["prompt"]["requis"] is True and expose["prompt"]["categorie"] == "sujet"
        assert expose["technique"]["options_depuis"] == {"techniques": True} and "defaut" not in expose["technique"]
        assert expose["style"]["options_depuis"] == {"menu": "style_image"}
        for nom in REGLAGES_DE_STYLE:
            assert expose[nom]["options_depuis"] == {"menu": nom + "_image"} and expose[nom]["categorie"] == "style"
        assert "step" not in expose["width"] and "step" not in expose["height"]
        for nom, champ in expose.items():
            assert champ.get("categorie") and str(champ.get("aide", "")).strip(), nom
    assert social["expose"]["bandeau"]["type"] == "BOOLEAN"
    assert social["expose"]["texte_position"]["options"] == ["bas", "centre", "haut"]


def test_les_techniques_de_ce_plan_tiennent_le_role_image_et_aucun_fantome(standard, social, techniques):
    """Le Turbo (cfg 1) ignore un négatif : il ne l'expose pas ; la soignée
    l'expose, avec le guidage, et les lit dans son graphe. Chaque réglage
    exposé par une technique est lu par son rôle."""
    assert techniques["rapide"].par_defaut is True and techniques["soignee"].par_defaut is False
    assert set(techniques["rapide"].champs) == {"steps"}
    assert set(techniques["soignee"].champs) == {"steps", "cfg", "negatif"}
    assert techniques["rapide"].champs["steps"].defaut == 8 and techniques["soignee"].champs["steps"].defaut == 25
    assert techniques["soignee"].champs["cfg"].defaut == 4.0
    for nom, t in techniques.items():
        assert set(t.roles) == {"image"}, nom
        entrees = t.roles["image"].inputs
        assert entrees["27.text"] == "$direction.recit.prompt"
        assert entrees["13.width"] == "$direction.recit.largeur_rendu"
        assert entrees["13.height"] == "$direction.recit.hauteur_rendu"
        assert entrees["3.steps"] == "$steps"
        lus = {r.split(".", 1)[0] for r in noyau.renvois(entrees)}
        assert set(t.champs) <= lus, nom
    assert techniques["soignee"].roles["image"].inputs["40.string_b"] == "$negatif"
    assert techniques["soignee"].roles["image"].inputs["3.cfg"] == "$cfg"
    assert techniques["rapide"].roles["image"].workflow == "image-z-image-turbo"
    assert techniques["soignee"].roles["image"].workflow == "image-z-image"
    for brut, nom in ((standard, "image-creation"), (social, "image-visuel-social")):
        chaine = noyau.lire(brut, nom)
        noyau.verifier_techniques(chaine, noyau.techniques_pour(chaine, techniques))
        assert noyau.technique_choisie(chaine, {}, techniques).nom == "rapide"


def test_le_message_est_pose_pas_peint_et_la_zone_demandee(social):
    direction = social["etapes"][0]["rendre"]["inputs"]
    assert direction["1.zone_de_texte"] == "$texte_position" and direction["1.texte_prevu"] == "$message"
    textes = social["etapes"][2]["composer"]["textes"]
    assert [t["texte"] for t in textes] == ["$message", "$sous_message"]
    assert all(t["police"] == "$police" and t["position"] == "$texte_position"
               and t["couleur"] == "$couleur_texte" and t["boite"] == "$bandeau" for t in textes)
    assert textes[0]["taille"] == "$taille_message" and textes[1]["decalage"] == 0.1
    # Le message est posé par la livraison, jamais confié au graphe de peinture.
    assert "$message" not in json.dumps(social["etapes"][1])
    constats = {c["id"]: c for c in social["etapes"][3]["constater"]}
    assert constats["chaque_texte_demande_est_pose"]["attendu"] == "$livraison.textes_demandes"
    du_message = social["etapes"][4]
    assert du_message["quand"] == "$message"
    assert du_message["constater"][0]["attendu"] == "$texte_position"


def test_la_taille_se_constate_et_seul_le_fichier_vide_refuse(standard, social):
    for brut in (standard, social):
        constats = {c["id"]: c for c in brut["etapes"][3]["constater"]}
        assert constats["la_largeur_demandee_est_tenue"]["valeur"] == "$livraison.mesure.width"
        assert constats["la_largeur_demandee_est_tenue"]["attendu"] == "$width"
        assert constats["la_hauteur_demandee_est_tenue"]["attendu"] == "$height"
        agrandi = constats["l_image_n_est_pas_agrandie_a_la_livraison"]
        assert (agrandi["valeur"], agrandi["op"], agrandi["attendu"]) == (
            "$direction.recit.agrandissement_a_la_livraison", "lte", 1.0)
        assert all(str(c.get("aide", "")).strip() for c in constats.values())
        controle = brut["etapes"][-1]["verifier"]
        assert [c["id"] for c in controle] == ["livrable_pese"]


def test_les_copies_versionnees_sont_celles_du_poste():
    if not (DONNEES / "chaines").is_dir():
        pytest.skip("pas de _data sur ce poste")
    for nom in ("image-creation", "image-visuel-social"):
        donnee = DONNEES / "chaines" / f"{nom}.json"
        assert donnee.read_text(encoding="utf-8") == (EXEMPLES / f"{nom}.json").read_text(encoding="utf-8"), nom
    for nom in ("rapide", "soignee"):
        donnee = DONNEES / "techniques" / f"{nom}.json"
        assert donnee.read_text(encoding="utf-8") == (TECHNIQUES / f"{nom}.json").read_text(encoding="utf-8"), nom


def test_sur_ce_poste_les_modes_sont_publies_et_leurs_graphes_tiennent():
    reconciliation = DONNEES / "reconciliation.local.json"
    if not reconciliation.is_file():
        pytest.skip("pas de _data sur ce poste")
    r = json.loads(reconciliation.read_text(encoding="utf-8"))
    assert r["categories"]["creer-une-image"]["ordre"] == 0
    rubriques = [c["valeur"] for c in r["categories_de_champs"]]
    assert rubriques[0] == "sujet" and "style" in rubriques
    assert {x["valeur"] for x in r["formats"]["resolutions"]} >= {"carre-1080", "4-5-1080"}
    for nom in ("style_image", "cadrage_image", "lumiere_image", "palette_image", "ambiance_image"):
        assert pathlib.Path(r["menus"][nom]["source_fichier"]["chemin"]).is_file(), nom
    for nom, ordre in (("image-creation", 1), ("image-visuel-social", 2)):
        mode = r["workflows"][nom]
        assert mode["categorie"] == "creer-une-image" and mode["ordre"] == ordre and mode["kind"] == "image"
        assert pathlib.Path(mode["chaine"]).is_file()
    direction = r["workflows"]["image-direction"]
    assert direction["bindings"] == {} and direction["kind"] == "image"
    graphe = json.loads(pathlib.Path(direction["workflow"]).read_text(encoding="utf-8"))
    assert graphe["1"]["class_type"] == "DirectionDImage"
    for cle in ENTREES_DE_LA_DIRECTION:
        assert cle.split(".", 1)[1] in graphe["1"]["inputs"], cle
    modeles = pathlib.Path("E:/Comfy-Desktop/ComfyUI-Shared/models")
    for nom, poids, negatif in (("image-z-image-turbo", "z_image_turbo_int8_convrot.safetensors", "ConditioningZeroOut"),
                                ("image-z-image", "z_image_int8_convrot.safetensors", "CLIPTextEncode")):
        entree = r["workflows"][nom]
        graphe = json.loads(pathlib.Path(entree["workflow"]).read_text(encoding="utf-8"))
        assert graphe["28"]["inputs"]["unet_name"] == poids
        assert graphe["30"]["inputs"]["type"] == "lumina2" and graphe["3"]["class_type"] == "KSampler"
        assert graphe[graphe["3"]["inputs"]["negative"][0]]["class_type"] == negatif
        for champ, liaison in entree["bindings"].items():
            assert liaison["input"] in graphe[liaison["node"]]["inputs"], (nom, champ)
        for fichier in (poids, "qwen_3_4b.safetensors", "ae.safetensors"):
            dossier = {"qwen_3_4b.safetensors": "text_encoders", "ae.safetensors": "vae"}.get(fichier, "diffusion_models")
            assert (modeles / dossier / fichier).is_file(), fichier
    # Le Turbo ne lit aucun négatif ; la soignée le joint au négatif du style avant de l'encoder.
    turbo = json.loads(pathlib.Path(r["workflows"]["image-z-image-turbo"]["workflow"]).read_text(encoding="utf-8"))
    assert turbo["3"]["inputs"]["cfg"] == 1.0
    base = json.loads(pathlib.Path(r["workflows"]["image-z-image"]["workflow"]).read_text(encoding="utf-8"))
    assert base["40"]["class_type"] == "StringConcatenate" and base["7"]["inputs"]["text"] == ["40", 0]
