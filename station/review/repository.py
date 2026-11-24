"""Persist paper-log checks scoped to a particular signed result."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from station.review.exceptions import ReviewConclusion


@dataclass(frozen=True)
class ReviewNote:
    note_id: int
    result_id: str
    boot: str
    sequence: int
    reported_um: int
    conclusion: ReviewConclusion

    @property
    def position(self) -> int | tuple[str, int]:
        return self.sequence if self.boot == '' else (self.boot, self.sequence)


class ReviewRepository:
    def __init__(self, database: sqlite3.Connection):
        self.database = database

    def initialize(self) -> None:
        self._upgrade_legacy_schema()
        self.database.executescript('''
            CREATE TABLE IF NOT EXISTS review_notes (
                note_id INTEGER PRIMARY KEY,
                result_id TEXT NOT NULL REFERENCES statements (result_id),
                boot TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                reported_um INTEGER NOT NULL,
                conclusion TEXT NOT NULL CHECK (conclusion IN ('match', 'mismatch', 'missing'))
            );
            CREATE INDEX IF NOT EXISTS review_notes_for_result ON review_notes (result_id, note_id);
        ''')

    def _upgrade_legacy_schema(self) -> None:
        columns = {row['name'] for row in self.database.execute('PRAGMA table_info(review_notes)')}
        if columns and 'boot' not in columns:
            self.database.execute("ALTER TABLE review_notes ADD COLUMN boot TEXT NOT NULL DEFAULT ''")

    def append_note(
        self, result_id: str, boot: str, sequence: int, reported_um: int,
        conclusion: ReviewConclusion,
    ) -> ReviewNote:
        result = self.database.execute('''
            INSERT INTO review_notes (result_id, boot, sequence, reported_um, conclusion)
            VALUES (?, ?, ?, ?, ?)
        ''', (result_id, boot, sequence, reported_um, conclusion.value))
        return ReviewNote(result.lastrowid, result_id, boot, sequence, reported_um, conclusion)

    def notes_for_result(self, result_id: str) -> tuple[ReviewNote, ...]:
        rows = self.database.execute(
            'SELECT * FROM review_notes WHERE result_id = ? ORDER BY note_id', (result_id,),
        )
        return tuple(
            ReviewNote(row['note_id'], row['result_id'], row['boot'], row['sequence'],
                       row['reported_um'], ReviewConclusion(row['conclusion']))
            for row in rows
        )
