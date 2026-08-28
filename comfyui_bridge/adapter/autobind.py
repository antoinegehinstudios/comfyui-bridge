"""Auto-binding — derive param->node mappings from a ComfyUI API graph.

The point: a user exports a workflow from ComfyUI and it becomes callable with
NO hand-written bindings. We introspect the graph and match our neutral params
to node inputs by ComfyUI's standard conventions:

    prompt / negative_prompt  ->  the CLIPTextEncode feeding the sampler's
                                  positive / negative conditioning (traced)
    width / height            ->  a latent node exposing width & height
    latent_batch (frames)     ->  ``length`` (video) else ``batch_size`` (image)
    seed                      ->  ``seed`` / ``noise_seed``
    steps / cfg               ->  ``steps`` / ``cfg``
    fps                       ->  ``fps`` / ``frame_rate``
    filename_prefix           ->  a Save* node's ``filename_prefix``

Whatever can't be identified is simply left unbound (the workflow keeps its own
value) — never guessed wildly. ``/v1/preview`` shows the result so a human can
verify or override with an explicit entry in the reconciliation file.
"""

from __future__ import annotations

from typing import Any

from ..core.intention import media_param
from .mapping import Binding
from .media_inputs import media_nodes

_SAMPLERS = ("KSampler", "SamplerCustom", "SamplerCustomAdvanced")


def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int))


def _inputs(node: Any) -> dict[str, Any]:
    return (node.get("inputs") if isinstance(node, dict) else None) or {}


def infer_kind(graph: dict[str, Any]) -> str:
    types = {n.get("class_type", "") for n in graph.values() if isinstance(n, dict)}
    sauvegardes = tuple(t for t in types if "Save" in t)
    # Ce qu'un workflow LIVRE décide de son genre. Une vidéo qui porte sa propre
    # bande son enregistre les deux (SaveVideo ET SaveAudioMP3) : le contenant
    # l'emporte. Lire l'audio d'abord faisait passer un workflow vidéo pour de
    # l'audio — mesuré sur `template_image_speech_to_video` — et son nombre
    # d'images repartait alors sur `batch_size`, c'est-à-dire un nombre de CLIPS.
    if any("Video" in t for t in sauvegardes):
        return "video"
    if any("Audio" in t for t in sauvegardes):
        return "audio"
    video_markers = ("SaveVideo", "CreateVideo", "VideoCombine", "LTXV", "SVD", "EmptyLTXVLatentVideo")
    if any(any(m in t for m in video_markers) for t in types):
        return "video"
    if any("length" in _inputs(n) for n in graph.values()):
        return "video"
    return "image"


def _trace_text_node(graph: dict[str, Any], nid: str | None) -> str | None:
    """Follow conditioning links upstream to the CLIPTextEncode with literal text."""
    seen: set[str] = set()
    while nid and nid in graph and nid not in seen:
        seen.add(nid)
        ins = _inputs(graph[nid])
        if "text" in ins and not _is_link(ins["text"]):
            return nid
        nxt = None
        for key in ("conditioning", "positive"):
            if _is_link(ins.get(key)):
                nxt = str(ins[key][0])
                break
        nid = nxt
    return None


def _negative_text_nodes(graph: dict[str, Any]) -> set[str]:
    """Text nodes whose conditioning ends up on a ``negative`` input.

    Binding the caller's prompt onto one of these tells the model to AVOID what
    was asked — measured on a real LTX workflow, where the produced video had
    nothing to do with the request. Consumers are followed through conditioning
    chains, so a text node behind a ZeroOut/Conditioning node still counts.
    """
    negative: set[str] = set()
    positive: set[str] = set()
    for node in graph.values():
        for key, val in _inputs(node).items():
            if not _is_link(val):
                continue
            src = str(val[0])
            if key in ("negative", "negative_prompt"):
                negative.add(src)
            elif key in ("positive", "prompt"):
                positive.add(src)
    # Direct wiring only. Walking further up confuses a node's separate outputs
    # (a conditioning node emits positive AND negative from the same node id)
    # and wrongly cleared the real negative text node.
    # A node feeding both sides (a positive text zeroed out into the negative)
    # stays usable for the prompt.
    return negative - positive


