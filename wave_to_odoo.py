#!/usr/bin/env python3
"""
Migration Wave → Odoo: importe les vendor bills d'un export CSV Wave vers Odoo.

Usage:
    python3 wave_to_odoo.py --year 2025   # migrer une année spécifique
    python3 wave_to_odoo.py --all         # migrer tout

Export Wave requis: Comptabilité → Transactions → Export CSV → accounting.csv
Déposer dans /opt/paperless/scripts/wave_export/accounting.csv
"""

import argparse
import csv
import sys
import xmlrpc.client
from collections import defaultdict
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ODOO_URL = "https://odoo.rapidetech.ca"
ODOO_DB = "odoo"
ODOO_UID = 2
ODOO_API_KEY = "3beef8c66decdd41f36c6d8b10d1c9390d04c137"

CSV_PATH = Path(__file__).parent / "wave_export" / "accounting.csv"

# Mapping: nom compte Wave → ID compte Odoo (série 513xxx)
ACCOUNT_MAP = {
    "Accounting Fees":               394,
    "Advertising & Promotion":       395,
    "Bank Service Charges":          396,
    "Computer – Hardware":           397,
    "Computer – Hosting":            398,
    "Computer – Internet":           399,
    "Computer – Software":           400,
    "Dues & Subscriptions":          401,
    "Meals and Entertainment":       402,
    "Office Supplies":               403,
    "Professional Fees":             404,
    "Rent Expense":                  405,
    "Repairs & Maintenance":         406,
    "Service Charge":                407,
    "Travel Expense":                408,
    "Vehicle – Fuel":                409,
    "Vehicle – Repairs & Maintenance": 410,
    "cellphone":                     411,
    "gift":                          412,
    "shipping expense":              413,
    "Cost of Goods Sold":            414,
}
DEFAULT_ACCOUNT_ID = 403  # Office Supplies (fallback)

# ─── ODOO CLIENT ──────────────────────────────────────────────────────────────
models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")


def odoo_call(model, method, args, kwargs=None):
    return models.execute_kw(ODOO_DB, ODOO_UID, ODOO_API_KEY,
                             model, method, args, kwargs or {})


def get_or_create_vendor(name: str) -> int | bool:
    name = name.strip()
    if not name:
        return False
    ids = odoo_call("res.partner", "search",
                    [[["name", "ilike", name], ["supplier_rank", ">", 0]]])
    if ids:
        return ids[0]
    return odoo_call("res.partner", "create", [{
        "name": name,
        "supplier_rank": 1,
        "company_type": "company",
    }])


# ─── MIGRATION ────────────────────────────────────────────────────────────────

def load_csv() -> list[dict]:
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def migrate(year: str | None = None):
    rows = load_csv()

    by_txn = defaultdict(list)
    for r in rows:
        by_txn[r["Transaction ID"]].append(r)

    # Sélectionner les bills (lignes Accounts Payable)
    ap_rows = [r for r in rows if r["Account Type"] == "System Payable Bill"]
    if year:
        ap_rows = [r for r in ap_rows if r["Transaction Date"].startswith(year)]

    bill_ids = {r["Transaction ID"] for r in ap_rows}
    print(f"Bills à migrer ({year or 'tout'}): {len(bill_ids)}")

    created, skipped, errors = 0, 0, []

    for txn_id in sorted(bill_ids):
        txn_rows = by_txn[txn_id]

        ap_row = next((r for r in txn_rows
                       if r["Account Type"] == "System Payable Bill"
                       and r["Credit Amount (Two Column Approach)"]), None)
        if not ap_row:
            skipped += 1
            continue  # paiement, pas une création de bill

        vendor_name = ap_row["Vendor"].strip()
        bill_date = ap_row["Transaction Date"]
        bill_number = ap_row["Bill Number"].strip()
        description = ap_row["Transaction Description"].strip()

        # Lignes de dépense
        exp_rows = [r for r in txn_rows
                    if r["Account Type"] in ("Operating Expense", "Cost of Goods Sold")]
        if not exp_rows:
            skipped += 1
            continue

        vendor_id = get_or_create_vendor(vendor_name) if vendor_name else False

        line_items = []
        for exp in exp_rows:
            amt_str = (exp.get("Debit Amount (Two Column Approach)") or "0").replace(",", "").strip()
            try:
                amount = float(amt_str)
            except ValueError:
                amount = 0.0
            if amount <= 0:
                continue
            acc_id = ACCOUNT_MAP.get(exp["Account Name"], DEFAULT_ACCOUNT_ID)
            line_items.append({
                "name": exp.get("Transaction Line Description") or description,
                "account_id": acc_id,
                "price_unit": amount,
                "quantity": 1.0,
            })

        if not line_items:
            skipped += 1
            continue

        vendor_clean = vendor_name.replace("/", "-")
        bill_name = f"{vendor_clean}/{bill_number}" if bill_number else vendor_clean

        try:
            bill_id = odoo_call("account.move", "create", [{
                "move_type": "in_invoice",
                "name": bill_name,
                "partner_id": vendor_id,
                "invoice_date": bill_date,
                "ref": bill_number,
                "invoice_line_ids": [(0, 0, line) for line in line_items],
            }])
            # Confirmer
            odoo_call("account.move", "action_post", [[bill_id]])
            created += 1
            print(f"  ✓ {bill_date} | {vendor_name:20s} | {bill_name}")
        except Exception as e:
            errors.append(f"{bill_name}: {str(e)[:120]}")
            print(f"  ✗ {bill_name}: {str(e)[:80]}")

    print(f"\nCréés: {created} | Ignorés: {skipped} | Erreurs: {len(errors)}")
    for e in errors:
        print(f"  {e}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--year", help="Année à migrer (ex: 2025)")
    group.add_argument("--all", action="store_true", help="Migrer tout")
    args = parser.parse_args()

    migrate(year=args.year if not args.all else None)
