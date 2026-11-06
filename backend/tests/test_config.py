"""Runtime Hindsight bank alias tests."""

from backend.config import resolve_bank_id, rotate_bank_id


def test_reset_rotates_requested_bank_alias() -> None:
    alias = "test-exp-bank-unique"

    rotated = rotate_bank_id(alias)

    assert rotated != alias
    assert resolve_bank_id(alias) == rotated
    assert resolve_bank_id("unmapped-bank") == "unmapped-bank"
