"""Advance one recorded event at a time, including across process restarts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from station.device.operations import RepairOutcome, RepairState, issue_in_transaction, query_repair
from station.device.recording import RecordingEvent
from station.device.repository import DeviceRepository
from station.device.slots import SlotImage, cover_position, place_packet


@dataclass(frozen=True)
class ReplaySession:
    repository: DeviceRepository
    events: tuple[RecordingEvent, ...]
    prefixes: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        events = tuple(self.events)
        digest = bytes(32)
        prefixes = [digest.hex()]
        for event in events:
            digest = hashlib.sha256(digest + event.fingerprint_material()).digest()
            prefixes.append(digest.hex())
        object.__setattr__(self, 'events', events)
        object.__setattr__(self, 'prefixes', tuple(prefixes))


@dataclass(frozen=True)
class PollResult:
    event: RecordingEvent
    image: SlotImage | None = None
    covered_sequence: int | None = None
    repair: RepairOutcome | None = None


def _apply_event(repository: DeviceRepository, event: RecordingEvent) -> PollResult:
    if event.kind == 'record':
        image = SlotImage(repository.device, event.sequence, event.slot, event.payload)
        covered = place_packet(repository, image)
        return PollResult(event, image=image, covered_sequence=covered)
    if event.kind == 'cover':
        covered = cover_position(repository, event.slot, event.sequence)
        return PollResult(event, covered_sequence=covered)
    if event.kind == 'issue':
        outcome = issue_in_transaction(repository, event.operation)
        if outcome.state is RepairState.UNKNOWN:
            raise ValueError('issue event refers to an unaccepted operation')
        return PollResult(event, repair=outcome)
    if event.kind == 'reply':
        outcome = query_repair(repository, event.operation)
        if outcome.state is not RepairState.ISSUED:
            raise ValueError('reply event requires an issued original')
        return PollResult(event, image=outcome.image, repair=outcome)
    raise ValueError(f'invalid event kind {event.kind!r}')


def poll(session: ReplaySession) -> PollResult | None:
    repository = session.repository
    with repository.transaction():
        cursor, prefix = repository.cursor_state()
        if cursor >= len(session.prefixes) or session.prefixes[cursor] != prefix:
            raise ValueError('recording differs from the already committed prefix')
        if cursor == len(session.events):
            return None
        result = _apply_event(repository, session.events[cursor])
        repository.advance(cursor + 1, session.prefixes[cursor + 1])
        return result
