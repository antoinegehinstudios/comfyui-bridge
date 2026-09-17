"""Workflow catalog — loads the reconciliation file and resolves named workflows.

The reconciliation file (``reconciliation.json``) DECLARES the workflows a
pipeline may call, keyed by name:

    {
      "version": 1,
      "default": "sd15-txt2img",
      "workflows": {
        "sd15-txt2img": {
          "kind": "image",
          "workflow": "workflow_template.json",
          "defaults": { "steps": 20, "cfg": 7.0, "width": 768, "height": 768 },
          "limits":   { "max_signature": 1310720 },
          "bindings": { "prompt": { "node": "6", "input": "text" }, ... }
        },
        "z-image-turbo": { ... }
      }
    }

A caller selects a workflow by name; the adapter resolves it here. Adding a new
model = adding an entry (workflow file + bindings + defaults) — no code change.
This object is BOTH the domain-facing ``WorkflowRegistry`` (abstract profiles)
and the adapter-facing source of graphs/bindings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re

from ..core.blocs import deplier
from ..core.errors import IntentValidationError, WorkflowMappingError
from ..core.workflow import WorkflowProfile
from . import assembleur, autobind, bibliotheque
from .mapping import Binding

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


_RACINE_BLOCS: Path | None = None


def _racine_des_blocs() -> Path:
    """Où vivent les blocs réutilisables : à côté des workflows déclarés."""
    return _RACINE_BLOCS or Path(__file__).resolve().parents[2] / "_data"


def est_montage(brut: Any) -> bool:
    """Un gabarit de montage, par opposition à un graphe API tel quel."""
    return isinstance(brut, dict) and bool(brut.get("assemblage"))


def _monter(brut: dict[str, Any], params: dict[str, Any] | None, nom: str
            ) -> tuple[dict[str, Any], dict[tuple[str, int], dict[str, str]],
                       dict[tuple[str, int], dict[str, str]]]:
    """Déplier un montage et le recoudre, avec sa table de numéros.

    Sans paramètres — le cas des pages qui DÉCRIVENT un workflow sans le lancer
    — le montage est déplié sur son exemple. Sans exemple, une recette resterait
    indescriptible : on refuse plutôt que de rendre un graphe vide.
    """
    montage = brut.get("montage")
    if not isinstance(montage, list):
        raise WorkflowMappingError(f"workflow {nom!r} : un montage a besoin de « montage »")
    constantes = dict(brut.get("constantes") or {})
    # Les constantes sont visibles de la boucle COMME des paramètres : le même
    # nombre sert au nœud qui fixe la longueur d'un bloc et au compte de tours.
    # Un paramètre du demandeur l'emporte, sans quoi une constante empêcherait
    # de piloter ce qu'elle nomme.
    valeurs = dict(constantes)
    valeurs.update(brut.get("exemple") or {})
    valeurs.update({k: v for k, v in (params or {}).items() if v is not None})
    # Les morceaux portent le préfixe du RUN (« cortex/<label>_bloc_003 ») : deux
    # runs du même montage n'écrivent plus au même endroit, et le demandeur
    # retrouve ses blocs sous son nom. La marque déclarée (le dernier segment de
    # la constante) reste dans le nom : c'est elle que le recollage cherche.
    nom_const = _constante_des_morceaux(brut)
    prefixe_run = (params or {}).get("filename_prefix")
    if nom_const and prefixe_run:
        marque = str(constantes.get(nom_const, "bloc")).replace("\\", "/").rsplit("/", 1)[-1]
        valeurs[nom_const] = f"{prefixe_run}_{marque}"
    if not valeurs and not brut.get("exemple"):
        raise WorkflowMappingError(
            f"workflow {nom!r} : montage sans « exemple », impossible à décrire "
            f"tant qu'aucun paramètre n'est fourni")
    # Les blocs reutilisables sont resolus AVANT le depliage : le reste de la
    # chaine ne voit que des fragments ordinaires.
    montage = bibliotheque.resoudre(montage, bibliotheque.charger(_racine_des_blocs()))
    fragments = deplier(montage, valeurs)
    # « blocs » : les fragments qui produisent un morceau — chacun connaît alors
    # son rang et le total, pour dire au récit où il en est.
    return (assembleur.assembler(fragments, valeurs, blocs=brut.get("blocs")),
            assembleur.numeroter(fragments),
            assembleur.sorties_nommees(fragments))


def _constante_des_morceaux(brut: dict[str, Any]) -> str | None:
    """Le nom de la constante que « livrable.morceaux » désigne, s'il y en a une."""
    marque = (brut.get("livrable") or {}).get("morceaux")
    if isinstance(marque, str) and marque.startswith(assembleur.CONSTANTE):
        return marque[len(assembleur.CONSTANTE):]
    return None


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    kind: str
    workflow_path: Path
    bindings: dict[str, Binding]
    defaults: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    # Maintained manifest metadata (managed/extracted workflows):
    source: str | None = None                       # ComfyUI workflow it came from
    source_hash: str | None = None                  # source hash at extraction (MAJ detection)
    extracted_at: str | None = None                 # WHEN this analysis was made
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # {models, node_types}

    carried: dict[str, Any] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    # Paramètres qui pilotent un MONTAGE sans viser de nœud : la durée demandée
    # décide du nombre de blocs, elle ne s'écrit dans aucun d'eux.
    pilote: tuple[str, ...] = ()
    # Les valeurs sur lesquelles un montage se DÉCRIT quand personne n'a encore
    # rien demandé (sa clé « exemple »). C'est la seule valeur connue d'un
    # paramètre pilote : le moteur ne le déclare pas, il ne vise aucun nœud.
    exemple: dict[str, Any] = field(default_factory=dict)
    # Une CHAÎNE au lieu d'un graphe : le fichier qui décrit l'enchaînement.
    # Une entrée porte l'un OU l'autre — jamais les deux.
    chaine_path: Path | None = None
    # Les champs qu'une chaîne expose : son contrat d'entrée, comme les
    # liaisons le sont pour un graphe.
    exposes: tuple[str, ...] = ()
    # La vitrine. Lue sur TOUTE entrée : un lanceur montre par catégorie et par
    # titre, jamais par nom technique. Sans `categorie`, l'entrée reste interne
    # (étape de chaîne, utilitaire) et n'est pas publiée.
    titre: str = ""
    description: str = ""
    categorie: str | None = None
    ordre: int = 100
    # Ce qu'il faut SAVOIR pour remplir un champ, quand le nom du champ ne
    # suffit pas (« le sujet s'écrit décor | temps un | temps deux »). Déclaré à
    # l'entrée, rendu par /io sur le champ concerné : écrit dans un client, ce
    # savoir mourait avec ce client.
    aides: dict[str, str] = field(default_factory=dict)

    @property
    def est_chaine(self) -> bool:
        return self.chaine_path is not None

    @property
    def presentation(self) -> dict[str, Any]:
        return {"titre": self.titre or self.name, "resume": self.description,
                "categorie": self.categorie, "ordre": self.ordre,
                "publie": self.categorie is not None}

    @property
    def profile(self) -> WorkflowProfile:
        from .neutral import has_neutral
        if self.est_chaine:
            # Une chaîne ne lie aucun nœud : ce qu'elle reçoit, elle le déclare.
            return WorkflowProfile(self.name, self.kind, dict(self.defaults),
                                   dict(self.limits), {}, tuple(self.exposes), ())
        return WorkflowProfile(self.name, self.kind, dict(self.defaults),
                               dict(self.limits), dict(self.carried),
                               tuple(sorted(set(self.bindings) | set(self.pilote))),
                               # Media inputs for which a neutral element exists:
                               # for the others, saying "neutral was sent" would
                               # be false and the workflow's own content is used.
                               # Par CATÉGORIE : `image_2` a le même neutre que
                               # `image`, et une entrée audio n'en a aucun.
                               tuple(sorted(p for p in self.bindings if has_neutral(p))))


