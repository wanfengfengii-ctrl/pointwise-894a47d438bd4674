"""Compare a paper log entry to its exact signed source position."""

from enum import Enum

from station import MIN_INTEGER, require_integer
from station.closeout.coverage import PositionOutcome


class ReviewConclusion(str, Enum):
    MATCH = 'match'
    MISMATCH = 'mismatch'
    MISSING = 'missing'


def classify_position(position: PositionOutcome, reported_um: int) -> ReviewConclusion:
    require_integer(reported_um, 'paper reading', MIN_INTEGER)
    if position.state == 'missing':
        return ReviewConclusion.MISSING
    if position.state != 'received' or position.receipt is None:
        raise ValueError('position has no signed outcome')
    if position.receipt.packet.micrometers == reported_um:
        return ReviewConclusion.MATCH
    return ReviewConclusion.MISMATCH
