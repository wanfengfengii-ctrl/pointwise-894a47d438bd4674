"""Publish only when every fixed position has a terminal result."""

from __future__ import annotations

from dataclasses import dataclass

from station.capture.repository import CaptureRepository
from station.closeout.coverage import resolve_coverage
from station.closeout.repository import StatementRepository
from station.closeout.statement import Publication, assemble_statement
from station.closeout.statistics import summarize_measurements
from station.device.repository import DeviceRepository
from station.shift.roster import lookup_shift


@dataclass(frozen=True)
class CloseResult:
    pending: tuple[object, ...] = ()
    publication: Publication | None = None

    @property
    def status(self) -> str:
        return 'waiting' if self.publication is None else 'published'


def close_shift(database, shift_id: str, *, source: DeviceRepository | None = None) -> CloseResult:
    repository = StatementRepository(database)
    # Intake and request registration cannot change the candidate while it is signed.
    with CaptureRepository(database).transaction():
        shift = lookup_shift(database, shift_id)
        previous = repository.get_by_shift(shift_id)
        if previous is not None:
            return CloseResult(publication=previous)

        outcomes = resolve_coverage(database, shift, source)
        pending = tuple(position.position for position in outcomes if position.state == 'waiting')
        if pending:
            return CloseResult(pending=pending)

        publication = assemble_statement(shift, outcomes, summarize_measurements(outcomes))
        repository.store_publication(publication)
        return CloseResult(publication=publication)
