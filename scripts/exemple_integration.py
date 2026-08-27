"""Un flux englobant appelle la passerelle — sans jamais toucher à l'UI.

Parcours complet et vérifiable : découvrir ce qu'un workflow accepte, envoyer
ses paramètres aux IN, suivre le run, récupérer les livrables du OUT.

    python scripts/exemple_integration.py [nom_de_workflow]

Rien n'est supposé du catalogue : tout est lu au fil de l'eau. Chaque maillon
manquant fait échouer bruyamment — c'est le but d'un exemple d'intégration.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8077"
TERMINAL = {"succeeded", "failed", "cancelled"}


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def post(path: str, body: dict):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:               # RFC 7807
        problem = json.loads(exc.read().decode("utf-8", "replace"))
        raise SystemExit(f"refusé ({exc.code}) : {problem.get('title')} — {problem.get('detail')}")


def main() -> None:
    # 1. LE CATALOGUE — quels workflows ce service sait appeler, et lesquels
    #    tournent réellement ici (mémoire des problèmes rencontrés).
    catalogue = get("/v1/workflows")
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    lançables = [n for n, w in catalogue["workflows"].items() if w.get("runnable") is not False]
    if not lançables:
        raise SystemExit("aucun workflow lançable sur cet hôte")
    name = wanted or lançables[0]
    workflow = catalogue["workflows"][name]
    print(f"workflow      : {name} ({workflow['kind']})")

    # 2. LES IN — les noms à envoyer sont annoncés, pas devinés.
    champs = workflow["intent_fields"]
    print(f"IN acceptés   : {', '.join(champs)}")
    io = get(f"/v1/workflows/{name}/io")
    if io.get("described"):
        print(f"entrées brutes: {len(io['inputs'])} adressables en 'noeud.entrée'")

    # 3. L'APTITUDE — inutile de lancer ce qui est connu pour échouer ici.
    readiness = get(f"/v1/workflows/{name}/readiness")
    if not readiness["runnable"]:
        raise SystemExit(f"non lançable ici : {readiness.get('problem')} — {readiness.get('detail')}")

    # 4. L'INTENTION — seulement des champs annoncés.
    # `label` nomme la sortie côté hôte : c'est ainsi qu'un flux appelant
    # retrouve SES fichiers, un identifiant de job ne survivant pas au service.
    intention = {"workflow": name, "label": "integration-demo",
                 "prompt": "integration test: a paper lantern on dark water"}
    souhaits = {"width": 352, "height": 224, "fps": 12, "duration_s": 1, "batch": 1, "steps": 8}
    intention.update({k: v for k, v in souhaits.items() if k in champs})
    # Les bornes viennent du workflow, pas d'un chiffre à nous.
    for entry in (io.get("intent_inputs") or []):
        borne = " · ".join(f"{k} {entry[k]}" for k in ("min", "max") if k in entry)
        if borne:
            print(f"  {entry['field']:14} {entry.get('type','?'):8} {borne}")

    # 5. LE COÛT — annoncé avant de lancer, avec ce qui a été lu du graphe.
    devis = post("/v1/estimate", intention)
    estimation = devis.get("estimate")
    print(f"charge        : {devis.get('work')} ({devis.get('effective')})")
    print(f"estimation    : {estimation and estimation['seconds']} s"
          f" [{estimation and estimation['basis']}]")
    if devis.get("ignored"):
        print(f"NON transmis  : {', '.join(devis['ignored'])}")

    # 6. LE RUN — asynchrone : un identifiant, puis on suit.
    job = post("/v1/render", intention)
    print(f"job           : {job['id']}")

    debut = time.monotonic()
    while job["status"] not in TERMINAL:
        time.sleep(3)
        try:
            job = get(f"/v1/jobs/{job['id']}")
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            # Le service peut redémarrer sous les pieds d'un appelant : le job
            # vit en mémoire, le fichier non. On le retrouve par son label.
            print(f"  … service indisponible ({exc}) — reprise par le label")
            time.sleep(10)
            continue
        etat = job.get("engine_state") or {}
        if etat.get("state") == "pending":
            print(f"  … {etat['ahead']} tâche(s) devant", flush=True)
    print(f"statut        : {job['status']} en {round(time.monotonic() - debut)} s"
          f" (moteur : {job.get('duration_s')} s)")

    if job["status"] != "succeeded":
        raise SystemExit(f"échec : {json.dumps(job.get('problem'), ensure_ascii=False)[:300]}")

    # 7. LE OUT — les fichiers créés, là où ils sont sur l'hôte.
    print("livrables     :")
    for a in job["artifacts"]:
        existe = Path(a["path"]).exists()
        print(f"  [{a['kind']}] {a['path']}  {a.get('bytes')} o  "
              f"{'présent' if existe else 'ABSENT DU DISQUE'}  ·  {BASE}{a.get('url') or ''}")
        if not existe:
            raise SystemExit("un livrable annoncé n'existe pas sur le disque")


main()
