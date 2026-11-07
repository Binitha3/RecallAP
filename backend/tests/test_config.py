"""Runtime Hindsight bank alias tests."""

from backend import config
from backend.config import resolve_bank_id, rotate_bank_id


def test_reset_rotates_requested_bank_alias(monkeypatch, tmp_path) -> None:
    alias = "test-exp-bank-unique"
    monkeypatch.setattr(config, "_bank_routes_path", tmp_path / "bank_routes.json")
    monkeypatch.setattr(config, "_bank_aliases", {})

    rotated = rotate_bank_id(alias)

    assert rotated != alias
    assert resolve_bank_id(alias) == rotated
    assert resolve_bank_id("unmapped-bank") == "unmapped-bank"


def test_default_bank_follows_rotated_configured_alias(monkeypatch) -> None:
    alias = "test-default-bank-unique"
    physical_bank = "test-default-bank-unique-demo-1"
    monkeypatch.setattr(config, "_active_bank_id", alias)
    monkeypatch.setattr(config, "_bank_aliases", {alias: physical_bank})

    assert config.resolve_bank_id() == physical_bank
    assert config.get_active_bank_id() == physical_bank


def test_seeded_bank_id_uses_its_stable_alias() -> None:
    seeded_bank = "recallap-demo-demo-20261106175321-c12d54"

    assert config.configured_bank_alias(seeded_bank) == "recallap-demo"


def test_registered_alias_survives_config_reload(monkeypatch, tmp_path) -> None:
    alias = "test-persistent-bank-unique"
    physical_bank = "test-persistent-bank-unique-demo-1"
    monkeypatch.setattr(config, "_bank_routes_path", tmp_path / "bank_routes.json")
    monkeypatch.setattr(config, "_bank_aliases", {})
    monkeypatch.setattr(config, "_active_bank_id", alias)

    config.register_bank_id(alias, physical_bank)
    monkeypatch.setattr(config, "_bank_aliases", config._load_bank_aliases())

    assert config.resolve_bank_id(alias) == physical_bank
    assert config.resolve_bank_id() == physical_bank
