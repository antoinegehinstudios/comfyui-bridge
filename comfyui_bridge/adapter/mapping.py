"""The binding of one neutral param to one ComfyUI node input.

The catalog (``reconciliation.json``) is the single place that declares them;
this module only holds the value type they are expressed in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Binding:
    node: str
    input: str
