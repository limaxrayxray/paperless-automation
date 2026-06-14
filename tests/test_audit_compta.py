"""Tests de la fonction pure audit_compta.summarize (aucun I/O)."""

from audit_compta import summarize

_DOC = {"id": 42, "title": "Une facture"}


def test_facture_coherente():
    analysis = {
        "doc_type": "facture", "currency": "CAD", "supplier_foreign": False,
        "total": "11.49", "tps": "0.50", "tvq": "0.99",
        "line_items": [{"description": "X", "amount": 10.00, "taxable": True}],
    }
    s = summarize(_DOC, analysis)
    assert s["doc_type"] == "facture"
    assert s["total_cents"] == 1149
    assert s["n_items"] == 1
    assert s["items_sum_cents"] == 1000
    assert s["coherent"] is True
    assert s["needs_review"] is False


def test_recu_global_sans_items():
    analysis = {
        "doc_type": "recu", "currency": "CAD",
        "total": "20.00", "tps": "0.87", "tvq": "1.74", "line_items": [],
    }
    s = summarize(_DOC, analysis)
    assert s["n_items"] == 0
    assert s["coherent"] is None          # rien à vérifier → repli ligne unique
    assert s["needs_review"] is True
    assert "items vides" in s["review_reason"]


def test_fournisseur_etranger_usd_passe_la_devise():
    analysis = {
        "doc_type": "facture", "currency": "USD", "supplier_foreign": True,
        "total": "50.00", "tps": "0.00", "tvq": "0.00",
        "line_items": [{"description": "Plan", "amount": 50.00, "taxable": False}],
    }
    s = summarize(_DOC, analysis)
    assert s["currency"] == "USD"
    assert s["supplier_foreign"] is True
    assert s["coherent"] is True           # 5000 + 0 + 0 == 5000


def test_incoherence_detectee():
    analysis = {
        "doc_type": "facture", "currency": "CAD",
        "total": "100.00", "tps": "0.00", "tvq": "0.00",
        "line_items": [{"description": "X", "amount": 10.00, "taxable": True}],
    }
    s = summarize(_DOC, analysis)
    assert s["coherent"] is False          # 1000 != 10000
    assert s["needs_review"] is True
