"""Normalize a fixed source position, with or without a boot segment.

Legacy shifts identify a position by an integer source sequence alone.  A
recorder that restarted inside the shift uses ``(boot, sequence)`` pairs
because both starts may reuse the same source numbers.  The empty string is
reserved for the legacy, no-boot identity so database keys stay uniform.
"""

from __future__ import annotations

from station import require_integer, require_name


LEGACY_BOOT = ''


def position_key(position: object, field: str = 'position') -> tuple[str, int]:
    """Return the public ``(boot, sequence)`` identity; legacy is a bare int."""
    if type(position) is int:
        return LEGACY_BOOT, require_integer(position, field)
    if isinstance(position, (tuple, list)) and len(position) == 2:
        boot, sequence = position
        try:
            boot = require_name(boot, 'boot')
            sequence = require_integer(sequence, 'sequence')
        except ValueError as error:
            raise ValueError(f'{field} must be a boot name and source sequence') from error
        return boot, sequence
    raise ValueError(f'{field} must be an integer or a (boot, sequence) pair')


def key_position(position: object, field: str = 'position') -> tuple[str, int]:
    """Like :func:`position_key`, but also accepts the durable legacy boot key."""
    if isinstance(position, (tuple, list)) and len(position) == 2 and position[0] == LEGACY_BOOT:
        return LEGACY_BOOT, require_integer(position[1], field)
    return position_key(position, field)


def key_boot(boot: str | None) -> str:
    """Map a public boot name to its durable key (``None`` or empty means legacy)."""
    if boot is None or boot == LEGACY_BOOT:
        return LEGACY_BOOT
    return require_name(boot, 'boot')
