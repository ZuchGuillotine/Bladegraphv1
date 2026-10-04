"""Persistent reservations survive crashes and share a project-wide hard cap."""

import math
import sqlite3
import uuid
from pathlib import Path

COMPONENTS = {"worker_agents", "Jev", "reviewer", "simulation", "storage", "other"}


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: str | Path, run_id: str, run_limit: float = 5.0):
        if not math.isfinite(run_limit) or not 0 < run_limit <= 20:
            raise ValueError("MVP paid runs must have a budget in (0, $20]")
        self.path = Path(path)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS costs (id TEXT PRIMARY KEY, run TEXT, component TEXT, usd REAL, tokens INTEGER, status TEXT)"
        )
        self.run_id, self.run_limit = run_id, run_limit

    def reserve(self, component: str, upper_bound: float) -> str:
        if component not in COMPONENTS or not math.isfinite(upper_bound) or upper_bound < 0:
            raise ValueError("invalid spend reservation")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            total = self.db.execute("SELECT COALESCE(SUM(usd), 0) FROM costs").fetchone()[0]
            run = self.db.execute("SELECT COALESCE(SUM(usd), 0) FROM costs WHERE run=?", (self.run_id,)).fetchone()[0]
            # Stop at the soft target too; raising it requires a reviewed code/config change.
            if total + upper_bound > 150 or total + upper_bound > 300 or run + upper_bound > self.run_limit:
                raise BudgetExceeded("project or run budget exhausted")
            key = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO costs VALUES (?, ?, ?, ?, 0, 'reserved')", (key, self.run_id, component, upper_bound)
            )
            self.db.commit()
            return key
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, key: str, actual: float, tokens: int):
        if not math.isfinite(actual) or actual < 0 or tokens < 0:
            raise ValueError("invalid provider usage")
        with self.db:
            row = self.db.execute("SELECT usd, status FROM costs WHERE id=?", (key,)).fetchone()
            if not row or row[1] != "reserved":
                raise ValueError("unknown or settled reservation")
            # Record actual overages even on failure; never erase incurred spend.
            self.db.execute("UPDATE costs SET usd=?, tokens=?, status='settled' WHERE id=?", (actual, tokens, key))
        if actual > row[0]:
            raise BudgetExceeded("provider usage exceeded conservative reservation; stop and reconcile pricing")

    def summary(self) -> dict:
        result = {c: 0.0 for c in sorted(COMPONENTS)}
        for c, usd in self.db.execute("SELECT component, SUM(usd) FROM costs GROUP BY component"):
            result[c] = round(usd, 8)
        result["total_usd"] = round(sum(result.values()), 8)
        result["tokens"] = self.db.execute("SELECT COALESCE(SUM(tokens),0) FROM costs").fetchone()[0]
        result["unsettled_reservations"] = self.db.execute(
            "SELECT COUNT(*) FROM costs WHERE status='reserved'"
        ).fetchone()[0]
        return result
