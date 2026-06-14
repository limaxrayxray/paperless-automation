"""Tests de claude_analyzer._validate_and_clean — règles fiscales (Phase 1, tâche 2).

Couvre :
- forçage tps/tvq à « 0.00 » quand None, UNIQUEMENT pour doc_type facture/recu
  (les autres types laissent tps/tvq à None) ;
- drapeau d'incohérence fiscale : pour facture/recu, si total > 20 $ et tvq = 0.00,
  la confiance est abaissée (≤ 0.60) et une note d'attention est ajoutée, en
  respectant la borne (boundary 20.00 $) et le non-déclenchement quand tvq ≠ 0.00.
"""

from claude_analyzer import _validate_and_clean


# ─── Forçage tps/tvq à 0.00 pour facture/recu ────────────────────────────────

def test_facture_tps_tvq_none_forces_a_zero():
    data = _validate_and_clean({"doc_type": "facture", "total": "10.00",
                                "tps": None, "tvq": None})
    assert data["tps"] == "0.00"
    assert data["tvq"] == "0.00"


def test_recu_tps_tvq_none_forces_a_zero():
    data = _validate_and_clean({"doc_type": "recu", "total": "10.00",
                                "tps": None, "tvq": None})
    assert data["tps"] == "0.00"
    assert data["tvq"] == "0.00"


def test_facture_tps_seul_none_force_l_autre_conserve():
    data = _validate_and_clean({"doc_type": "facture", "total": "10.00",
                                "tps": None, "tvq": "0.50"})
    assert data["tps"] == "0.00"
    assert data["tvq"] == "0.50"


def test_facture_tps_tvq_fournis_non_ecrases():
    data = _validate_and_clean({"doc_type": "facture", "total": "115.00",
                                "tps": "5.00", "tvq": "9.98"})
    assert data["tps"] == "5.00"
    assert data["tvq"] == "9.98"


def test_releve_tps_tvq_none_restent_none():
    data = _validate_and_clean({"doc_type": "releve", "total": "1000.00",
                                "tps": None, "tvq": None})
    assert data["tps"] is None
    assert data["tvq"] is None


def test_autre_tps_tvq_none_restent_none():
    data = _validate_and_clean({"doc_type": "autre", "total": "1000.00",
                                "tps": None, "tvq": None})
    assert data["tps"] is None
    assert data["tvq"] is None


# ─── Drapeau d'incohérence fiscale (total > 20 $ et tvq = 0.00) ───────────────

def test_facture_total_eleve_tvq_zero_abaisse_confiance():
    data = _validate_and_clean({"doc_type": "facture", "total": "100.00",
                                "tps": None, "tvq": None, "confidence": 0.95})
    # tvq forcé à 0.00 puis incohérence détectée → confiance bornée à 0.60
    assert data["tvq"] == "0.00"
    assert data["confidence"] == 0.60
    assert "ATTENTION" in data["notes"]


def test_recu_total_eleve_tvq_zero_abaisse_confiance():
    data = _validate_and_clean({"doc_type": "recu", "total": "55.00",
                                "tps": None, "tvq": "0.00", "confidence": 0.9})
    assert data["confidence"] == 0.60
    assert "ATTENTION" in data["notes"]


def test_incoherence_n_augmente_pas_une_confiance_basse():
    # min() : une confiance déjà inférieure à 0.60 n'est pas remontée
    data = _validate_and_clean({"doc_type": "facture", "total": "100.00",
                                "tps": None, "tvq": None, "confidence": 0.30})
    assert data["confidence"] == 0.30
    assert "ATTENTION" in data["notes"]


def test_note_attention_preserve_la_note_existante():
    data = _validate_and_clean({"doc_type": "facture", "total": "100.00",
                                "tps": None, "tvq": None,
                                "notes": "Reçu thermique pâle"})
    assert data["notes"].startswith("Reçu thermique pâle")
    assert "ATTENTION" in data["notes"]


def test_pas_de_drapeau_si_total_petit():
    data = _validate_and_clean({"doc_type": "recu", "total": "15.00",
                                "tps": None, "tvq": None, "confidence": 0.9})
    # total <= 20 $ → pas d'incohérence
    assert data["confidence"] == 0.9
    assert "ATTENTION" not in data.get("notes", "")


def test_pas_de_drapeau_a_la_borne_20():
    # total == 20.00 n'est pas > 20.0 → pas de drapeau
    data = _validate_and_clean({"doc_type": "facture", "total": "20.00",
                                "tps": None, "tvq": None, "confidence": 0.9})
    assert data["confidence"] == 0.9
    assert "ATTENTION" not in data.get("notes", "")


def test_drapeau_juste_au_dessus_de_la_borne():
    data = _validate_and_clean({"doc_type": "facture", "total": "20.01",
                                "tps": None, "tvq": None, "confidence": 0.9})
    assert data["confidence"] == 0.60
    assert "ATTENTION" in data["notes"]


def test_pas_de_drapeau_si_tvq_non_nulle():
    data = _validate_and_clean({"doc_type": "facture", "total": "115.00",
                                "tps": "5.00", "tvq": "9.98", "confidence": 0.9})
    assert data["confidence"] == 0.9
    assert "ATTENTION" not in data.get("notes", "")


def test_pas_de_drapeau_si_total_none():
    data = _validate_and_clean({"doc_type": "facture", "total": None,
                                "tps": None, "tvq": None, "confidence": 0.9})
    assert data["confidence"] == 0.9
    assert "ATTENTION" not in data.get("notes", "")


def test_pas_de_drapeau_pour_releve_meme_si_total_eleve():
    # releve : tvq reste None (pas forcé), donc condition tvq=="0.00" fausse
    data = _validate_and_clean({"doc_type": "releve", "total": "1000.00",
                                "tps": None, "tvq": None, "confidence": 0.9})
    assert data["confidence"] == 0.9
    assert "ATTENTION" not in data.get("notes", "")
