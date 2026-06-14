"""Tests de claude_analyzer._validate_and_clean — durcissement de la
normalisation/validation du JSON renvoyé par le modèle (Phase 1, tâche 1).

Couvre : normalisation des montants, doc_type/context invalides → défaut,
filtrage des tags hors ALLOWED_TAGS, bornage des confiances [0,1], plus la
normalisation de invoice_number / date / correspondent.

Les règles fiscales (forçage tps/tvq, drapeau d'incohérence) sont testées
séparément (Phase 1, tâche 2). Pour rester hors de ce périmètre, les tests de
montants ici utilisent doc_type="autre" (pas de forçage tps/tvq)."""

from claude_analyzer import _validate_and_clean


# ─── Montants ────────────────────────────────────────────────────────────────

def test_montant_avec_symbole_et_virgule():
    data = _validate_and_clean({"doc_type": "autre", "total": "66,81 $"})
    assert data["total"] == "66.81"


def test_montant_decimal_point_conserve():
    data = _validate_and_clean({"doc_type": "autre", "total": "1234.56"})
    assert data["total"] == "1234.56"


def test_montant_nombre_natif_formate_deux_decimales():
    data = _validate_and_clean({"doc_type": "autre", "total": 5})
    assert data["total"] == "5.00"


def test_montant_none_reste_none():
    data = _validate_and_clean({"doc_type": "autre", "total": None,
                                "tps": None, "tvq": None})
    assert data["total"] is None
    assert data["tps"] is None
    assert data["tvq"] is None


def test_montant_chaine_illisible_devient_none():
    data = _validate_and_clean({"doc_type": "autre", "total": "N/A"})
    assert data["total"] is None


def test_montants_multiples_normalises():
    data = _validate_and_clean({
        "doc_type": "autre",
        "total": "100,00 $", "tps": "5,00 $", "tvq": "9,98 $",
    })
    assert data["total"] == "100.00"
    assert data["tps"] == "5.00"
    assert data["tvq"] == "9.98"


# ─── doc_type / context ──────────────────────────────────────────────────────

def test_doc_type_invalide_devient_autre():
    data = _validate_and_clean({"doc_type": "bidon"})
    assert data["doc_type"] == "autre"


def test_doc_type_absent_devient_autre():
    data = _validate_and_clean({})
    assert data["doc_type"] == "autre"


def test_doc_type_valide_conserve():
    data = _validate_and_clean({"doc_type": "releve"})
    assert data["doc_type"] == "releve"


def test_context_invalide_devient_rapidetech():
    data = _validate_and_clean({"doc_type": "autre", "context": "bidon"})
    assert data["context"] == "rapidetech"


def test_context_personnel_conserve():
    data = _validate_and_clean({"doc_type": "autre", "context": "personnel"})
    assert data["context"] == "personnel"


# ─── Filtrage des tags ───────────────────────────────────────────────────────

def test_tags_hors_allowed_filtres():
    data = _validate_and_clean({
        "doc_type": "autre",
        "tags_to_add": ["facture", "personnel", "impots", "inexistant"],
    })
    # personnel/impots/inexistant ne sont pas dans ALLOWED_TAGS
    assert data["tags_to_add"] == ["facture"]


def test_tags_tous_valides_conserves():
    data = _validate_and_clean({
        "doc_type": "autre",
        "tags_to_add": ["recu", "medical", "Olivia"],
    })
    assert data["tags_to_add"] == ["recu", "medical", "Olivia"]


def test_tags_non_liste_devient_liste_vide():
    data = _validate_and_clean({"doc_type": "autre", "tags_to_add": "facture"})
    assert data["tags_to_add"] == []


def test_tags_absents_devient_liste_vide():
    data = _validate_and_clean({"doc_type": "autre"})
    assert data["tags_to_add"] == []


# ─── Bornage des confiances [0,1] ────────────────────────────────────────────

def test_confiance_superieure_bornee_a_un():
    data = _validate_and_clean({"doc_type": "autre",
                                "confidence": 1.5, "date_confidence": 2.0})
    assert data["confidence"] == 1.0
    assert data["date_confidence"] == 1.0


def test_confiance_negative_bornee_a_zero():
    data = _validate_and_clean({"doc_type": "autre",
                                "confidence": -0.5, "date_confidence": -3})
    assert data["confidence"] == 0.0
    assert data["date_confidence"] == 0.0


def test_confiance_illisible_par_defaut():
    data = _validate_and_clean({"doc_type": "autre",
                                "confidence": "abc", "date_confidence": None})
    assert data["confidence"] == 0.5
    assert data["date_confidence"] == 0.5


def test_confiance_valide_conservee():
    data = _validate_and_clean({"doc_type": "autre", "confidence": 0.8})
    assert data["confidence"] == 0.8


# ─── invoice_number / date / correspondent ───────────────────────────────────

def test_invoice_number_null_devient_none():
    data = _validate_and_clean({"doc_type": "autre", "invoice_number": "null"})
    assert data["invoice_number"] is None


def test_invoice_number_normalise():
    data = _validate_and_clean({"doc_type": "autre", "invoice_number": "  INV-42 "})
    assert data["invoice_number"] == "INV-42"


def test_date_valide_conservee():
    data = _validate_and_clean({"doc_type": "autre", "date": "2026-03-15",
                                "date_confidence": 0.9})
    assert data["date"] == "2026-03-15"
    assert data["date_confidence"] == 0.9


def test_date_invalide_devient_none_et_confiance_zero():
    data = _validate_and_clean({"doc_type": "autre", "date": "15 mars 2026",
                                "date_confidence": 0.9})
    assert data["date"] is None
    assert data["date_confidence"] == 0.0


def test_correspondent_null_devient_none():
    data = _validate_and_clean({"doc_type": "autre", "correspondent": "null"})
    assert data["correspondent"] is None


def test_correspondent_normalise():
    data = _validate_and_clean({"doc_type": "autre", "correspondent": "  RONA "})
    assert data["correspondent"] == "RONA"
