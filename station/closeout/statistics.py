"""Compute shift statistics from formal measured readings."""

from __future__ import annotations

from dataclasses import dataclass

from station.closeout.coverage import PositionOutcome


@dataclass(frozen=True)
class MeasurementStats:
    received_count: int
    minimum_um: int | None
    maximum_um: int | None
    missing: tuple[object, ...]


def _report(position: PositionOutcome) -> object:
    return position.sequence if position.boot == '' else (position.boot, position.sequence)


def summarize_measurements(positions: tuple[PositionOutcome, ...]) -> MeasurementStats:
    values = [position.receipt.packet.micrometers for position in positions if position.receipt is not None]
    missing = tuple(_report(position) for position in positions if position.state == 'missing')
    return MeasurementStats(len(values), min(values, default=None), max(values, default=None), missing)
