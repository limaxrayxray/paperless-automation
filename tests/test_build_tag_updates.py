"""Tests de doc_processor.build_tag_updates (Phase 1, tâche 3).

Couvre :
- tags protégés jamais retirés ni ajoutés automatiquement ;
- remplacement des anciens tags de classification ;
- tag année : un seul à la fois, selon DATE_CONFIDENCE_THRESHOLD ;
- tag `a-verifier` ajouté/retiré selon GLOBAL_CONFIDENCE_THRESHOLD ;
- règle métier medical → personnel ;
- filtrage des tags_to_add hors ALLOWED_TAGS.

Note : `confidence` est fourni explicitement (>= seuil) quand le test ne porte
pas sur `a-verifier`, car son absence (défaut 0) déclencherait le tag.
"""

from config import (
    DATE_CONFIDENCE_THRESHOLD,
    GLOBAL_CONFIDENCE_THRESHOLD,
    PROTECTED_TAG_IDS,
    TAG_IDS,
    YEAR_TAG_IDS,
)
from doc_processor import build_tag_updates

HIGH = 0.95  # confiance globale au-dessus du seuil (pas de a-verifier)


# ─── Tags protégés ────────────────────────────────────────────────────────────

def test_tags_proteges_conserves():
    # Tous les tags protégés présents au départ restent présents.
    current = sorted(PROTECTED_TAG_IDS)
    result = build_tag_updates(current, {"doc_type": "facture", "confidence": HIGH})
    for tid in PROTECTED_TAG_IDS:
        assert tid in result


def test_tags_proteges_jamais_ajoutes():
    # Aucun tag protégé absent au départ n'apparaît dans le résultat.
    result = build_tag_updates([], {"doc_type": "facture", "confidence": HIGH})
    assert PROTECTED_TAG_IDS.isdisjoint(result)


def test_tag_protege_seul_preserve_avec_classification():
    current = [53, TAG_IDS["facture"]]
    result = build_tag_updates(current, {"doc_type": "recu", "confidence": HIGH})
    assert 53 in result


# ─── Remplacement des tags de classification ──────────────────────────────────

def test_ancien_type_remplace_par_nouveau():
    current = [TAG_IDS["facture"]]
    result = build_tag_updates(current, {"doc_type": "recu", "confidence": HIGH})
    assert TAG_IDS["recu"] in result
    assert TAG_IDS["facture"] not in result


def test_doc_type_invalide_n_ajoute_rien():
    result = build_tag_updates([], {"doc_type": "inconnu", "confidence": HIGH})
    assert result == []


def test_doc_type_ajoute():
    result = build_tag_updates([], {"doc_type": "contrat", "confidence": HIGH})
    assert result == [TAG_IDS["contrat"]]


# ─── tags_to_add : filtrage ALLOWED_TAGS ──────────────────────────────────────

def test_tags_to_add_autorises_ajoutes():
    result = build_tag_updates(
        [], {"doc_type": "facture", "tags_to_add": ["transport"], "confidence": HIGH}
    )
    assert TAG_IDS["transport"] in result


def test_tags_to_add_hors_allowed_ignores():
    # impots est dans TAG_IDS mais pas dans ALLOWED_TAGS → jamais ajouté.
    result = build_tag_updates(
        [], {"doc_type": "facture", "tags_to_add": ["impots"], "confidence": HIGH}
    )
    assert TAG_IDS["impots"] not in result


def test_tags_to_add_inconnu_ignore():
    result = build_tag_updates(
        [], {"doc_type": "facture", "tags_to_add": ["zzz"], "confidence": HIGH}
    )
    assert result == [TAG_IDS["facture"]]


# ─── Règle medical → personnel ────────────────────────────────────────────────

def test_medical_force_personnel():
    result = build_tag_updates(
        [], {"doc_type": "autre", "tags_to_add": ["medical"], "confidence": HIGH}
    )
    assert TAG_IDS["medical"] in result
    assert TAG_IDS["personnel"] in result


