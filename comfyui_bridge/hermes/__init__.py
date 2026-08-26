"""Hermes — reconciliation against KNOWN problems, in local mode.

Its value is memory, not cleverness: it remembers what actually went wrong on
this host (the local problem registry) and checks what can be verified up front,
so a run is not launched into a problem already met. No invented heuristics.
"""
