#!/usr/bin/env python3
"""
Enregistre les paiements Wave dans Odoo et réconcilie avec les vendor bills.

Approche: crée des écritures comptables (account.move type=entry) directement
pour contourner le problème de None return dans account.payment.action_post.

Usage:
    python3 pay_odoo_bills.py --dry-run   # voir ce qui serait fait
    python3 pay_odoo_bills.py             # enregistrer les paiements
"""

import argparse
import csv
import xmlrpc.client
from collections import defaultdict
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ODOO_URL = "https://odoo.rapidetech.ca"
ODOO_DB  = "odoo"
ODOO_UID = 2
ODOO_API_KEY = "3beef8c66decdd41f36c6d8b10d1c9390d04c137"

CSV_PATH = Path(__file__).parent / "wave_export" / "accounting.csv"

AP_ACCOUNT_ID = 230  # 221110 - Accounts Payable

# Mapping: nom compte Wave → (journal_id, account_id)
JOURNAL_MAP = {
    "Current Account (910)": (8,  415),  # 111315
    "Shareholder Loan":       (9,  416),  # 111316
    "Mastercard Cad (0319)":  (10, 417),  # 111317
    "Cash on Hand":           (7,  387),  # 111211
}

# ─── ODOO CLIENT ──────────────────────────────────────────────────────────────
_models = xmlrpc.client.ServerProxy(
    f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
)


def odoo_call(model, method, args, kwargs=None):
    return _models.execute_kw(
        ODOO_DB, ODOO_UID, ODOO_API_KEY, model, method, args, kwargs or {}
    )


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_wave_payments(year="2026") -> list[dict]:
    """Retourne les lignes AP avec débit (= paiement de bill) pour l'année donnée."""
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_txn = defaultdict(list)
    for r in rows:
        by_txn[r["Transaction ID"]].append(r)

    payments = []
    for r in rows:
        if (r["Account Type"] == "System Payable Bill"
                and r["Debit Amount (Two Column Approach)"].strip()
                and r["Transaction Date"].startswith(year)):

            txn_rows = by_txn[r["Transaction ID"]]
            pay_line = next(
                (x for x in txn_rows
                 if x["Account Type"] not in ("System Payable Bill",)
                 and x["Credit Amount (Two Column Approach)"].strip()),
                None,
            )
            pay_account = pay_line["Account Name"] if pay_line else None
            journal_info = JOURNAL_MAP.get(pay_account)

            wave_amount = float(
                r["Debit Amount (Two Column Approach)"].replace(",", "").strip()
            )

            payments.append({
                "date":         r["Transaction Date"],
                "vendor":       r["Vendor"].strip(),
                "bill_number":  r["Bill Number"].strip(),
                "wave_amount":  wave_amount,
                "pay_account":  pay_account,
                "journal_info": journal_info,  # (journal_id, account_id) ou None
            })

    return payments


def get_odoo_bills_by_ref() -> dict:
    bills = odoo_call(
        "account.move", "search_read",
        [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]],
        {"fields": ["id", "name", "ref", "partner_id", "invoice_date",
                    "amount_total", "amount_residual", "payment_state"]},
    )
    by_ref = defaultdict(list)
    for b in bills:
        if b["ref"]:
            by_ref[b["ref"].strip()].append(b)
    return by_ref


def create_payment_entry(pay_date, journal_id, account_id, amount, ref) -> int:
    """Crée et poste une écriture de paiement: débit AP / crédit compte bancaire."""
    move_id = odoo_call("account.move", "create", [{
        "move_type": "entry",
        "date":      pay_date,
        "journal_id": journal_id,
        "ref":       ref,
        "line_ids": [
            (0, 0, {
                "account_id": AP_ACCOUNT_ID,
                "debit":      amount,
                "credit":     0.0,
                "name":       ref,
            }),
            (0, 0, {
                "account_id": account_id,
                "debit":      0.0,
                "credit":     amount,
                "name":       ref,
            }),
        ],
    }])
    odoo_call("account.move", "action_post", [[move_id]])
    return move_id


