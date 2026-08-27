"""The problem registry — Hermes' local memory of what actually went wrong.

A plain SQLite log of REAL run outcomes: what was attempted (workflow, config
fingerprint), what happened (ok / a problem kind), and the real message. Nothing
synthetic is ever written: an empty registry honestly means "no known problem".

Knowledge is local to this pipeline (private ``_data`` dir) and scoped to its
tool, so it cannot leak into or be polluted by another pipeline.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scope     TEXT NOT NULL,
    host      TEXT NOT NULL,
    workflow  TEXT NOT NULL,
    config    TEXT NOT NULL,   -- fingerprint of the parameters that matter
    status    TEXT NOT NULL,   -- succeeded | failed
    problem   TEXT,            -- problem kind when failed (NULL on success)
    detail    TEXT,            -- the real message, verbatim (truncated)
    duration_s REAL,           -- how long the ENGINE took (queue wait excluded)
    work       REAL,           -- pixels x frames x steps (millions) for that run
    work_model INTEGER,        -- HOW that work was counted: two barèmes never mix
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_lookup ON runs(scope, host, workflow, config);
"""


from ..core.cost import config_fingerprint  # single definition, owned by the core


def _affine_fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares ``duration = setup + per_unit x work``.

    None when the measurements cannot support it: fewer than two different
    sizes, or a fit that comes out backwards (a bigger job taking less time),
    which would say more about noise than about the machine.
    """
    if len({w for w, _ in points}) < 2:
        return None
    n = len(points)
    sum_w = sum(w for w, _ in points)
    sum_d = sum(d for _, d in points)
    sum_ww = sum(w * w for w, _ in points)
    sum_wd = sum(w * d for w, d in points)
    denominator = n * sum_ww - sum_w * sum_w
    if denominator <= 0:
        return None
    per_unit = (n * sum_wd - sum_w * sum_d) / denominator
    if per_unit <= 0:
        return None
    setup = (sum_d - per_unit * sum_w) / n
    return max(0.0, setup), per_unit


class ProblemRegistry:
    def __init__(self, db_path: str | Path, scope: str = "comfyui") -> None:
        self._path = str(db_path)
        self._scope = scope
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            if "duration_s" not in cols:      # registry created before durations
                conn.execute("ALTER TABLE runs ADD COLUMN duration_s REAL")
            if "work" not in cols:            # …and before work was measured
                conn.execute("ALTER TABLE runs ADD COLUMN work REAL")
            if "work_model" not in cols:      # …and before it was counted this way
                conn.execute("ALTER TABLE runs ADD COLUMN work_model INTEGER")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- write (only real outcomes) -------------------------------------------

    def record(self, host: str, workflow: str, params: dict[str, Any], status: str,
               problem: str | None = None, detail: str | None = None,
               duration_s: float | None = None, work: float | None = None,
               work_model: int | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(scope,host,workflow,config,status,problem,detail,"
                "duration_s,work,work_model,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self._scope, host, workflow, config_fingerprint(params), status,
                 problem, (detail or "")[:500], duration_s, work, work_model,
                 datetime.now(timezone.utc).isoformat()),
            )

    # -- experience: how long does this usually take? -------------------------

    def estimate_duration(self, host: str, workflow: str, work: float | None = None,
                          config: str | None = None,
                          work_model: int | None = None) -> dict[str, Any] | None:
        """How long this will take, from measured runs.

        With a work figure (megapixels x frames x steps) and runs of DIFFERENT
        sizes, the fit is affine: ``setup + work x seconds-per-unit``. A run
        carries a real fixed cost (loading the model), so a purely proportional
        rule put a 70 s run at 8 s — and, the other way round, a 2 min run at
        29 min. One measured size therefore yields NO load-based answer: the
        median of that configuration is returned instead, labelled as such.
        Returns None when nothing comparable was ever measured.
        """
        if work:
            # Only runs whose work was counted the SAME way: mixing barèmes
            # would fit a line through two different scales.
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT duration_s, work FROM runs WHERE scope=? AND host=? AND workflow=?"
                    " AND status='succeeded' AND duration_s IS NOT NULL AND work > 0"
                    " AND work_model IS ? ORDER BY id DESC LIMIT 30",
                    (self._scope, host, workflow, work_model)).fetchall()
            points = [(r["work"], r["duration_s"]) for r in rows]
            fitted = _affine_fit(points)
            if fitted is not None:
                setup, per_unit = fitted
                seconds = setup + per_unit * work
                spread = max((abs(d - (setup + per_unit * w)) for w, d in points), default=0.0)
                return {"seconds": max(1, round(seconds)), "samples": len(points),
                        "basis": "work-fit", "work": round(work, 1),
                        "setup_s": round(setup), "per_unit_s": round(per_unit, 2),
                        "min": max(1, round(seconds - spread)),
                        "max": max(1, round(seconds + spread))}
            # One size measured cannot separate the fixed cost from the work:
            # applied proportionally it announced 29 min for a run of 2 (110 s
            # measured at load 10, asked for load 166). Better to fall back on a
            # plain median and say a second size is needed.

        # No work figure (or nothing fitted yet): exact configuration first,
        # then the workflow's other configurations — labelled either way.
        for scoped, basis in ((config, "same-config"), (None, "same-workflow")):
            sql = ("SELECT duration_s FROM runs WHERE scope=? AND host=? AND workflow=?"
                   " AND status='succeeded' AND duration_s IS NOT NULL")
            args: list[Any] = [self._scope, host, workflow]
            if scoped:
                sql += " AND config=?"
                args.append(scoped)
            sql += " ORDER BY id DESC LIMIT 20"
            with self._connect() as conn:
                values = sorted(r[0] for r in conn.execute(sql, args).fetchall())
            if values:
                n = len(values)
                median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
                return {"seconds": round(median), "samples": n, "basis": basis,
                        "min": round(values[0]), "max": round(values[-1])}
        return None

    # -- read ------------------------------------------------------------------

    def problems_for(self, host: str, workflow: str, config: str | None = None) -> list[dict[str, Any]]:
        """Past problems for this workflow (optionally this exact config), newest first."""
        sql = ("SELECT problem, detail, config, ts FROM runs"
               " WHERE scope=? AND host=? AND workflow=? AND status='failed'")
        args: list[Any] = [self._scope, host, workflow]
        if config is not None:
            sql += " AND config=?"
            args.append(config)
        sql += " ORDER BY id DESC LIMIT 20"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def blocking_problems(self, host: str, workflow: str,
                          config: str | None = None) -> list[dict[str, Any]]:
        """Remembered problems that would still stand today.

        A problem is dropped as soon as the SAME configuration succeeded here
        afterwards — the host changed, the memory is stale. One definition, used
        both to refuse a run and to tell the UI a workflow cannot run: two
        readings of the same memory would end up contradicting each other.
        """
        from ..core import problems as P
        # A missing model stays missing whatever the resolution, so those
        # problems are read across every configuration; an out-of-memory is
        # read only against the configuration that met it.
        rows = self.problems_for(host, workflow, config)
        if config is not None:
            seen = {(r.get("ts"), r.get("problem")) for r in rows}
            rows = rows + [r for r in self.problems_for(host, workflow)
                           if (r.get("ts"), r.get("problem")) not in seen
                           and r.get("problem") in P.INDEPENDENT_OF_CONFIG]
        standing = []
        for past in rows:
            if past.get("problem") not in P.BLOCKING:
                continue
            if self.succeeded_before(host, workflow, past.get("config", "")):
                continue
            standing.append(past)
        return standing

    def succeeded_before(self, host: str, workflow: str, config: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM runs WHERE scope=? AND host=? AND workflow=? AND config=?"
                " AND status='succeeded' LIMIT 1",
                (self._scope, host, workflow, config),
            ).fetchone()
        return row is not None

    def recent(self, host: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE scope=? AND host=? ORDER BY id DESC LIMIT ?",
                (self._scope, host, limit),
            ).fetchall()
        return [dict(r) for r in rows]
