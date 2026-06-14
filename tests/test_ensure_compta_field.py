"""Tests de `ensure_compta_field` — création idempotente du champ `compta_json`
et injection de son id dans CUSTOM_FIELD_IDS (config.py).

Aucun appel réseau : `find_or_create_custom_field` est testé via monkeypatch des
fonctions réseau, et l'injection dans config.py est une transformation de texte pure.
"""

import ensure_compta_field as ecf
import paperless_client
import pytest

# ─── find_or_create_custom_field (idempotence, sans réseau) ───────────────────

def test_find_existing_field_case_insensitive_never_creates(monkeypatch):
    monkeypatch.setattr(
        paperless_client,
        "get_custom_fields",
        lambda: [{"id": 99, "name": "Compta_JSON", "data_type": "longtext"}],
    )
    created = []
    monkeypatch.setattr(
        paperless_client,
        "create_custom_field",
        lambda name, data_type="string": created.append((name, data_type)),
    )

    field = paperless_client.find_or_create_custom_field("compta_json", "longtext")

    assert field["id"] == 99
    assert created == []  # jamais de doublon


def test_creates_field_when_absent(monkeypatch):
    monkeypatch.setattr(paperless_client, "get_custom_fields", list)
    calls = []

    def fake_create(name, data_type="string"):
        calls.append((name, data_type))
        return {"id": 42, "name": name, "data_type": data_type}

    monkeypatch.setattr(paperless_client, "create_custom_field", fake_create)

    field = paperless_client.find_or_create_custom_field("compta_json", "longtext")

    assert field["id"] == 42
    assert calls == [("compta_json", "longtext")]


def test_find_or_create_idempotent_after_creation(monkeypatch):
    """Une fois le champ présent, un second appel ne crée rien (idempotence)."""
    store = []
    monkeypatch.setattr(paperless_client, "get_custom_fields", lambda: list(store))
    creations = []

    def fake_create(name, data_type="string"):
        creations.append(name)
        field = {"id": 7, "name": name, "data_type": data_type}
        store.append(field)
        return field

    monkeypatch.setattr(paperless_client, "create_custom_field", fake_create)

    first = paperless_client.find_or_create_custom_field("compta_json", "longtext")
    second = paperless_client.find_or_create_custom_field("compta_json", "longtext")

    assert first["id"] == second["id"] == 7
    assert creations == ["compta_json"]  # créé exactement une fois


# ─── inject_field_id_into_config (transformation de texte pure) ───────────────

SAMPLE_CONFIG = (
    "import os\n\n"
    "CUSTOM_FIELD_IDS = {\n"
    '    "TPS": 13,\n'
    '    "TVQ": 14,\n'
    '    "Total": 15,\n'
    '    "Facture": 16,  # Numéro de facture\n'
    "}\n\n"
    "AUTRE = 1\n"
)


def _exec_field_ids(source: str, file: str = "config_under_test") -> dict:
    # `__file__` fourni : le vrai config.py l'utilise dans _load_dotenv().
    ns: dict = {"__file__": file}
    exec(compile(source, "config_under_test", "exec"), ns)
    return ns["CUSTOM_FIELD_IDS"]


def test_inject_adds_entry_when_absent():
    updated = ecf.inject_field_id_into_config(SAMPLE_CONFIG, "compta_json", 42)
    fields = _exec_field_ids(updated)

    assert fields["compta_json"] == 42
    # entrées existantes préservées
    assert fields["Total"] == 15
    assert fields["Facture"] == 16
    # code hors bloc intact
    assert "AUTRE = 1" in updated


def test_inject_updates_existing_value():
    base = ecf.inject_field_id_into_config(SAMPLE_CONFIG, "compta_json", 7)
    updated = ecf.inject_field_id_into_config(base, "compta_json", 42)

    fields = _exec_field_ids(updated)
    assert fields["compta_json"] == 42
    # une seule occurrence de la clé (pas de doublon inséré)
    assert updated.count('"compta_json"') == 1


def test_inject_idempotent_same_id():
    once = ecf.inject_field_id_into_config(SAMPLE_CONFIG, "compta_json", 42)
    twice = ecf.inject_field_id_into_config(once, "compta_json", 42)
    assert once == twice


def test_inject_raises_without_block():
    with pytest.raises(ValueError):
        ecf.inject_field_id_into_config("x = 1\n", "compta_json", 42)


def test_inject_into_real_config_parses():
    """La transformation appliquée au vrai config.py produit un Python valide où
    CUSTOM_FIELD_IDS['compta_json'] vaut bien l'id injecté, sans casser le reste."""
    source = ecf.CONFIG_PATH.read_text(encoding="utf-8")
    updated = ecf.inject_field_id_into_config(source, "compta_json", 42)

    fields = _exec_field_ids(updated, file=str(ecf.CONFIG_PATH))
    assert fields["compta_json"] == 42
    # entrées héritées préservées
    assert fields["TPS"] == 13
    assert fields["Total"] == 15
    assert fields["Facture"] == 16