def reconcile_with_bill(payment_move_id: int, bill_id: int) -> bool:
    """Réconcilie la ligne AP du paiement avec la ligne AP du bill."""
    pay_ap = odoo_call(
        "account.move.line", "search_read",
        [[["move_id", "=", payment_move_id],
          ["account_id", "=", AP_ACCOUNT_ID],
          ["reconciled", "=", False]]],
        {"fields": ["id"]},
    )
    bill_ap = odoo_call(
        "account.move.line", "search_read",
        [[["move_id", "=", bill_id],
          ["account_id", "=", AP_ACCOUNT_ID],
          ["reconciled", "=", False]]],
        {"fields": ["id"]},
    )
    if not pay_ap or not bill_ap:
        return False
    line_ids = [l["id"] for l in pay_ap] + [l["id"] for l in bill_ap]
    try:
        odoo_call("account.move.line", "reconcile", [line_ids])
    except Exception as e:
        # Odoo renvoie None (non-sérialisable) même quand la réconciliation réussit
        if "cannot marshal None" not in str(e):
            raise
    return True


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run(dry_run=False, year="2026"):
    payments = load_wave_payments(year)
    bills_by_ref = get_odoo_bills_by_ref()

    paid, skipped, errors = 0, 0, []
    bill_used = set()  # IDs déjà réconciliés

    print(f"Paiements Wave {year}: {len(payments)}")
    print()

    for p in sorted(payments, key=lambda x: x["date"]):
        bill_ref     = p["bill_number"]
        vendor       = p["vendor"]
        pay_date     = p["date"]
        wave_amount  = p["wave_amount"]
        journal_info = p["journal_info"]

        candidates = [
            b for b in bills_by_ref.get(bill_ref, [])
            if b["id"] not in bill_used and b["amount_residual"] > 0
        ]

        if not candidates:
            print(f"  ⚠  SKIP {pay_date} | {vendor:20s} | ref={bill_ref} | {wave_amount:.2f} — bill non trouvé")
            skipped += 1
            continue

        if journal_info is None:
            print(f"  ⚠  SKIP {pay_date} | {vendor:20s} | ref={bill_ref} | compte inconnu: {p['pay_account']}")
            skipped += 1
            continue

        journal_id, account_id = journal_info
        bill        = candidates[0]
        odoo_amount = bill["amount_residual"]
        ref_str     = f"Paiement {vendor} {bill_ref}".strip()

        print(
            f"  {'[DRY]' if dry_run else '    '} "
            f"{pay_date} | {vendor:20s} | ref={bill_ref:25s} | "
            f"Odoo={odoo_amount:.2f} (Wave={wave_amount:.2f}) | {p['pay_account']}"
        )

        if not dry_run:
            try:
                move_id = create_payment_entry(
                    pay_date, journal_id, account_id, odoo_amount, ref_str
                )
                reconciled = reconcile_with_bill(move_id, bill["id"])
                if reconciled:
                    paid += 1
                    bill_used.add(bill["id"])
                    print(f"         ✓ move ID={move_id} réconcilié avec bill ID={bill['id']}")
                else:
                    errors.append(f"{vendor}/{bill_ref}: move créé (ID={move_id}) mais réconciliation échouée")
                    print(f"         ✗ réconciliation échouée (move ID={move_id})")
            except Exception as e:
                errors.append(f"{vendor}/{bill_ref}: {str(e)[:120]}")
                print(f"         ✗ {str(e)[:80]}")
        else:
            paid += 1

    print(f"\nPayés: {paid} | Ignorés: {skipped} | Erreurs: {len(errors)}")
    for e in errors:
        print(f"  {e}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans modification")
    parser.add_argument("--year", default="2026", help="Année (défaut: 2026)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, year=args.year)
