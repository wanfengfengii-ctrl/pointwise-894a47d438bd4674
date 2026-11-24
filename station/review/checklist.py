"""Expose gaps and paper-log discrepancies in device order."""

from __future__ import annotations

from dataclasses import dataclass

from station.capture.receipts import Receipt
from station.review.exceptions import ReviewConclusion
from station.review.repository import ReviewNote, ReviewRepository
from station.review.retrieve import retrieve


@dataclass(frozen=True)
class ChecklistEntry:
    boot: str
    sequence: int
    conclusion: ReviewConclusion
    receipt: Receipt | None = None
    note: ReviewNote | None = None

    @property
    def position(self) -> int | tuple[str, int]:
        return self.sequence if self.boot == '' else (self.boot, self.sequence)


def build_checklist(database, result_id: str) -> tuple[ChecklistEntry, ...]:
    statement = retrieve(database, result_id).statement
    discrepancies = {
        (note.boot, note.sequence): note
        for note in ReviewRepository(database).notes_for_result(result_id)
        if note.conclusion is ReviewConclusion.MISMATCH
    }
    entries = []
    for position in statement.positions:
        key = (position.boot, position.sequence)
        if position.state == 'missing':
            entries.append(ChecklistEntry(position.boot, position.sequence, ReviewConclusion.MISSING))
        elif key in discrepancies:
            entries.append(ChecklistEntry(
                position.boot, position.sequence, ReviewConclusion.MISMATCH, position.receipt,
                discrepancies[key],
            ))
    return tuple(entries)