def test_doc_type_medical_force_personnel():
    result = build_tag_updates([], {"doc_type": "medical", "confidence": HIGH})
    assert TAG_IDS["medical"] in result
    assert TAG_IDS["personnel"] in result


def test_personnel_existant_jamais_retire():
    # personnel n'est pas dans ALLOWED_TAGS → jamais retiré automatiquement.
    current = [TAG_IDS["personnel"]]
    result = build_tag_updates(current, {"doc_type": "facture", "confidence": HIGH})
    assert TAG_IDS["personnel"] in result


# ─── Tag année ────────────────────────────────────────────────────────────────

def test_tag_annee_ajoute_si_confiance_haute():
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "2025-03-01",
             "date_confidence": 0.95, "confidence": HIGH}
    )
    assert YEAR_TAG_IDS[2025] in result


def test_pas_de_tag_annee_si_confiance_basse():
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "2025-03-01",
             "date_confidence": 0.5, "confidence": HIGH}
    )
    assert YEAR_TAG_IDS[2025] not in result


def test_tag_annee_borne_seuil_inclus():
    # date_confidence == seuil exact → tag ajouté (>=).
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "2025-03-01",
             "date_confidence": DATE_CONFIDENCE_THRESHOLD, "confidence": HIGH}
    )
    assert YEAR_TAG_IDS[2025] in result


def test_pas_de_tag_annee_sans_date_confidence():
    # date_confidence absent → défaut 0.0 → pas de tag année.
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "2025-03-01", "confidence": HIGH}
    )
    assert YEAR_TAG_IDS[2025] not in result


def test_un_seul_tag_annee_a_la_fois():
    # Un ancien tag année est remplacé par le nouveau.
    current = [YEAR_TAG_IDS[2025]]
    result = build_tag_updates(
        current, {"doc_type": "facture", "date": "2026-01-15",
                  "date_confidence": 0.95, "confidence": HIGH}
    )
    assert YEAR_TAG_IDS[2026] in result
    assert YEAR_TAG_IDS[2025] not in result


def test_annee_inconnue_aucun_tag_ni_crash():
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "2099-01-01",
             "date_confidence": 0.95, "confidence": HIGH}
    )
    # Aucun tag année connu, et pas d'exception.
    assert set(result).isdisjoint(YEAR_TAG_IDS.values())


def test_date_malformee_aucun_tag_ni_crash():
    result = build_tag_updates(
        [], {"doc_type": "facture", "date": "pas-une-date",
             "date_confidence": 0.95, "confidence": HIGH}
    )
    assert set(result).isdisjoint(YEAR_TAG_IDS.values())


# ─── Tag a-verifier ───────────────────────────────────────────────────────────

def test_a_verifier_ajoute_si_confiance_basse():
    result = build_tag_updates([], {"doc_type": "facture", "confidence": 0.3})
    assert TAG_IDS["a-verifier"] in result


def test_a_verifier_retire_si_confiance_haute():
    current = [TAG_IDS["a-verifier"]]
    result = build_tag_updates(current, {"doc_type": "facture", "confidence": HIGH})
    assert TAG_IDS["a-verifier"] not in result


def test_a_verifier_ajoute_si_confidence_absente():
    # confidence absent → défaut 0 < seuil → a-verifier.
    result = build_tag_updates([], {"doc_type": "facture"})
    assert TAG_IDS["a-verifier"] in result


def test_a_verifier_borne_seuil_exclu():
    # confidence == seuil exact : la condition est `< seuil` → pas de a-verifier.
    current = [TAG_IDS["a-verifier"]]
    result = build_tag_updates(
        current, {"doc_type": "facture", "confidence": GLOBAL_CONFIDENCE_THRESHOLD}
    )
    assert TAG_IDS["a-verifier"] not in result


# ─── Sortie ───────────────────────────────────────────────────────────────────

def test_resultat_trie_et_sans_doublon():
    current = [TAG_IDS["facture"], TAG_IDS["facture"], 53]
    result = build_tag_updates(current, {"doc_type": "recu", "confidence": HIGH})
    assert result == sorted(result)
    assert len(result) == len(set(result))
