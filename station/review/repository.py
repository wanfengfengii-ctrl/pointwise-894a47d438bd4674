"""Persist paper-log checks scoped to a particular signed result."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from station.review.exceptions import ReviewConclusion


@dataclass(frozen=True)
class ReviewNote:
    note_id: int
    result_id: str
    sequence: int
    reported_um: int
    conclusion: ReviewConclusion


class ReviewRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS review_notes (
                note_id INTEGER PRIMARY KEY,
                result_id TEXT NOT NULL REFERENCES statements (result_id),
                sequence INTEGER NOT NULL,
                reported_um INTEGER NOT NULL,
                conclusion TEXT NOT NULL CHECK (conclusion IN ('match', 'mismatch', 'missing'))
            );
            CREATE INDEX IF NOT EXISTS review_notes_for_result ON review_notes (result_id, note_id);
        ''')

    def append_note(
        self, result_id: str, sequence: int, reported_um: int, conclusion: ReviewConclusion,
    ) -> ReviewNote:
        result = self.database.execute('''
            INSERT INTO review_notes (result_id, sequence, reported_um, conclusion) VALUES (?, ?, ?, ?)
        ''', (result_id, sequence, reported_um, conclusion.value))
        return ReviewNote(result.lastrowid, result_id, sequence, reported_um, conclusion)

    def notes_for_result(self, result_id: str) -> tuple[ReviewNote, ...]:
        rows = self.database.execute(
            'SELECT * FROM review_notes WHERE result_id = ? ORDER BY note_id', (result_id,),
        )
        return tuple(
            ReviewNote(row['note_id'], row['result_id'], row['sequence'], row['reported_um'],
                       ReviewConclusion(row['conclusion']))
            for row in rows
        )
