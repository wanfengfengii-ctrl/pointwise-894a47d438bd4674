"""Record review against the positions in a frozen statement."""

from station import require_integer
from station.review.exceptions import classify_position
from station.review.repository import ReviewNote, ReviewRepository
from station.review.retrieve import retrieve


def reconcile(database, result_id: str, sequence: int, reported_um: int) -> ReviewNote:
    require_integer(sequence, 'review sequence')
    statement = retrieve(database, result_id).statement
    offset = sequence - statement.shift.start
    if not 0 <= offset < len(statement.positions):
        raise ValueError('review position is outside the signed shift')
    position = statement.positions[offset]
    conclusion = classify_position(position, reported_um)
    return ReviewRepository(database).append_note(result_id, sequence, reported_um, conclusion)
