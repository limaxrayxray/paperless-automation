"""Tests de doc_processor.build_custom_fields (Phase 1, tâche 4).

Couvre :
- TPS/TVQ/Total/Facture écrits seulement pour les `doc_type` pertinents ;
- types non pertinents (rapport, manuel, ...) → aucun champ écrit ;
- valeurs existantes préservées (et écrasées seulement par une nouvelle valeur) ;
- aucune valeur None écrite ; `invoice_number` falsy (None/"") ignoré.

Le résultat est une liste de dicts {"field": id, "value": v} ; on la réindexe
par id pour des assertions stables (l'ordre n'est pas garanti).
"""

from config import CUSTOM_FIELD_IDS
from doc_processor import build_custom_fields

TPS_ID = CUSTOM_FIELD_IDS["TPS"]
TVQ_ID = CUSTOM_FIELD_IDS["TVQ"]
TOTAL_ID = CUSTOM_FIELD_IDS["Total"]
FACTURE_ID = CUSTOM_FIELD_IDS["Facture"]
# compta_json (s'il est configuré) est écrit pour TOUS les documents — c'est le
# contrat d'unification, indépendant des champs hérités testés ici. On l'isole.
COMPTA_ID = CUSTOM_FIELD_IDS.get("compta_json")

RELEVANT_TYPES = ("facture", "recu", "releve", "contrat", "assurance", "autre")
IRRELEVANT_TYPES = ("rapport", "manuel", "personnel", "medical", "", None)


def _by_id(payload: list[dict]) -> dict:
    """Réindexe la liste custom_fields par field id → valeur."""
    return {cf["field"]: cf["value"] for cf in payload}


def _legacy(fields: dict) -> dict:
    """Retire le champ compta_json pour n'asserter que sur les champs hérités."""
    return {k: v for k, v in fields.items() if k != COMPTA_ID}


# ─── Types pertinents : écriture des montants ────────────────────────────────

def test_facture_ecrit_tous_les_champs():
    analysis = {
        "doc_type": "facture",
        "tps": "5.00",
        "tvq": "9.98",
        "total": "114.98",
        "invoice_number": "INV-001",
    }
    fields = _by_id(build_custom_fields([], analysis))
    assert fields[TPS_ID] == "5.00"
    assert fields[TVQ_ID] == "9.98"
    assert fields[TOTAL_ID] == "114.98"
    assert fields[FACTURE_ID] == "INV-001"


def test_tous_les_types_pertinents_ecrivent():
    for doc_type in RELEVANT_TYPES:
        analysis = {"doc_type": doc_type, "total": "10.00"}
        fields = _by_id(build_custom_fields([], analysis))
        assert fields.get(TOTAL_ID) == "10.00", doc_type


# ─── Types non pertinents : aucun champ écrit ────────────────────────────────

def test_types_non_pertinents_aucun_champ():
    for doc_type in IRRELEVANT_TYPES:
        analysis = {
            "doc_type": doc_type,
            "tps": "5.00",
            "tvq": "9.98",
            "total": "114.98",
            "invoice_number": "INV-001",
        }
        # Aucun champ HÉRITÉ écrit pour un type non pertinent (compta_json à part).
        assert _legacy(_by_id(build_custom_fields([], analysis))) == {}, doc_type


def test_type_non_pertinent_preserve_existant():
    # Un type non pertinent n'écrit aucun champ hérité mais ne détruit pas l'existant.
    existing = [{"field": TOTAL_ID, "value": "99.99"}]
    fields = _legacy(_by_id(build_custom_fields(existing, {"doc_type": "rapport", "total": "1.00"})))
    assert fields == {TOTAL_ID: "99.99"}


# ─── Aucune valeur None écrite ───────────────────────────────────────────────

def test_montants_none_non_ecrits():
    analysis = {
        "doc_type": "facture",
        "tps": None,
        "tvq": None,
        "total": "50.00",
        "invoice_number": None,
    }
    fields = _legacy(_by_id(build_custom_fields([], analysis)))
    assert fields == {TOTAL_ID: "50.00"}
    assert TPS_ID not in fields
    assert TVQ_ID not in fields
    assert FACTURE_ID not in fields


def test_champs_absents_non_ecrits():
    # Clés absentes de l'analyse → traitées comme None, aucun champ hérité écrit.
    fields = _legacy(_by_id(build_custom_fields([], {"doc_type": "facture"})))
    assert fields == {}


def test_invoice_number_vide_ignore():
    # "" est falsy → Facture non écrit (le code teste la véracité, pas is None).
    fields = _by_id(build_custom_fields([], {"doc_type": "facture", "invoice_number": ""}))
    assert FACTURE_ID not in fields


# ─── Préservation et écrasement des valeurs existantes ───────────────────────

def test_valeurs_existantes_preservees():
    # Un champ existant non touché par l'analyse est conservé tel quel.
    existing = [{"field": FACTURE_ID, "value": "OLD-123"}]
    fields = _by_id(build_custom_fields(existing, {"doc_type": "facture", "total": "20.00"}))
    assert fields[FACTURE_ID] == "OLD-123"
    assert fields[TOTAL_ID] == "20.00"


def test_nouvelle_valeur_ecrase_existante():
    existing = [{"field": TOTAL_ID, "value": "10.00"}]
    fields = _by_id(build_custom_fields(existing, {"doc_type": "facture", "total": "25.00"}))
    assert fields[TOTAL_ID] == "25.00"


def test_existant_non_ecrase_par_none():
    # Une valeur existante n'est jamais effacée par un None de l'analyse.
    existing = [{"field": TPS_ID, "value": "5.00"}]
    fields = _by_id(build_custom_fields(existing, {"doc_type": "facture", "tps": None}))
    assert fields[TPS_ID] == "5.00"
