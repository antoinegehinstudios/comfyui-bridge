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
from comfyui_bridge.core import reconciliant

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
TECHNIQUES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "techniques-exemples"
DONNEES = RACINE / "_data"

SOCLE = [("contrainte", "rendre"), ("direction", "rendre"), ("rendu", "rendre"), ("livraison", "composer"),
         ("conformite", "rendre")]
PLAN_STANDARD = SOCLE + [("constat", "constater"), ("constat_de_la_charte", "constater"), ("controle", "verifier")]
PLAN_SOCIAL = SOCLE + [("constat", "constater"), ("constat_du_message", "constater"),
                       ("constat_de_la_charte", "constater"), ("controle", "verifier")]
REGLAGES_DE_STYLE = ("cadrage", "lumiere", "palette", "ambiance")
COMMUNS = {"charte": "aucune", "prompt": "", "style": None, "cadrage": "libre", "lumiere": "libre", "palette": "libre",
           "ambiance": "libre", "width": 1080, "height": 1350, "seed": 71}
IMPOSE_PAR_LA_CHARTE = {"champ": "charte", "sauf": ["aucune"]}
# La direction reçoit les couleurs DOMINANTES (le colorway du nœud), et la zone calme du logo SEULEMENT quand le
# nœud pose un logo pendant l'image — une valeur conditionnelle : l'usage d'un fait, écrit chez le réalisateur.
ZONE_CALME_SI_LOGO_POSE = {"si": "$contrainte.recit.logo_fichier_pendant", "alors": "$contrainte.recit.logo_ancrage",
                           "sinon": ""}
# Le nom que le wordmark écrit ne s'ôte du message que si le logo est posé (vu le 2026-09-24 à 22:23 : « sans »
# logo, « Grabuge Fest » ôté du sous-message — la marque disparaissait de l'image). Depuis le réconciliant
# (2026-09-25), c'est ce que le nœud laisse au logo — la même règle, câblée une fois pour l'image et la vidéo.
NOM_OTE_SI_LOGO_POSE = "$contrainte.recit.texte_laisse_au_logo"
CE_QUE_LA_CHARTE_IMPOSE_A_LA_DIRECTION = {"1.charte": "$charte", "1.style_impose": "$contrainte.recit.positif",
                                          "1.palette_imposee": "$contrainte.recit.colorway_en",
                                          "1.negatif_impose": "$contrainte.recit.negatif",
                                          "1.zone_du_logo": ZONE_CALME_SI_LOGO_POSE}
LOGO_SOUS_UNE_CHARTE = {"type": "COMBO", "defaut": "selon le message", "options_depuis": {"menu": "logo_charte"},
                        "selon": {"champ": "charte", "sauf": ["aucune"]}, "libelle": "Logo de la charte",
                        "categorie": "charte"}
CONSTATS_DE_LA_CHARTE = ["la_charte_demandee_est_appliquee", "la_palette_de_la_charte_est_dans_la_consigne",
                         "la_palette_de_la_charte_est_tenue", "le_logo_impose_est_pose", "le_logo_impose_est_intact",
                         "le_logo_impose_tient_sa_taille", "les_regles_mesurables_de_la_charte_sont_tenues",
                         "les_interdits_de_la_charte_pesent", "aucune_image_de_reference_de_la_charte_n_est_attendue"]
TEXTE = {"message": "", "sous_message": "", "police": "Segoe UI Bold", "texte_position": "bas",
         "couleur_texte": "white", "bandeau": False, "taille_message": 0.075}
ENTREES_DE_LA_DIRECTION = {"1.style": "$style", "1.sujet": "$prompt", "1.cadrage": "$cadrage",
                           "1.lumiere": "$lumiere", "1.palette": "$palette", "1.ambiance": "$ambiance",
                           "1.largeur": "$width", "1.hauteur": "$height"}


