"""Record review against the positions in a frozen statement."""

from station.closeout.coverage import PositionOutcome
from station.positions import LEGACY_BOOT, position_key
from station.review.exceptions import classify_position
from station.review.repository import ReviewNote, ReviewRepository
from station.review.retrieve import retrieve


def _find_position(
    statement, boot: str, sequence: int,
) -> PositionOutcome:
    matches = [
        position for position in statement.positions
        if (position.boot, position.sequence) == (boot, sequence)
    ]
    if not matches:
        raise ValueError('review position is outside the signed shift')
    return matches[0]


def reconcile(database, result_id: str, position, reported_um: int) -> ReviewNote:
    """Recheck one signed position; ``position`` is an integer or (boot, sequence)."""
    boot, sequence = position_key(position, 'review position')
    statement = retrieve(database, result_id).statement
    if statement.shift.segments is None:
        # Legacy signed results keep the original integer-only shape.
        offset = sequence - statement.shift.start
        if not 0 <= offset < len(statement.positions):
            raise ValueError('review position is outside the signed shift')
        target = statement.positions[offset]
        boot = LEGACY_BOOT
    else:
        target = _find_position(statement, boot, sequence)
    conclusion = classify_position(target, reported_um)
    return ReviewRepository(database).append_note(result_id, boot, sequence, reported_um, conclusion)