class WorkflowCatalog:
    # (méthodes de lecture plus bas ; l'ingestion et le retrait encadrent le cycle
    #  de vie d'un extrait : ce qui s'ajoute doit pouvoir se retirer.)
    def __init__(self, default: str, specs: dict[str, WorkflowSpec],
                 workflows_dir: Path | None = None,
                 categories: dict[str, Any] | None = None,
                 menus: dict[str, Any] | None = None,
                 formats: dict[str, Any] | None = None,
                 techniques: dict[str, Any] | None = None) -> None:
        self._default = default
        self._specs = specs
        self._workflows_dir = Path(workflows_dir) if workflows_dir else None
        self._templates: dict[str, dict[str, Any]] = {}
        self._chaines: dict[str, Any] = {}
        # Noms servis par une entrée déclarée alors qu'un graphe enregistré
        # porte le même : ce qui est masqué doit pouvoir être dit.
        self.shadowed: tuple[str, ...] = ()
        # Des titres déclarés pour un workflow qui n'existe plus : renommé ou
        # retiré. Un habillage qui n'habille rien doit se voir.
        self.vitrines_orphelines: tuple[str, ...] = ()
        # La vitrine, déclarée au fichier de réconciliation : les catégories qui
        # rangent les entrées, et les libellés des menus. Des DONNÉES, pas du
        # code : une liste de styles écrite dans un client se serait figée le
        # jour où le fournisseur en a ajouté un.
        self.categories: dict[str, Any] = dict(categories or {})
        self.menus: dict[str, Any] = dict(menus or {})
        # Le vocabulaire des FORMATS (orientations, résolutions). Deux listes,
        # vides quand rien n'est déclaré : un lanceur ne montre alors pas les
        # listes, et largeur/hauteur restent des champs ordinaires. Aucun défaut
        # ici — le défaut d'un mode est celui de SES champs.
        self.formats: dict[str, Any] = dict(formats or {"orientations": [], "resolutions": []})
        # Le vocabulaire des CATÉGORIES DE CHAMPS : dans quel ordre et sous quel
        # titre un lanceur regroupe les réglages qu'un mode expose. Déclaré une
        # fois ; chaque champ nomme la sienne (`categorie`). Vide quand rien
        # n'est déclaré : les champs restent une liste.
        self.categories_de_champs: list[dict[str, Any]] = []
        # Les TECHNIQUES déclarées sur cette machine : ce qui tient les rôles
        # d'une chaîne. Lues une fois avec le catalogue, comme les chaînes le
        # sont à leur entrée — une technique de plus est un fichier de plus.
        self._techniques: dict[str, Any] = dict(techniques or {})

    # -- maintained manifest (provenance + dependencies) ----------------------

    def _index_path(self) -> Path | None:
        return (self._workflows_dir / "index.json") if self._workflows_dir else None

    def _read_index(self) -> dict[str, Any]:
        p = self._index_path()
        if p and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("workflows", {})
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _write_index(self, workflows: dict[str, Any]) -> None:
        p = self._index_path()
        if p:
            p.write_text(json.dumps({"version": 1, "workflows": workflows}, indent=2), encoding="utf-8")

    def manifest(self, name: str) -> dict[str, Any]:
        return self._read_index().get(name, {})

    # -- self-service ingestion ----------------------------------------------

    def register(self, name: str, graph: dict[str, Any], source: str | None = None,
                 source_hash: str | None = None, extracted_at: str | None = None,
                 titles: dict[str, str] | None = None) -> WorkflowSpec:
        """Ingest a ComfyUI API graph: auto-bind it, derive its dependencies,
        record its provenance, persist it, make it callable.

        No hand-written bindings — ``autobind`` derives them from the graph. The
        drop folder + ``index.json`` are the maintained reconciliation/dependency
        manifest: name, kind, source, source hash (for update detection), and
        the models + node types the workflow needs.
        """
        if not autobind.looks_like_api_graph(graph):
            raise WorkflowMappingError(
                "not a ComfyUI API graph — in ComfyUI use 'Export (API)', not a plain save "
                "(UI format with 'nodes'/'links' is not accepted)"
            )
        name = _SAFE_NAME.sub("-", name).strip("-") or "workflow"
        meta = {
            "source": source,
            "source_hash": source_hash,
            "extracted_at": extracted_at,
            # The author's own node names, lost by the API export. Kept because
            # they are what ComfyUI Desktop shows and what makes the discovered
            # inputs readable ("Duration", not "PrimitiveInt · value").
            "titles": dict(titles or {}),
        }
        if self._workflows_dir is not None:
            self._workflows_dir.mkdir(parents=True, exist_ok=True)
            path = self._workflows_dir / f"{name}.json"
            path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
            index = self._read_index()
            index[name] = meta
            self._write_index(index)
        else:
            path = Path(f"{name}.json")
        spec = _spec_from_graph(name, path, graph, meta)
        self._specs[name] = spec
        self._templates[name] = graph
        return spec

    def unregister(self, name: str) -> dict[str, Any]:
        """Retirer un extrait du catalogue — l'inverse de ``register``.

        Ce qui s'ingère doit pouvoir se retirer : une source supprimée dans
        ComfyUI laissait sinon un extrait orphelin, toujours appelable et que
        rien ne pouvait sortir de la liste. Seul ce que NOUS avons écrit est
        effacé ; une entrée déclarée à la main dans le fichier de réconciliation
        n'est pas à nous et est refusée.
        """
        spec = self._specs.get(name)
        if spec is None:
            raise IntentValidationError(f"unknown workflow {name!r}", available=self.names())
        # Ce qui est à nous, c'est le graphe que NOUS avons écrit dans le dossier
        # d'ingestion. La provenance ComfyUI ne dit pas ça : un graphe importé
        # par `POST /v1/workflows` n'en a pas, et refusait donc de se retirer
        # alors qu'il avait bien été ingéré ici.
        fichier = (self._workflows_dir / f"{name}.json") if self._workflows_dir else None
        if fichier is None or spec.workflow_path.resolve() != fichier.resolve():
            raise WorkflowMappingError(
                f"{name!r} n'a pas été ingéré ici : il vient du fichier de "
                f"réconciliation et ne peut pas être retiré par l'API", workflow=name)
        index = self._read_index()
        index.pop(name, None)
        self._write_index(index)
        fichier.unlink(missing_ok=True)
        self._specs.pop(name, None)
        self._templates.pop(name, None)
        return {"removed": name, "graph_file": str(fichier), "source": spec.source}

    # -- WorkflowRegistry port (domain-facing) --------------------------------

    def get_profile(self, name: str | None) -> WorkflowProfile:
        return self.get_spec(name).profile

    def names(self) -> list[str]:
        return list(self._specs)

    def default_name(self) -> str:
        return self._default

    # -- adapter-facing -------------------------------------------------------

    def get_spec(self, name: str | None) -> WorkflowSpec:
        key = name or self._default
        spec = self._specs.get(key)
        if spec is None:
            raise IntentValidationError(
                f"unknown workflow {key!r}", available=self.names(),
            )
        return spec

    def chaine(self, spec: WorkflowSpec):
        """La chaîne d'une entrée, lue et validée une fois.

        La lecture REFUSE ce qui ne tient pas (renvoi vers l'aval, champ
        inconnu) : découvert à l'exécution, un renvoi faux faisait échouer la
        chaîne après avoir dépensé les étapes d'avant.
        """
        from ..core import chaine as noyau
        if not spec.est_chaine:
            raise WorkflowMappingError(f"{spec.name!r} n'est pas une chaîne", workflow=spec.name)
        if spec.name not in self._chaines:
            try:
                brut = json.loads(Path(spec.chaine_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowMappingError(
                    f"chaîne {spec.name!r} : impossible de lire {spec.chaine_path} : {exc}"
                ) from exc
            self._chaines[spec.name] = noyau.lire(brut, spec.name)
        return self._chaines[spec.name]

    def techniques(self) -> dict[str, Any]:
        """Les techniques déclarées, par leur nom."""
        return dict(self._techniques)

    def technique(self, nom: str | None):
        """UNE technique par son nom, ou None. Un nom inconnu n'est pas une
        panne : c'est une valeur que la validation d'un champ refusera en le
        nommant, là où l'appelant comprendra."""
        return self._techniques.get(str(nom)) if nom else None

    def load_template(self, spec: WorkflowSpec,
                      params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.monter(spec, params)[0]

    def monter(self, spec: WorkflowSpec, params: dict[str, Any] | None = None
               ) -> tuple[dict[str, Any], dict[str, Binding]]:
        """Le graphe à envoyer, et les liaisons qui le visent.

        Un gabarit ordinaire est rendu tel quel, avec ses liaisons déclarées.
        Un gabarit de MONTAGE (``"assemblage": 1``) est déplié d'abord : le
        nombre de blocs sort des paramètres, donc les numéros de nœuds n'existent
        qu'après le dépliage, et les liaisons doivent être traduites vers eux.
        """
        if spec.est_chaine:
            # Une chaîne n'a pas de graphe : elle enchaîne des runs, dont chacun
            # a le sien. Rendre un graphe vide ferait croire à un workflow muet.
            raise WorkflowMappingError(
                f"{spec.name!r} est une chaîne : elle n'a pas de graphe à monter "
                f"(voir ses étapes)", workflow=spec.name)
        brut = self._brut(spec)
        if not est_montage(brut):
            return brut, spec.bindings
        graphe, table, sorties = _monter(brut, params, spec.name)
        # Une liaison déclarée vise un RÔLE (« $commun.prompt ») quand le
        # fragment en déclare un : le numéro de nœud n'a pas à être recopié dans
        # la réconciliation, où il vieillirait sans que personne le voie.
        liaisons = {
            k: (Binding(node=assembleur.adresse(b.node, table, sorties), input=b.input)
                if b.node.startswith(assembleur.PREFIXE) else b)
            for k, b in spec.bindings.items()
        }
        return graphe, liaisons

    def livrable(self, spec: WorkflowSpec) -> dict[str, Any]:
        """Ce que la recette dit de son livrable — notamment qu'il est en morceaux.

        Un montage a memoire constante ecrit un fichier par bloc ; ce que le
        demandeur a commande reste UNE video. La recette est seule a savoir
        laquelle des deux choses elle produit.
        """
        if spec.est_chaine:
            return {}                    # une chaîne nomme son livrable elle-même
        brut = self._brut(spec)
        if not est_montage(brut):
            return {}
        constantes = dict(brut.get("constantes") or {})
        # Le préfixe des morceaux est déclaré UNE fois : le montage l'écrit dans
        # les noms de fichiers, le recollage le relit ici. Deux copies auraient
        # fini par se contredire, et le livrable aurait joint les mauvais bouts.
        return {k: (constantes.get(v[len(assembleur.CONSTANTE):], v)
                    if isinstance(v, str) and v.startswith(assembleur.CONSTANTE) else v)
                for k, v in (brut.get("livrable") or {}).items()}

    def _brut(self, spec: WorkflowSpec) -> dict[str, Any]:
        if spec.name not in self._templates:
            try:
                self._templates[spec.name] = json.loads(spec.workflow_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowMappingError(
                    f"workflow {spec.name!r}: cannot read {spec.workflow_path}: {exc}"
                ) from exc
        return self._templates[spec.name]


def _carried_media(graph: dict[str, Any], bindings: dict[str, Binding]) -> dict[str, Any]:
    """Media values already sitting in the graph, per bound media param.

    Toutes les entrées média, pas une liste figée de trois noms : un workflow à
    quatre images en porte quatre, et taire les trois dernières laissait leur
    contenu partir dans le résultat sans que rien ne le dise.
    """
    from ..core.intention import is_media_param
    out: dict[str, Any] = {}
    for param, b in bindings.items():
        if not is_media_param(param):
            continue
        val = ((graph.get(b.node) or {}).get("inputs") or {}).get(b.input)
        if isinstance(val, str) and val:
            out[param] = val
    return out


def _spec_from_graph(name: str, path: Path, graph: dict[str, Any],
                     meta: dict[str, Any] | None = None) -> WorkflowSpec:
    meta = meta or {}
    titles = dict(meta.get("titles") or {})
    bindings = autobind.derive_bindings(graph, titles)
    return WorkflowSpec(
        name=name,
        # Derived from the graph on every read — a stored copy would go stale
        # the moment the file is replaced in the drop folder.
        kind=autobind.infer_kind(graph),
        workflow_path=Path(path),
        bindings=bindings,
        # No defaults: the graph already holds its author's values. Copying
        # them here would be a second source that can disagree with it.
        defaults={},
        limits={},
        source=meta.get("source"),
        source_hash=meta.get("source_hash"),
        extracted_at=meta.get("extracted_at"),
        # Always read from the graph (the single source); never a stored copy.
        dependencies=autobind.derive_dependencies(graph),
        carried=_carried_media(graph, bindings),
        titles=titles,
    )


def build_injection(catalog: "WorkflowCatalog", plan) -> dict[str, Any]:
    """Resolve a plan's workflow and inject its params — the shared preview.

    Used by both backends' ``preview`` so the graph view is identical whether
    the engine is the CLI or the HTTP API.
    """
    from .injector import apply_overrides, inject

    spec = catalog.get_spec(plan.workflow)
    template, liaisons = catalog.monter(spec, plan.params)
    graph = apply_overrides(inject(template, liaisons, plan.params), plan.overrides)
    applied = [
        {
            "param": key, "node": b.node, "input": b.input,
            "class_type": (template.get(b.node) or {}).get("class_type"),
            "value": plan.params[key],
        }
        for key, b in liaisons.items()
        if key in plan.params
    ]
    touched = {a["node"] for a in applied}
    nodes = [
        {"id": nid, "class_type": node.get("class_type"), "touched": nid in touched}
        for nid, node in graph.items()
    ]
    return {
        "workflow_name": spec.name,
        "workflow_source": str(spec.workflow_path),
        "bindings_applied": applied,
        "nodes": nodes,
        "workflow": graph,
    }


def _exemple(path: Path) -> dict[str, Any]:
    """Les valeurs d'exemple d'un gabarit de montage, lues dans le gabarit.

    Un paramètre pilote n'est déclaré par personne d'autre : ni le moteur (il ne
    vise aucun nœud), ni les défauts du catalogue (qui parlent en champs
    d'intention). Sans elles, le formulaire d'un montage annonçait un champ sans
    la moindre valeur.
    """
    try:
        brut = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not est_montage(brut):
        return {}
    return dict(brut.get("exemple") or {})


def _pilotes(path: Path) -> tuple[str, ...]:
    """Ce qui pilote le montage d'un gabarit, lu dans le gabarit lui-même."""
    from ..core.blocs import parametres_pilotes
    try:
        brut = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not est_montage(brut):
        return ()
    pilotes = set(parametres_pilotes(brut.get("montage") or []))
    # Le nom de sortie nomme les morceaux : il pilote le montage, et « label »
    # est donc un champ que ce workflow reçoit — pas un réglage ignoré.
    if _constante_des_morceaux(brut):
        pilotes.add("filename_prefix")
    return tuple(sorted(pilotes))


def _graph_of(path: Path) -> dict[str, Any]:
    try:
        brut = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if est_montage(brut):
        # Un montage n'est un graphe qu'une fois déplié. Ce qui le lit ici — le
        # média que le graphe porte déjà, ses dépendances — se lit donc sur son
        # exemple. Un montage impossible à déplier ne fait pas échouer le
        # chargement du catalogue : il se signalera au moment de servir.
        try:
            return _monter(brut, None, str(path))[0]
        except Exception:
            return {}
    return brut


def _spec_de_chaine(name: str, entry: dict[str, Any], catalogue: Path,
                    vitrine: dict[str, Any],
                    techniques: dict[str, Any] | None = None) -> WorkflowSpec:
    """Une entrée qui déclare une CHAÎNE au lieu d'un graphe.

    Ce qu'elle reçoit vient de la rubrique « expose » de la chaîne, lue ici même
    : sans cela, le catalogue annoncerait une entrée sans aucun champ, et un
    formulaire construit dessus serait vide. Les réglages des TECHNIQUES en font
    partie — un appelant les envoie à la racine du corps comme les autres, et
    sans eux « fond » repartait en « champ inconnu ».
    """
    from ..core import chaine as noyau
    chemin = Path(entry["chaine"])
    if not chemin.is_absolute():
        chemin = (catalogue.parent / chemin).resolve()
    expose: tuple[str, ...] = ()
    defauts: dict[str, Any] = {}
    try:
        lue = noyau.lire(json.loads(chemin.read_text(encoding="utf-8")), name)
        noyau.verifier_techniques(lue, techniques or {})
        expose = tuple(noyau.champs_admis(lue, techniques))
        # Les défauts publiés sont ceux du plan ET de la technique par DÉFAUT :
        # c'est ce qu'un formulaire ouvre, et ce contre quoi un écart se juge.
        defauts = noyau.defauts(lue, noyau.technique_choisie(lue, {}, techniques))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(
            f"chaîne {name!r} : impossible de lire {chemin} : {exc}") from exc
    return WorkflowSpec(
        name=name,
        kind=str(entry.get("kind", "video")),
        # Le fichier de la chaîne EST la source de cette entrée : c'est lui que
        # les vues qui parlent de « source du workflow » doivent montrer.
        workflow_path=chemin,
        chaine_path=chemin,
        bindings={},
        exposes=expose,
        defaults={**defauts, **dict(entry.get("defaults", {}))},
        limits=dict(entry.get("limits", {})),
        **vitrine,
    )


# Les deux seules rubriques du vocabulaire des formats. Une liste blanche
# plutôt qu'un filtre : ce qui n'est pas nommé ici ne part pas au réseau, et la
# clé `_lire_moi` qui documente le bloc dans le fichier en est écartée du même
# coup (le `_` est la convention de documentation de ce fichier).
RUBRIQUES_DE_FORMAT: tuple[str, ...] = ("orientations", "resolutions")


def _formats(brut: Any, path: Path) -> dict[str, list[dict[str, Any]]]:
    """Le vocabulaire des formats, lu et VÉRIFIÉ à la lecture.

    Servi tel quel à un lanceur, qui traduit le choix en ``width``/``height``
    (portrait ⇒ la largeur est le petit côté). Une résolution sans valeur ou
    sans ses deux côtés entiers est donc inutilisable : la servir tronquée
    aurait donné une liste où un choix n'écrit rien, et le lanceur aurait eu
    l'air en panne. Elle est refusée ici, nommée.
    """
    if not isinstance(brut, dict):
        return {rubrique: [] for rubrique in RUBRIQUES_DE_FORMAT}
    lu: dict[str, list[dict[str, Any]]] = {}
    for rubrique in RUBRIQUES_DE_FORMAT:
        lignes = brut.get(rubrique)
        lu[rubrique] = []
        if lignes is None:
            continue
        if not isinstance(lignes, list):
            raise WorkflowMappingError(f"{path}: formats.{rubrique} doit être une liste")
        for rang, ligne in enumerate(lignes, 1):
            if not isinstance(ligne, dict):
                raise WorkflowMappingError(
                    f"{path}: formats.{rubrique} n°{rang} n'est pas un objet")
            garde = {k: v for k, v in ligne.items() if not str(k).startswith("_")}
            valeur = str(garde.get("valeur") or "")
            if not valeur:
                raise WorkflowMappingError(
                    f"{path}: formats.{rubrique} n°{rang} n'a pas de « valeur » — "
                    f"c'est elle que le lanceur renvoie")
            if rubrique == "resolutions":
                for cote in ("cote_court", "cote_long"):
                    mesure = garde.get(cote)
                    if not isinstance(mesure, int) or isinstance(mesure, bool):
                        raise WorkflowMappingError(
                            f"{path}: la résolution {valeur!r} n'a pas de « {cote} » entier "
                            f"({mesure!r}) — sans les deux côtés, elle ne se traduit pas "
                            f"en largeur/hauteur")
            lu[rubrique].append(garde)
    return lu


def load_catalog(path: str | Path, workflows_dir: str | Path | None = None,
                 data_dir: str | Path | None = None) -> WorkflowCatalog:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"cannot read reconciliation file {path}: {exc}") from exc

    # Les workflows propres à CETTE machine vivent à côté, jamais dans le paquet.
    from .local_overlay import merge, read_overlay
    livrees = dict(data.get("workflows") or {})
    try:
        data = merge(data, read_overlay(Path(data_dir) if data_dir else None, path), "workflows")
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"cannot read local reconciliation overlay: {exc}") from exc

    workflows = data.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise WorkflowMappingError(f"{path}: 'workflows' must be a non-empty object")

    # Une surcharge locale REMPLACE l'entrée de même nom : c'est ce qu'on veut
    # d'une liaison réécrite à la main. Mais une entrée qui ne porte QUE la
    # vitrine (titre, catégorie) veut DÉCORER, pas remplacer — sans cette
    # nuance, donner un titre à un workflow livré effaçait ses liaisons.
    for nom, entree in list(workflows.items()):
        if (isinstance(entree, dict) and nom in livrees
                and not {"workflow", "bindings", "chaine"} & set(entree)):
            workflows[nom] = {**livrees[nom], **entree}

    # Les techniques d'abord : une chaîne expose AUSSI leurs réglages, et son
    # entrée de catalogue ne peut pas se décrire sans elles.
    from .techniques import lire_toutes as _lire_techniques
    techniques = _lire_techniques(data_dir)

    specs: dict[str, WorkflowSpec] = {}
    vitrines: dict[str, dict[str, Any]] = {}
    for name, entry in workflows.items():
        if not isinstance(entry, dict):
            raise WorkflowMappingError(f"workflow {name!r}: needs 'workflow' and 'bindings'")
        # La vitrine se lit sur TOUTE entrée, chaîne ou graphe : c'est ce qui
        # fait qu'un lanceur montre le titre déclaré (« Révéler une image »)
        # et non l'identifiant technique, et qu'une entrée technique reste hors
        # de la vue sans avoir à tenir une seconde liste quelque part.
        vitrine = {
            "titre": str(entry.get("titre") or ""),
            "description": str(entry.get("description") or ""),
            "categorie": (str(entry["categorie"]) if entry.get("categorie") else None),
            "ordre": int(entry.get("ordre", 100)),
            "aides": {str(k): str(v) for k, v in (entry.get("aides") or {}).items()},
        }
        if "chaine" in entry:
            specs[name] = _spec_de_chaine(name, entry, path, vitrine, techniques)
            continue
        if "workflow" not in entry and "bindings" not in entry:
            # Une entrée qui ne porte QUE la vitrine habille un graphe DÉPOSÉ
            # dans le dossier des workflows : celui-là est auto-lié, il n'a
            # aucune entrée déclarée où écrire son titre. Le déclarer en entier
            # ici le masquerait et lui ferait perdre son auto-liaison.
            vitrines[name] = vitrine
            continue
        if "workflow" not in entry or "bindings" not in entry:
            raise WorkflowMappingError(f"workflow {name!r}: needs 'workflow' and 'bindings'")
        bindings = {}
        for k, spec in entry["bindings"].items():
            if not isinstance(spec, dict) or "node" not in spec or "input" not in spec:
                raise WorkflowMappingError(f"workflow {name!r}: binding {k!r} needs 'node' and 'input'")
            bindings[k] = Binding(node=str(spec["node"]), input=str(spec["input"]))
        wf_path = (path.parent / entry["workflow"]).resolve()
        # Derive dependencies from the declared graph too (for deps checks).
        deps: dict[str, list[str]] = {}
        try:
            # Passe par _graph_of : un gabarit de MONTAGE n'est un graphe qu'une
            # fois déplié, et ses dépendances doivent se lire sur ce dépliage.
            deps = autobind.derive_dependencies(_graph_of(wf_path))
        except (OSError, json.JSONDecodeError):
            pass
        specs[name] = WorkflowSpec(
            name=name,
            kind=str(entry.get("kind", "image")),
            workflow_path=wf_path,
            bindings=bindings,
            defaults=dict(entry.get("defaults", {})),
            limits=dict(entry.get("limits", {})),
            dependencies=deps,
            carried=_carried_media(_graph_of(wf_path), bindings),
            pilote=_pilotes(wf_path),
            exemple=_exemple(wf_path),
            **vitrine,
        )

    default = data.get("default") or next(iter(specs))
    if default not in specs:
        raise WorkflowMappingError(f"{path}: default {default!r} not among declared workflows")

    # Les graphes déposés dans le dossier des workflows sont découverts seuls.
    # Sur un nom déjà DÉCLARÉ, l'entrée déclarée l'emporte — ses liaisons sont
    # écrites à la main, donc voulues. Mais le masquage était silencieux : un
    # workflow enregistré par l'API répondait « praticable » jusqu'au
    # redémarrage, puis cédait la place sans un mot. Les collisions sont donc
    # retenues et exposées.
    masques: list[str] = []
    if workflows_dir:
        wd = Path(workflows_dir)
        index: dict[str, Any] = {}
        idx_file = wd / "index.json"
        if idx_file.exists():
            try:
                index = json.loads(idx_file.read_text(encoding="utf-8")).get("workflows", {})
            except (OSError, json.JSONDecodeError):
                index = {}
        if wd.is_dir():
            for f in sorted(wd.glob("*.json")):
                if f.name == "index.json":
                    continue
                if f.stem in specs:
                    masques.append(f.stem)
                    continue
                try:
                    graph = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if autobind.looks_like_api_graph(graph):
                    specs[f.stem] = _spec_from_graph(f.stem, f, graph, index.get(f.stem))

    # La vitrine se pose APRÈS la découverte : elle habille aussi bien un
    # graphe déposé qu'une entrée déclarée.
    from dataclasses import replace as _replace
    orphelines: list[str] = []
    for nom, vitrine in vitrines.items():
        if nom not in specs:
            # Un titre qui n'habille rien ne doit pas disparaître en silence :
            # c'est un workflow renommé ou retiré, et la vitrine le dira.
            orphelines.append(nom)
            continue
        specs[nom] = _replace(specs[nom], **vitrine)

    catalogue = WorkflowCatalog(default=default, specs=specs,
                                workflows_dir=Path(workflows_dir) if workflows_dir else None,
                                categories=data.get("categories"),
                                menus=data.get("menus"),
                                formats=_formats(data.get("formats"), path),
                                techniques=techniques)
    catalogue.shadowed = tuple(masques)
    catalogue.vitrines_orphelines = tuple(sorted(orphelines))
    catalogue.categories_de_champs = _categories_de_champs(data.get("categories_de_champs"), path)
    return catalogue


def _categories_de_champs(brut: Any, path: Path) -> list[dict[str, Any]]:
    """Le vocabulaire des catégories de champs, lu et VÉRIFIÉ : une liste
    ordonnée d'objets {valeur, titre}, sans doublon — un titre qui manque ou
    une valeur en double se dirait au premier formulaire, mieux vaut ici."""
    if brut is None:
        return []
    if not isinstance(brut, list):
        raise WorkflowMappingError(f"{path}: categories_de_champs doit être une liste")
    sorties: list[dict[str, Any]] = []
    vues: set[str] = set()
    for rang, entree in enumerate(brut, start=1):
        if not isinstance(entree, dict) or not str(entree.get("valeur") or "").strip():
            raise WorkflowMappingError(
                f"{path}: categories_de_champs n°{rang} doit être un objet avec une « valeur »")
        valeur = str(entree["valeur"]).strip()
        if valeur in vues:
            raise WorkflowMappingError(f"{path}: categories_de_champs : {valeur!r} en double")
        vues.add(valeur)
        sorties.append({"valeur": valeur, "titre": str(entree.get("titre") or valeur),
                        "resume": str(entree.get("resume") or ""),
                        # « repliee » : une catégorie qu'un lanceur replie sous un
                        # volet — utile, jamais le sujet. Déclaré ici, jamais
                        # deviné par le lanceur d'après un nom.
                        "repliee": bool(entree.get("repliee", False))})
    return sorties