def _sampler_conditioning(graph: dict[str, Any]) -> tuple[str | None, str | None]:
    for node in graph.values():
        ct = node.get("class_type", "") if isinstance(node, dict) else ""
        ins = _inputs(node)
        if any(s in ct for s in _SAMPLERS) and _is_link(ins.get("positive")):
            pos = str(ins["positive"][0])
            neg = str(ins["negative"][0]) if _is_link(ins.get("negative")) else None
            return pos, neg
    return None, None


_TEXT_KEYS = ("text", "value", "prompt", "string", "text_g", "text_l")


def _literal_text_input(node: Any) -> str | None:
    """The settable text input of a node, whatever its class calls it."""
    ins = _inputs(node)
    for key in _TEXT_KEYS:
        if isinstance(ins.get(key), str):
            return key
    return None


def _role_sets(graph: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Nodes wired to the positive side, and to the negative side."""
    positive: set[str] = set()
    negative: set[str] = set()
    for node in graph.values():
        for key, val in _inputs(node).items():
            if not _is_link(val):
                continue
            src = str(val[0])
            if key in ("negative", "negative_prompt"):
                negative.add(src)
            elif key in ("positive", "prompt"):
                positive.add(src)
    return positive, negative - positive


# The author's own titles ("Duration", "Width", "Frame Rate") say what a node
# means far better than any heuristic. ComfyUI Desktop shows exactly these.
_TITLE_PARAMS = {
    "duration": "duration_s", "duree": "duration_s", "durée": "duration_s",
    "frame rate": "fps", "fps": "fps", "framerate": "fps",
    "width": "width", "largeur": "width",
    "height": "height", "hauteur": "height",
    "steps": "steps", "seed": "seed", "cfg": "cfg",
    "prompt": "prompt", "negative prompt": "negative_prompt",
    "length": "latent_batch", "frames": "latent_batch",
}
_VALUE_KEYS = ("value", "text", "int", "float", "string", "number", "seconds")


def _title_candidates(title: str) -> list[str]:
    """The names a title can be read under.

    ComfyUI titles a converted widget "Float (duration)" — the meaning sits in
    the parentheses. Reading only the whole string lost the duration node of a
    real workflow, so the parenthesised part and the part before it are read
    too. Nothing wider: a title that says nothing stays unbound.
    """
    title = title.strip().lower()
    names = [title]
    if "(" in title and title.endswith(")"):
        head, _, inner = title.partition("(")
        names.append(inner[:-1].strip())
        names.append(head.strip())
    return [n for n in names if n]


def bindings_from_titles(graph: dict[str, Any], titles: dict[str, str]) -> dict[str, Binding]:
    """Bind semantic params to the nodes the author named for them."""
    out: dict[str, Binding] = {}
    for nid, node in graph.items():
        title = titles.get(nid) or titles.get(nid.rsplit(":", 1)[-1]) or ""
        param = next((p for p in (_TITLE_PARAMS.get(c) for c in _title_candidates(title)) if p), None)
        if not param:
            continue
        ins = _inputs(node)
        for key in _VALUE_KEYS:
            if key in ins and not _is_link(ins[key]):
                out.setdefault(param, Binding(nid, key))
                break
    return out


def derive_bindings(graph: dict[str, Any], titles: dict[str, str] | None = None) -> dict[str, Binding]:
    b: dict[str, Binding] = dict(bindings_from_titles(graph, titles or {}))
    positives, negatives = _role_sets(graph)
    is_video = infer_kind(graph) == "video"

    # The prompt is decided by WIRING, not by node class: a workflow may hold it
    # in a PrimitiveStringMultiline feeding the positive side (measured on a real
    # LTX workflow whose prompt was invisible to a CLIPTextEncode-only scan).
    for nid in sorted(positives):
        key = _literal_text_input(graph.get(nid))
        if key:
            b["prompt"] = Binding(nid, key)
            break
    for nid in sorted(negatives):
        key = _literal_text_input(graph.get(nid))
        if key:
            b["negative_prompt"] = Binding(nid, key)
            break

    # Input MEDIA are inputs like any other: name them ALL so a caller can set
    # them. N'en nommer qu'une par catégorie laissait sans preneur la dernière
    # image d'un flf2v, les vues d'un assemblage et toute entrée audio — mesuré :
    # 3 des 4 LoadImage de `utility_image_stitch` injoignables, « Load Last
    # Frame » de `video_ltx2_3_flf2v` aussi, l'audio de `video_wan2_2_14B_s2v`
    # aussi. Le rang suit l'ordre des nœuds, donc il ne bouge pas d'un appel à
    # l'autre, et la première de chaque catégorie garde son nom nu.
    rangs: dict[str, int] = {}
    for nid, _class_type, categorie, entree in media_nodes(graph):
        rangs[categorie] = rangs.get(categorie, 0) + 1
        b.setdefault(media_param(categorie, rangs[categorie]), Binding(nid, entree))
    return _complete_bindings(graph, b, negatives,
                              need_prompt="prompt" not in b, is_video=is_video)


def _complete_bindings(graph: dict[str, Any], b: dict[str, Binding],
                       negatives: set[str], need_prompt: bool = False,
                       is_video: bool = False) -> dict[str, Binding]:

    # Fallback when nothing is wired as positive (simple graphs): the first
    # literal text node that is not the negative one.
    if need_prompt and "prompt" not in b:
        for nid, node in graph.items():
            if nid in negatives:
                continue
            key = _literal_text_input(node)
            if key:
                b["prompt"] = Binding(nid, key)
                break
    if "negative_prompt" not in b:
        for nid, node in graph.items():
            ins = _inputs(node)
            if isinstance(ins.get("negative_prompt"), str):
                b["negative_prompt"] = Binding(nid, "negative_prompt")
                break

    # -- dimensions + frame/batch axis ----------------------------------------
    for nid, node in graph.items():
        ins = _inputs(node)
        if "width" in ins and "height" in ins and not _is_link(ins["width"]):
            b.setdefault("width", Binding(nid, "width"))
            b.setdefault("height", Binding(nid, "height"))
        # An audio latent is measured in SECONDS — ComfyUI's own name for it
        # (EmptyLatentAudio.seconds). Without this the duration of an audio
        # render, its main parameter, could not be set at all.
        if "seconds" in ins and not _is_link(ins["seconds"]):
            b.setdefault("duration_s", Binding(nid, "seconds"))
        # Frames and batch are NOT interchangeable. In a video workflow the
        # frame count lives in `length`; every `batch_size` there counts CLIPS
        # (video or audio latents alike). Falling back to batch_size turned
        # "2 seconds" into 48 simultaneous videos — engine dead, nothing
        # delivered. So in a video graph, only a literal `length` is accepted.
        if "length" in ins and not _is_link(ins["length"]):
            b.setdefault("latent_batch", Binding(nid, "length"))
        elif not is_video and "batch_size" in ins and not _is_link(ins["batch_size"]):
            b.setdefault("latent_batch", Binding(nid, "batch_size"))

    # -- sampler params --------------------------------------------------------
    for nid, node in graph.items():
        ins = _inputs(node)
        for key in ("seed", "noise_seed"):
            if key in ins and not _is_link(ins[key]):
                b.setdefault("seed", Binding(nid, key))
        if "steps" in ins and not _is_link(ins["steps"]):
            b.setdefault("steps", Binding(nid, "steps"))
        if "cfg" in ins and not _is_link(ins["cfg"]):
            b.setdefault("cfg", Binding(nid, "cfg"))

    # -- outputs ---------------------------------------------------------------
    for nid, node in graph.items():
        ins = _inputs(node)
        if "filename_prefix" in ins:
            b.setdefault("filename_prefix", Binding(nid, "filename_prefix"))
        for fk in ("fps", "frame_rate"):
            if fk in ins and not _is_link(ins[fk]):
                b.setdefault("fps", Binding(nid, fk))

    return b


_MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft", ".bin", ".onnx")


def derive_dependencies(graph: dict[str, Any]) -> dict[str, list[str]]:
    """What a workflow NEEDS to run: model files + node types.

    Feeds the maintained dependency manifest: a workflow can be checked against
    the host (models present? node types installed?) before use — and a missing
    dependency is a known, honest failure reason for Hermes / the knowledge base.
    """
    models: set[str] = set()
    node_types: set[str] = set()
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        if ct:
            node_types.add(ct)
        for key, val in (node.get("inputs") or {}).items():
            if isinstance(val, str) and val.lower().endswith(_MODEL_EXT):
                models.add(val)
    return {"models": sorted(models), "node_types": sorted(node_types)}


def looks_like_api_graph(data: Any) -> bool:
    """True for a ComfyUI API graph ({id: {class_type, inputs}}), not UI format."""
    if not isinstance(data, dict) or "nodes" in data or "links" in data:
        return False
    return any(isinstance(v, dict) and "class_type" in v for v in data.values())
