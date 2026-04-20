"""Client Dolibarr REST API — création automatique de factures fournisseurs."""

import os
from pathlib import Path

import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DOLIBARR_URL    = os.environ.get("DOLIBARR_URL", "").rstrip("/")
DOLIBARR_API_KEY = os.environ.get("DOLIBARR_API_KEY", "")

# Produit générique réutilisé pour toutes les lignes de facture
SERVICE_PRODUCT_REF = "SERVICE"
_service_product_id: int | None = None  # cache en mémoire

# Mapping tag Paperless → description de catégorie
TAG_TO_CATEGORY = {
    "internet":  "Computer – Internet",
    "telephone": "Telephone – Wireless",
    "transport": "Vehicle – Fuel",
    "assurance": "Insurance",
    "contrat":   "Professional Fees",
}
DEFAULT_CATEGORY = "Dépense opérationnelle"


# ─── HTTP ──────────────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> list | dict:
    r = requests.get(
        f"{DOLIBARR_URL}/api/index.php/{path}",
        headers={"DOLAPIKEY": DOLIBARR_API_KEY},
        params=params or {},
        timeout=15,
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


def _post(path: str, data: dict) -> int | dict:
    r = requests.post(
        f"{DOLIBARR_URL}/api/index.php/{path}",
        headers={"DOLAPIKEY": DOLIBARR_API_KEY, "Content-Type": "application/json"},
        json=data,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ─── PRODUITS ──────────────────────────────────────────────────────────────────

_product_cache: dict[str, int] = {}  # label.lower() → id


def get_or_create_product(label: str) -> int:
    """
    Retourne l'ID du produit Dolibarr par son libellé.
    Cherche d'abord par label exact (insensible à la casse), crée si absent.
    Le produit SERVICE générique est utilisé comme fallback.
    """
    key = label.strip().lower()
    if key in _product_cache:
        return _product_cache[key]

    # Chercher dans Dolibarr (recherche par tous les produits, filtre en Python)
    results = _get("products", {"limit": 500, "mode": 1})
    if isinstance(results, list):
        for p in results:
            if (p.get("label") or "").strip().lower() == key:
                pid = int(p["id"])
                _product_cache[key] = pid
                return pid

    # Créer le produit avec ce libellé
    # Générer une ref unique à partir du label (max 30 chars, alphanum+tiret)
    import re
    ref = re.sub(r"[^A-Z0-9\-]", "-", label.strip().upper())[:30].strip("-")
    if not ref:
        ref = "SERVICE"

    try:
        pid = int(_post("products", {
            "ref":            ref,
            "label":          label.strip(),
            "type":           1,       # service
            "status":         1,
            "status_buy":     1,
            "tva_tx":         5,
            "localtax1_tx":   9.975,
            "localtax1_type": "1",
        }))
    except Exception:
        # Ref déjà prise (collision) → ajouter suffixe numérique
        import time
        ref = (ref[:25] + f"-{int(time.time()) % 10000}").strip("-")
        pid = int(_post("products", {
            "ref":            ref,
            "label":          label.strip(),
            "type":           1,
            "status":         1,
            "status_buy":     1,
            "tva_tx":         5,
            "localtax1_tx":   9.975,
            "localtax1_type": "1",
        }))

    _product_cache[key] = pid
    return pid


def get_or_create_service_product() -> int:
    """Produit SERVICE générique — fallback quand pas de ligne détaillée."""
    global _service_product_id
    if _service_product_id is not None:
        return _service_product_id
    _service_product_id = get_or_create_product("Service")
    return _service_product_id


# ─── FOURNISSEURS ──────────────────────────────────────────────────────────────

def get_or_create_supplier(name: str) -> int:
    """Retourne l'ID Dolibarr du fournisseur, le crée si inexistant."""
    name = name.strip()
    results = _get("thirdparties", {"sqlfilters": f"(t.nom:like:'{name}')", "limit": 5})
    if isinstance(results, list) and results:
        for r in results:
            if r.get("fournisseur") in ("1", 1):
                return int(r["id"])
        return int(results[0]["id"])

    supplier_id = _post("thirdparties", {
        "name":             name,
        "fournisseur":      1,
        "client":           0,
        "code_fournisseur": "auto",
        "country_code":     "CA",
    })
    return int(supplier_id)


# ─── DÉDUPLICATION ────────────────────────────────────────────────────────────

def invoice_exists(supplier_name: str, invoice_ref: str, date_str: str) -> bool:
    """
    Vérifie si une facture fournisseur existe déjà.
    Déduplication sur ref_supplier seule — les numéros de facture sont uniques par transaction.
    Même ref + date différente (mois différent) = la ref serait différente de toute façon.
    """
    if not invoice_ref:
        return False
    try:
        results = _get("supplierinvoices", {"limit": 500})
        if not isinstance(results, list):
            return False
        ref = str(invoice_ref).strip()
        for inv in results:
            if str(inv.get("ref_supplier", "")).strip() == ref:
                return True
        return False
    except Exception:
        return False


def _timestamp_to_date(ts) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


# ─── FACTURES FOURNISSEURS ─────────────────────────────────────────────────────

def _compute_pretax(total: str | None, tps: str | None, tvq: str | None) -> float:
    if total is None:
        return 0.0
    total_f = float(total)
    tps_f   = float(tps) if tps else 0.0
    tvq_f   = float(tvq) if tvq else 0.0
    if tps_f > 0 or tvq_f > 0:
        return round(total_f - tps_f - tvq_f, 2)
    return total_f


def _date_to_timestamp(date_str: str) -> int:
    from datetime import datetime, timezone
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return int(__import__("datetime").datetime.now(__import__("datetime").timezone.utc).timestamp())


def _build_line(description: str, amount: float, taxable: bool,
                has_tps: bool = True, has_tvq: bool = True) -> dict:
    """Construit une ligne de facture Dolibarr."""
    product_id   = get_or_create_product(description)
    tva_tx       = 5.0   if (taxable and has_tps) else 0.0
    localtax1_tx = 9.975 if (taxable and has_tvq) else 0.0
    return {
        "fk_product":     product_id,
        "product_type":   1,
        "desc":           description,
        "subprice":       round(amount, 2),
        "qty":            1,
        "tva_tx":         tva_tx,
        "localtax1_tx":   localtax1_tx,
        "localtax1_type": "1",
    }


def create_supplier_invoice(
    supplier_name: str,
    date: str,
    invoice_ref: str | None,
    total: str | None,
    tps: str | None,
    tvq: str | None,
    line_items: list[dict],
    tags: list[str],
    doc_title: str,
    paperless_doc_id: int,
) -> int:
    """Crée une facture fournisseur dans Dolibarr. Retourne l'ID créé."""
    supplier_id = get_or_create_supplier(supplier_name)

    tps_f  = float(tps) if tps else 0.0
    tvq_f  = float(tvq) if tvq else 0.0
    any_tax = tps_f > 0 or tvq_f > 0

    # Lignes détaillées si Claude les a extraites
    has_tps = tps_f > 0
    has_tvq = tvq_f > 0
    if line_items:
        lines = [_build_line(item["description"], item["amount"], item["taxable"],
                             has_tps=has_tps, has_tvq=has_tvq) for item in line_items]
    else:
        # Fallback: une ligne générique avec le montant HT
        pretax = _compute_pretax(total, tps, tvq)
        category = DEFAULT_CATEGORY
        for tag in tags:
            if tag in TAG_TO_CATEGORY:
                category = TAG_TO_CATEGORY[tag]
                break
        desc = f"{category} — {doc_title} (Paperless #{paperless_doc_id})"
        lines = [_build_line(desc, pretax if pretax > 0 else float(total or 0), any_tax,
                             has_tps=has_tps, has_tvq=has_tvq)]

    invoice_id = _post("supplierinvoices", {
        "socid":        supplier_id,
        "date":         _date_to_timestamp(date),
        "ref_supplier": invoice_ref or "",
        "lines":        lines,
        "status":       0,  # brouillon
    })
    return int(invoice_id)


# ─── POINT D'ENTRÉE (test manuel) ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    status = _get("status")
    print(f"Dolibarr {status.get('success', {}).get('dolibarr_version', '?')} — connecté")

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        inv_id = create_supplier_invoice(
            supplier_name    = "Test Fournisseur",
            date             = "2026-03-17",
            invoice_ref      = "TEST-002",
            total            = "113.00",
            tps              = "5.00",
            tvq              = "9.975",
            tags             = ["internet"],
            doc_title        = "Test facture",
            paperless_doc_id = 0,
        )
        print(f"Facture créée: ID={inv_id}")
        print(f"→ {DOLIBARR_URL}/fourn/facture/card.php?id={inv_id}")