# Les deux modes branchent le réconciliant « charte » : ce qui s'exécute est la chaîne DÉPLIÉE, et c'est elle
# que ces témoins épinglent (la source ne lit que des emplacements, `$charte.<rôle>`).
@pytest.fixture(scope="module")
def standard():
    return reconciliant.deplier(json.loads((EXEMPLES / "image-creation.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def social():
    return reconciliant.deplier(json.loads((EXEMPLES / "image-visuel-social.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def techniques():
    return {f.stem: noyau.lire_technique(json.loads(f.read_text(encoding="utf-8")), f.stem)
            for f in (TECHNIQUES / "rapide.json", TECHNIQUES / "soignee.json")}


def test_les_deux_modes_partagent_le_meme_socle(standard, social):
    assert [(e.id, e.genre) for e in noyau.lire(standard, "image-creation").etapes] == PLAN_STANDARD
    assert [(e.id, e.genre) for e in noyau.lire(social, "image-visuel-social").etapes] == PLAN_SOCIAL
    for brut in (standard, social):
        assert brut["livrable"] == "$livraison.livrable"
        etapes = {e["id"]: e for e in brut["etapes"]}
        contrainte, direction = etapes["contrainte"], etapes["direction"]["rendre"]
        rendu, livraison, conformite = etapes["rendu"]["rendre"], etapes["livraison"]["composer"], etapes["conformite"]
        # La charte se lit EN PREMIER, quand elle est nommée ; sans elle, son « sinon » rend les mêmes clés à vide.
        assert contrainte["quand"] == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}
        assert contrainte["rendre"]["workflow"] == "image-heraldiste"
        assert contrainte["rendre"]["inputs"]["1.charte"] == "$charte" and contrainte["rendre"]["inputs"]["1.prompt"] == "$prompt"
        # Le LOGO : un champ qui n'existe que sous une charte (selon « sauf »), juste après elle ; le nœud décide
        # (selon le message | avec | sans) et le dit ; la chaîne lit sa décision, jamais le fichier brut.
        assert list(brut["expose"])[:2] == ["charte", "logo"]
        assert {k: v for k, v in brut["expose"]["logo"].items() if k != "aide"} == LOGO_SOUS_UNE_CHARTE
        assert contrainte["rendre"]["inputs"]["1.logo"] == "$logo"
        sinon = contrainte["sinon"]["recit"]
        assert sinon["charte"] == "aucune" and sinon["tenue"] is True and sinon["appliquee"] is False
        for cle in ("positif", "couleurs_en", "colorway_en", "negatif", "logo_fichier", "logo_fichier_pendant",
                    "logo_ancrage", "logo_raison", "logo_attendu", "texture_fichier", "references_transmises", "fond",
                    "logo_texte"):
            assert cle in sinon, cle
        assert sinon["logo_fichier_pendant"] is None and sinon["logo_attendu"] == "aucun"
        assert direction["workflow"] == "image-direction"
        for cle, renvoi in {**ENTREES_DE_LA_DIRECTION, **CE_QUE_LA_CHARTE_IMPOSE_A_LA_DIRECTION}.items():
            assert direction["inputs"][cle] == renvoi, cle
        # Le rendu nomme un RÔLE, jamais un graphe ; la graine passe par sa liaison, la taille par la direction.
        assert rendu == {"role": "image", "technique": "$technique", "seed": "$seed"}
        assert (livraison["image"], livraison["largeur"], livraison["hauteur"]) == (
            "$rendu.livrable", "$width", "$height")
        # Le logo et la texture de la charte se posent à la livraison, avec ses mesures.
        # Le logo se pose quand le nœud l'a décidé (`logo_fichier_pendant`), et sa raison est dite dans tous les cas.
        logo = livraison["images"][0]
        assert logo == {"fichier": "$contrainte.recit.logo_fichier_pendant", "ancrage": "$contrainte.recit.logo_ancrage",
                        "largeur": "$contrainte.recit.logo_largeur", "marge": "$contrainte.recit.logo_marge",
                        "hauteur_min_px": "$contrainte.recit.logo_hauteur_min_px", "espace_min": "$contrainte.recit.logo_zone",
                        "raison": "$contrainte.recit.logo_raison"}
        assert livraison["texture"]["fichier"] == "$contrainte.recit.texture_fichier"
        # Le constat d'Héraldiste porte sur l'IMAGE livrée, une seule, par son chemin.
        assert conformite["quand"] == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}
        assert conformite["rendre"]["workflow"] == "video-heraldiste-conformite"
        # … et ne cherche le logo que là où la décision l'attend (partout, ou nulle part).
        assert conformite["rendre"]["inputs"] == {"1.video": "$livraison.livrable", "1.charte": "$charte", "1.images": 1,
                                                  "1.logo_attendu": "$contrainte.recit.logo_attendu"}
        assert conformite["sinon"]["recit"]["tenue"] is True and conformite["sinon"]["recit"]["regles_non_tenues"] == []


def test_les_champs_et_leurs_defauts(standard, social):
    defauts = {k: v.get("defaut") for k, v in standard["expose"].items()}
    assert defauts == {**COMMUNS, "logo": "selon le message", "style": "photographie", "technique": None}
    defauts = {k: v.get("defaut") for k, v in social["expose"].items()}
    assert defauts == {**COMMUNS, **TEXTE, "logo": "selon le message", "style": "affiche-minimaliste", "technique": None}
    for brut in (standard, social):
        expose = brut["expose"]
        # La charte EN PREMIER : « sinon c'est pas logique » (Antoine, 2026-09-24).
        assert list(expose)[0] == "charte" and expose["charte"]["categorie"] == "charte"
        assert expose["charte"]["options_depuis"] == {"menu": "charte"} and expose["charte"]["defaut"] == "aucune"
        # Ce que la charte supplante se GRISE sous elle (impose_par), jamais caché (selon = n'existe pas).
        assert expose["palette"]["impose_par"] == IMPOSE_PAR_LA_CHARTE and "selon" not in expose["palette"]
        for nom in ("style", "cadrage", "lumiere", "ambiance", "technique", "width", "height", "seed", "prompt"):
            assert "selon" not in expose[nom] and "impose_par" not in expose[nom], nom
        # La chaîne MANIFESTE le gabarit « creation » : elle est vérifiée contre lui.
        assert brut["gabarit"] == "creation"
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
    for nom in ("police", "couleur_texte", "bandeau"):
        assert social["expose"][nom]["impose_par"] == IMPOSE_PAR_LA_CHARTE and "selon" not in social["expose"][nom], nom
    for nom in ("message", "sous_message", "texte_position", "taille_message"):
        assert "selon" not in social["expose"][nom] and "impose_par" not in social["expose"][nom], nom


def test_les_techniques_de_ce_plan_tiennent_le_role_image_et_aucun_fantome(standard, social, techniques):
    """Le Turbo (cfg 1) ignore un négatif : il ne l'expose pas ; la soignée
    l'expose, avec le guidage, et les lit dans son graphe. Chaque réglage
    exposé par une technique est lu par son rôle."""
    assert techniques["rapide"].par_defaut is True and techniques["soignee"].par_defaut is False
    assert set(techniques["rapide"].champs) == {"steps"}
    assert set(techniques["soignee"].champs) == {"steps", "cfg", "negatif"}
    # Le négatif n'existe que sous la soignée (selon, calculé) ; sous une charte il est rempli par elle (impose_par).
    assert techniques["soignee"].champs["negatif"].impose_par == IMPOSE_PAR_LA_CHARTE
    assert techniques["rapide"].donnees["negatif"]["applique"] is False and techniques["rapide"].donnees["negatif"]["dit"]
    # La règle compte PARTOUT : sous la rapide, le cfg (que la soignée règle) est déclaré fixé, comme le négatif ;
    # rien d'autre ne manque à la rapide (les pas se règlent sous les deux), rien ne manque à la soignée.
    from comfyui_bridge.adapter import techniques as vues_t
    sous_rapide = {m["quoi"]: m for m in vues_t.manques(techniques["rapide"], techniques)}
    assert set(sous_rapide) == {"negatif", "cfg"} and all(m["detecte"] == "declare" for m in sous_rapide.values())
    assert sous_rapide["cfg"]["libelle"] == techniques["soignee"].champs["cfg"].libelle
    assert vues_t.manques(techniques["soignee"], techniques) == []
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
    etapes = {e["id"]: e for e in social["etapes"]}
    direction = etapes["direction"]["rendre"]["inputs"]
    assert direction["1.zone_de_texte"] == "$texte_position" and direction["1.texte_prevu"] == "$message"
    # La typographie vient de la contrainte : celle de la charte, ou — sans charte — celle qu'on a choisie (le « sinon »).
    contrainte = etapes["contrainte"]
    assert contrainte["rendre"]["inputs"]["1.police"] == "$police" and contrainte["rendre"]["inputs"]["1.couleur"] == "$couleur_texte"
    assert contrainte["sinon"]["recit"]["police"] == "$police" and contrainte["sinon"]["recit"]["couleur"] == "$couleur_texte"
    textes = etapes["livraison"]["composer"]["textes"]
    assert [t["texte"] for t in textes] == ["$message", "$sous_message"]
    assert all(t["police"] == "$contrainte.recit.police" and t["position"] == "$texte_position"
               and t["couleur"] == "$contrainte.recit.couleur" and t["fond"] == "$contrainte.recit.fond"
               and t["boite"] == "$bandeau" and t["laisser_au_logo"] == NOM_OTE_SI_LOGO_POSE for t in textes)
    # Sans logo posé, le nom de la marque reste dans le message ; le constat du texte du logo est alors sans objet.
    controle = next(x for x in etapes["constat_de_la_charte"]["constater"] if x["id"] == "ce_que_le_logo_ecrit_n_est_pas_reecrit")
    assert controle["valeur"] == {"si": "$contrainte.recit.logo_pose",
                                  "alors": "$conformite.recit.logo_texte_non_reecrit", "sinon": True}
    # Le récit tel que le nœud l'écrit (une image fixe : un logo posé l'est pendant l'image) : sans logo, rien
    # n'est laissé au logo ; posé, son nom l'est.
    sans_logo = {"contrainte": {"recit": {"logo_pose": False, "logo_fichier_pendant": None, "logo_texte": "Grabuge Fest",
                                          "texte_laisse_au_logo": None}},
                 "conformite": {"recit": {"logo_texte_non_reecrit": False}}}
    avec_logo = {"contrainte": {"recit": {"logo_pose": True, "logo_fichier_pendant": "E:/logo.png",
                                          "logo_texte": "Grabuge Fest", "texte_laisse_au_logo": "Grabuge Fest"}},
                 "conformite": {"recit": {"logo_texte_non_reecrit": False}}}
    assert not noyau.resoudre(textes[1]["laisser_au_logo"], {}, sans_logo)
    assert noyau.resoudre(textes[1]["laisser_au_logo"], {}, avec_logo) == "Grabuge Fest"
    assert noyau.resoudre(controle["valeur"], {}, sans_logo) is True
    assert noyau.resoudre(controle["valeur"], {}, avec_logo) is False
    assert textes[0]["taille"] == "$taille_message" and textes[1]["sous_le_precedent"] is True and "decalage" not in textes[1]
    # Le message est posé par la livraison, jamais confié au graphe de peinture ; la charte ne le reçoit que pour
    # DÉCIDER du logo (« un message est posé : la marque signe »), comme accroche et appel — jamais dans sa consigne.
    assert "$message" not in json.dumps(etapes["rendu"])
    entrees_charte = contrainte["rendre"]["inputs"]
    assert entrees_charte["1.prompt"] == "$prompt"
    assert {k for k, v in entrees_charte.items() if "message" in json.dumps(v)} == {"1.accroche", "1.appel"}
    constats = {c["id"]: c for c in etapes["constat"]["constater"]}
    assert constats["chaque_texte_demande_est_pose"]["attendu"] == "$livraison.textes_demandes"
    du_message = etapes["constat_du_message"]
    assert du_message["quand"] == "$message"
    assert du_message["constater"][0]["attendu"] == "$texte_position"


def test_ce_que_la_charte_impose_se_constate_et_ce_qu_elle_ne_peut_pas_imposer_se_dit(standard, social, techniques):
    """« Au vu de ce qu'impose la charte graphique, cela doit peser » : appliquée,
    palette dans la consigne et tenue sur l'image, logo posé / intact / à sa
    taille, règles mesurables ; et ce que ce modèle ne sait pas honorer (les
    interdits sous une technique sans guidage, les images de référence) est
    CONSTATÉ — jamais refusé, jamais tu."""
    for brut in (standard, social):
        etape = next(e for e in brut["etapes"] if e["id"] == "constat_de_la_charte")
        assert etape["quand"] == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}
        ids = [c["id"] for c in etape["constater"]]
        assert [i for i in ids if i in CONSTATS_DE_LA_CHARTE] == CONSTATS_DE_LA_CHARTE
        constats = {c["id"]: c for c in etape["constater"]}
        assert constats["les_interdits_de_la_charte_pesent"]["valeur"] == "$technique.negatif.applique"
        assert constats["aucune_image_de_reference_de_la_charte_n_est_attendue"]["attendu"] == []
        assert constats["la_palette_de_la_charte_est_tenue"]["attendu"] == "$conformite.recit.part_attendue"
        assert all(str(c.get("aide", "")).strip() for c in etape["constater"])
    assert "ce_que_le_logo_ecrit_n_est_pas_reecrit" in [c["id"] for c in next(
        e for e in social["etapes"] if e["id"] == "constat_de_la_charte")["constater"]]
    # Chaque technique dit si elle lit le négatif : c'est ce que le constat lit dans son fichier.
    assert techniques["rapide"].donnees["negatif"]["applique"] is False
    assert techniques["soignee"].donnees["negatif"]["applique"] is True


def test_la_taille_se_constate_et_seul_le_fichier_vide_refuse(standard, social):
    for brut in (standard, social):
        constats = {c["id"]: c for c in next(e for e in brut["etapes"] if e["id"] == "constat")["constater"]}
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
    assert rubriques[:2] == ["charte", "sujet"] and "style" in rubriques
    assert pathlib.Path(r["menus"]["charte"]["source_fichier"]["chemin"]).is_file()
    # Héraldiste expose des FAITS (colonne « faits » de son menu) ; l'USAGE — quel champ un fait impose,
    # sous quelle condition, en quels mots — est déclaré ICI, chez le réalisateur (2026-09-24).
    impose = r["menus"]["charte"]["source_fichier"]["impose"]
    assert impose["colonne"] == "faits"
    # la police des titres impose aussi celle de l'appel final de « Révéler une image » (même fait, même condition)
    assert set(impose["champs"]) == {"palette", "negatif", "police", "couleur_texte", "bandeau", "cta_police"}
    assert impose["champs"]["cta_police"] == impose["champs"]["police"]
    assert impose["champs"]["police"] == {"fait": "titres.famille", "si": "titres.fichier"}
    assert impose["champs"]["bandeau"]["fait"] == "titres.fond" and "{valeur}" in impose["champs"]["bandeau"]["dit"]
    assert all("fait" in u for u in impose["champs"].values())
    for nom, noeud, cles in (("image-heraldiste", "HeraldisteCharte", ("charte", "prompt", "police", "couleur")),
                             ("video-heraldiste-conformite", "HeraldisteConformite", ("video", "charte", "images"))):
        graphe = json.loads(pathlib.Path(r["workflows"][nom]["workflow"]).read_text(encoding="utf-8"))
        assert graphe["1"]["class_type"] == noeud and all(c in graphe["1"]["inputs"] for c in cles), nom
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


def test_le_visuel_social_dit_au_noeud_qu_un_message_est_pose(social):
    """Le nœud de la charte signe d'un logo une création qui porte un message (règle d'usage du 2026-09-24) : le
    visuel social lui envoie son message et son sous-message comme accroche et appel ; la création standard, qui
    n'en a pas, ne lui envoie rien de tel."""
    entrees = next(e for e in social["etapes"] if e["id"] == "contrainte")["rendre"]["inputs"]
    assert entrees["1.accroche"] == "$message" and entrees["1.appel"] == "$sous_message"
    standard = reconciliant.deplier(json.loads((EXEMPLES / "image-creation.json").read_text(encoding="utf-8")))
    entrees = next(e for e in standard["etapes"] if e["id"] == "contrainte")["rendre"]["inputs"]
    assert "1.accroche" not in entrees and "1.appel" not in entrees


def test_la_zone_calme_ne_se_demande_que_si_le_logo_est_pose_pendant_l_image(standard):
    """La direction lit la décision du nœud par une valeur conditionnelle : l'ancrage quand le logo est posé
    pendant l'image, rien sinon — une image d'ambiance ne garde pas une zone vide pour un logo absent."""
    zone = next(e for e in standard["etapes"] if e["id"] == "direction")["rendre"]["inputs"]["1.zone_du_logo"]
    pose = {"contrainte": {"recit": {"logo_fichier_pendant": "E:/logo.png", "logo_ancrage": "haut-centre"}}}
    absent = {"contrainte": {"recit": {"logo_fichier_pendant": None, "logo_ancrage": "haut-centre"}}}
    assert noyau.resoudre(zone, {}, pose) == "haut-centre"
    assert noyau.resoudre(zone, {}, absent) == ""


def test_les_choix_du_logo_disent_le_principe():
    """2026-09-25 : « “selon le message” et “sans” n'est pas assez parlant, choisis les bons factuels qui donnent le
    principe ». Les valeurs restent celles du nœud de la charte ; leurs libellés et leurs résumés vivent dans un menu
    nommé de la réconciliation, que lit le champ « logo » du réconciliant « charte » — de toute création qui le
    branche, image ou vidéo (2026-09-25) : les libellés parlent donc de « la création »."""
    r = json.loads((DONNEES / "reconciliation.local.json").read_text(encoding="utf-8"))
    menu = r["menus"]["logo_charte"]
    assert list(menu["libelles"]) == ["selon le message", "avec", "sans"]
    assert menu["libelles"]["selon le message"]["libelle"] == "Quand la création porte un message ou une promotion"
    assert menu["libelles"]["avec"]["libelle"].startswith("Toujours") and menu["libelles"]["sans"]["libelle"].startswith("Jamais")
    assert all(v["resume"] for v in menu["libelles"].values())
    assert "logo" not in r["menus"], "un menu au nom du champ retitrerait aussi le « logo » d'une autre chaîne"

