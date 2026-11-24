"""Retrieve by the original result identity without a device connection."""

from station import require_name
from station.closeout.repository import StatementRepository
from station.closeout.statement import Publication


def retrieve(database, result_id: str) -> Publication:
    require_name(result_id, 'result id')
    publication = StatementRepository(database).get_by_result_id(result_id)
    if publication is None:
        raise KeyError(result_id)
    return publication
