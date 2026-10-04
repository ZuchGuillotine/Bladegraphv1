"""Append-only SQLite events with deterministic IDs, logical time and hash chaining."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class EventLog:
    def __init__(self, path: str | Path = ":memory:", *, logical_time: bool = True):
        self.logical_time = logical_time
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append only'); END"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append only'); END"
        )
        row = self.db.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        self.seq, self.previous = row if row else (0, "0" * 64)

    def append(
        self,
        event_type: str,
        *,
        agent_id: str = "controller",
        agent_generation: int = 0,
        task_id: str = "swarm",
        parent_event_ids: list[str] | None = None,
        artifact_ids_read: list[str] | None = None,
        artifact_ids_written: list[str] | None = None,
        artifact_ids_derived_from: list[str] | None = None,
        message_recipients: list[str] | None = None,
        tool_name: str | None = None,
        tool_action_summary: str = "",
        result_summary: str = "",
        classifier_result: dict | None = None,
        review_status: str = "NOT_REQUESTED",
        intervention_state: str = "NORMAL",
        payload: dict | None = None,
    ) -> dict:
        seq = self.seq + 1
        body = dict(
            event_id=f"e{seq:07d}",
            sequence=seq,
            timestamp=(
                (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=seq))
                if self.logical_time
                else datetime.now(timezone.utc)
            ).isoformat(),
            timestamp_basis=(
                "logical: one millisecond per event; not wall-clock latency" if self.logical_time else "UTC wall clock"
            ),
            agent_id=agent_id,
            agent_generation=agent_generation,
            event_type=event_type,
            task_id=task_id,
            parent_event_ids=parent_event_ids or [],
            artifact_ids_read=artifact_ids_read or [],
            artifact_ids_written=artifact_ids_written or [],
            artifact_ids_derived_from=artifact_ids_derived_from or [],
            message_recipients=message_recipients or [],
            tool_name=tool_name,
            tool_action_summary=tool_action_summary,
            result_summary=result_summary,
            classifier_result=classifier_result,
            review_status=review_status,
            intervention_state=intervention_state,
            payload=payload or {},
            previous_hash=self.previous,
        )
        h = digest(body)
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?, ?, ?)", (seq, canonical(body), h))
        self.seq, self.previous = seq, h
        return {**body, "hash": h}

    def events(self) -> Iterator[dict]:
        for body, h in self.db.execute("SELECT body, hash FROM events ORDER BY seq"):
            yield {**json.loads(body), "hash": h}

    def verify(self) -> None:
        previous = "0" * 64
        for seq, event in enumerate(self.events(), 1):
            h = event.pop("hash")
            if event["sequence"] != seq or event["previous_hash"] != previous or digest(event) != h:
                raise ValueError("event log integrity failure")
            previous = h

    def export(self, path: Path) -> None:
        path.write_text("".join(canonical(e) + "\n" for e in self.events()))

    def close(self) -> None:
        self.db.close()
