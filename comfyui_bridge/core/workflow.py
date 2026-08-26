"""Abstract view of a declared workflow, as the domain sees it.

The reconciliation file declares named workflows. The core only needs the
*abstract* face of each — its name, media kind, parameter defaults, and any
declared hardware limits. The ComfyUI-specific parts (graph, node bindings)
stay in the adapter's catalog and never reach the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowProfile:
    name: str
    kind: str                                   # image | video | audio
    defaults: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    # Media the workflow already carries (e.g. an input image). Left alone when
    # the caller supplies nothing — but never silently: that content ends up in
    # the result, so the run says which one it used.
    carried: dict[str, Any] = field(default_factory=dict)
    # The parameter names this workflow can actually receive. Abstract on
    # purpose: the core never learns HOW they are wired, only whether asking
    # for one means anything here.
    accepts: tuple[str, ...] = ()
