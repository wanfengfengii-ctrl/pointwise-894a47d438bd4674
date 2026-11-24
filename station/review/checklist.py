"""Expose gaps and paper-log discrepancies in device order."""

from __future__ import annotations

from dataclasses import dataclass

from station.capture.receipts import Receipt
from station.review.exceptions import ReviewConclusion
from station.review.repository import ReviewNote, ReviewRepository
from station.review.retrieve import retrieve


@dataclass(frozen=True)
class ChecklistEntry:
    sequence: int
    conclusion: ReviewConclusion
    receipt: Receipt | None = None
    note: ReviewNote | None = None


def build_checklist(database, result_id: str) -> tuple[ChecklistEntry, ...]:
    statement = retrieve(database, result_id).statement
    discrepancies = {
        note.sequence: note
        for note in ReviewRepository(database).notes_for_result(result_id)
        if note.conclusion is ReviewConclusion.MISMATCH
    }
    entries = []
    for position in statement.positions:
        if position.state == 'missing':
            entries.append(ChecklistEntry(position.sequence, ReviewConclusion.MISSING))
        elif position.sequence in discrepancies:
            entries.append(ChecklistEntry(
                position.sequence, ReviewConclusion.MISMATCH, position.receipt, discrepancies[position.sequence],
            ))
    return tuple(entries)
