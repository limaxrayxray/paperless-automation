#!/usr/bin/env python3
"""
Correction complète des bills Odoo 2026 :
1. Supprime les écritures de paiement (moves 78-115)
2. Remet chaque bill en brouillon
3. Ajoute les taxes (TPS+TVQ 14.975% ou exempt) par bill
4. Corrige les montants des bills mal importés
5. Reposte tous les bills
6. Crée le 2e bill CA62BZX2H9TI manquant
7. Refait tous les paiements Wave

Usage:
    python3 fix_odoo_taxes.py --dry-run
    python3 fix_odoo_taxes.py
"""
import argparse
import csv
import xmlrpc.client
from collections import defaultdict
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ODOO_URL    = "https://odoo.rapidetech.ca"
ODOO_DB     = "odoo"
ODOO_UID    = 2
ODOO_API_KEY = "3beef8c66decdd41f36c6d8b10d1c9390d04c137"

CSV_PATH = Path(__file__).parent / "wave_export" / "accounting.csv"

AP_ACCOUNT_ID  = 230   # 221110 Accounts Payable
TAX_14975_ID   = 30    # 14.975% GST+QST group
ACCOUNT_CELLPHONE = 411  # 513480 cellphone

JOURNAL_MAP = {
    "Current Account (910)": (8,  415),
    "Shareholder Loan":       (9,  416),
    "Mastercard Cad (0319)":  (10, 417),
    "Cash on Hand":           (7,  387),
}

# Bills exempts de taxes (basé sur données Wave)
EXEMPT_REFS = {
    "9700",        # maxi
    "3962",        # maxi
    "5727",        # maxi
    "CA61Y0CS8I",  # amazon (0% dans Wave)
    "3992394695",  # Godaddy (US, pas de TPS/TVQ)
    "CA684UIDV1I", # amazon (0% dans Wave)
    "IN 56259955", # CloudFlare (US, pas de TPS/TVQ)
}

# ─── ODOO CLIENT ──────────────────────────────────────────────────────────────
_models = xmlrpc.client.ServerProxy(
    f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
)

def call(model, method, args, kwargs=None):
    return _models.execute_kw(
        ODOO_DB, ODOO_UID, ODOO_API_KEY, model, method, args, kwargs or {}
    )

def call_ignore_none(model, method, args, kwargs=None):
    """Appelle Odoo et ignore l'erreur 'cannot marshal None' (opération réussie côté serveur)."""
    try:
        return call(model, method, args, kwargs)
    except Exception as e:
        if "cannot marshal None" in str(e):
            return None
        raise

# ─── STEP 1: SUPPRIMER PAIEMENTS ──────────────────────────────────────────────

def delete_payment_moves():
    moves = call("account.move", "search",
                 [[["id", ">=", 78], ["move_type", "=", "entry"]]])
    if not moves:
        print("Aucun move paiement à supprimer.")
        return
    print(f"Suppression de {len(moves)} écritures de paiement (IDs {min(moves)}-{max(moves)})...")
    # Remettre en brouillon
    call_ignore_none("account.move", "button_draft", [moves])
    # Supprimer
    call("account.move", "unlink", [moves])
    print(f"  ✓ {len(moves)} moves supprimés")

# ─── STEP 2-5: CORRIGER ET RETAXER LES BILLS ──────────────────────────────────

def reset_to_draft(bill_id):
    call_ignore_none("account.move", "button_draft", [[bill_id]])

def get_product_lines(bill_id):
    return call("account.move.line", "search_read",
                [[["move_id", "=", bill_id], ["display_type", "=", "product"]]],
                {"fields": ["id", "name", "account_id", "price_unit", "quantity"]})

def fix_and_retax_bills(dry_run=False):
    bills = call("account.move", "search_read",
                 [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]],
                 {"fields": ["id", "name", "ref", "invoice_line_ids"]})

    print(f"\n{'─'*60}")
    print(f"Correction taxes sur {len(bills)} bills")
    print(f"{'─'*60}")

    for bill in sorted(bills, key=lambda b: b["id"]):
        bill_id = bill["id"]
        ref     = (bill["ref"] or "").strip()
        taxable = ref not in EXEMPT_REFS

        print(f"\n  Bill {bill_id} ({bill['name']}) ref={ref} → {'14.975%' if taxable else '0% exempt'}")

        if dry_run:
            continue

        reset_to_draft(bill_id)
        lines = get_product_lines(bill_id)

        # ── Cas spéciaux ──────────────────────────────────────────────────────

        if ref == "CA6ZKBO7QZI":
            # Montant incorrect : $218.44 (AP Wave) → $189.99 (pré-taxe)
            if lines:
                call("account.move.line", "write", [[lines[0]["id"]], {
                    "price_unit": 189.99,
                    "tax_ids": [(6, 0, [TAX_14975_ID])],
                }])
            print(f"    ✓ Corrigé : $218.44 → $189.99 + 14.975%")

        elif ref == "ACCU-INV-CA-2026-11762975":
            # Montant incorrect : $11.49 (AP Wave) → $9.99 (pré-taxe)
            if lines:
                call("account.move.line", "write", [[lines[0]["id"]], {
                    "price_unit": 9.99,
                    "tax_ids": [(6, 0, [TAX_14975_ID])],
                }])
            print(f"    ✓ Corrigé : $11.49 → $9.99 + 14.975%")

        elif ref == "39691055 - jan":
            # Telus : split en ligne taxable ($80.53) + exempt ($35.96)
            if lines:
                line_id = lines[0]["id"]
                # Modifier la ligne existante → taxable
                call("account.move.line", "write", [[line_id], {
                    "price_unit": 80.53,
                    "tax_ids": [(6, 0, [TAX_14975_ID])],
                    "name": "Telus - services taxables",
                }])
                # Ajouter ligne exempt
                call("account.move", "write", [[bill_id], {
                    "invoice_line_ids": [(0, 0, {
                        "name": "Telus - services exempts",
                        "account_id": ACCOUNT_CELLPHONE,
                        "price_unit": 35.96,
                        "quantity": 1.0,
                        "tax_ids": [(6, 0, [])],
                    })]
                }])
            print(f"    ✓ Split : $80.53 taxable + $35.96 exempt")

        elif ref == "001325":
            # Retro Active : 2 lignes existantes, toutes taxables
            for line in lines:
                call("account.move.line", "write", [[line["id"]], {
                    "tax_ids": [(6, 0, [TAX_14975_ID])],
                }])
            print(f"    ✓ {len(lines)} lignes → 14.975%")

        else:
            # Cas standard : ajouter tax_ids sur toutes les lignes
            for line in lines:
                tax_ids = [(6, 0, [TAX_14975_ID])] if taxable else [(6, 0, [])]
                call("account.move.line", "write", [[line["id"]], {
                    "tax_ids": tax_ids,
                }])
            print(f"    ✓ {len(lines)} lignes mises à jour")

        # Reposter
        call("account.move", "action_post", [[bill_id]])

    # Vérification
    if not dry_run:
        bills_check = call("account.move", "search_read",
                           [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]],
                           {"fields": ["id", "ref", "amount_total", "amount_untaxed"]})
        print(f"\n{'─'*60}")
        print("Vérification totaux après taxes :")
        for b in sorted(bills_check, key=lambda x: x["id"]):
            print(f"  {b['id']:3d} | ref={b['ref']:25s} | HT={b['amount_untaxed']:8.2f} | TTC={b['amount_total']:8.2f}")

# ─── STEP 6: CRÉER LE 2E BILL CA62BZX2H9TI ────────────────────────────────────

def create_missing_amazon_bill(dry_run=False):
    print(f"\n{'─'*60}")
    print("Création du 2e bill Amazon CA62BZX2H9TI ($18.99 + $26.98 + taxes)")
    if dry_run:
        print("  [DRY] Serait créé : amazon CA62BZX2H9TI = $18.99 Office + $26.98 Equipment + 14.975%")
        return

    # Trouver le partner amazon
    partners = call("res.partner", "search_read",
                    [[["name", "ilike", "amazon"], ["supplier_rank", ">", 0]]],
                    {"fields": ["id", "name"]})
    amazon_id = partners[0]["id"] if partners else False
    print(f"  Partner amazon: {partners[0]['name'] if partners else 'non trouvé'}")

    bill_id = call("account.move", "create", [{
        "move_type":    "in_invoice",
        "partner_id":   amazon_id,
        "invoice_date": "2026-01-22",
        "ref":          "CA62BZX2H9TI",
        "invoice_line_ids": [
            (0, 0, {
                "name":      "Amazon CA62BZX2H9TI - Office Supplies",
                "account_id": 403,   # Office Supplies
                "price_unit": 18.99,
                "quantity":   1.0,
                "tax_ids":    [(6, 0, [TAX_14975_ID])],
            }),
            (0, 0, {
                "name":      "Amazon CA62BZX2H9TI - Equipement",
                "account_id": 122,   # Machinery and Equipment
                "price_unit": 26.98,
                "quantity":   1.0,
                "tax_ids":    [(6, 0, [TAX_14975_ID])],
            }),
        ],
    }])
    call("account.move", "action_post", [[bill_id]])
    result = call("account.move", "read", [[bill_id]], {"fields": ["name", "amount_total"]})
    print(f"  ✓ Bill créé: {result[0]['name']} | TTC={result[0]['amount_total']:.2f} (attendu ~52.85)")
    return bill_id

# ─── STEP 7: PAIEMENTS ────────────────────────────────────────────────────────

def load_wave_payments(year="2026"):
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
            pay_account  = pay_line["Account Name"] if pay_line else None
            journal_info = JOURNAL_MAP.get(pay_account)
            wave_amount  = float(
                r["Debit Amount (Two Column Approach)"].replace(",", "").strip()
            )
            payments.append({
                "date":         r["Transaction Date"],
                "vendor":       r["Vendor"].strip(),
                "bill_number":  r["Bill Number"].strip(),
                "wave_amount":  wave_amount,
                "pay_account":  pay_account,
                "journal_info": journal_info,
            })
    return payments


def create_payment_entry(pay_date, journal_id, account_id, amount, ref):
    move_id = call("account.move", "create", [{
        "move_type":  "entry",
        "date":       pay_date,
        "journal_id": journal_id,
        "ref":        ref,
        "line_ids": [
            (0, 0, {"account_id": AP_ACCOUNT_ID, "debit": amount, "credit": 0.0, "name": ref}),
            (0, 0, {"account_id": account_id,    "debit": 0.0, "credit": amount, "name": ref}),
        ],
    }])
    call("account.move", "action_post", [[move_id]])
    return move_id


def reconcile_with_bill(payment_move_id, bill_id):
    pay_ap = call("account.move.line", "search_read",
                  [[["move_id", "=", payment_move_id],
                    ["account_id", "=", AP_ACCOUNT_ID],
                    ["reconciled", "=", False]]],
                  {"fields": ["id"]})
    bill_ap = call("account.move.line", "search_read",
                   [[["move_id", "=", bill_id],
                     ["account_id", "=", AP_ACCOUNT_ID],
                     ["reconciled", "=", False]]],
                   {"fields": ["id"]})
    if not pay_ap or not bill_ap:
        return False
    ids = [l["id"] for l in pay_ap] + [l["id"] for l in bill_ap]
    call_ignore_none("account.move.line", "reconcile", [ids])
    return True


def run_payments(dry_run=False):
    payments = load_wave_payments()
    bills = call("account.move", "search_read",
                 [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]],
                 {"fields": ["id", "ref", "partner_id", "amount_total", "amount_residual"]})
    bills_by_ref = defaultdict(list)
    for b in bills:
        if b["ref"]:
            bills_by_ref[b["ref"].strip()].append(b)

    print(f"\n{'─'*60}")
    print(f"Enregistrement des paiements Wave 2026")
    print(f"{'─'*60}")

    paid, skipped, errors = 0, 0, []
    bill_used = set()

    for p in sorted(payments, key=lambda x: x["date"]):
        ref          = p["bill_number"]
        vendor       = p["vendor"]
        pay_date     = p["date"]
        wave_amount  = p["wave_amount"]
        journal_info = p["journal_info"]

        candidates = [b for b in bills_by_ref.get(ref, [])
                      if b["id"] not in bill_used and b["amount_residual"] > 0.01]

        if not candidates:
            print(f"  ⚠  SKIP {pay_date} | {vendor:20s} | {ref} | {wave_amount:.2f} — bill non trouvé")
            skipped += 1
            continue
        if journal_info is None:
            print(f"  ⚠  SKIP {pay_date} | {vendor:20s} | {ref} | compte inconnu: {p['pay_account']}")
            skipped += 1
            continue

        journal_id, account_id = journal_info
        bill        = candidates[0]
        odoo_total  = bill["amount_total"]
        odoo_resid  = bill["amount_residual"]

        # Utiliser le montant Wave (= montant réel payé, doit maintenant correspondre au TTC Odoo)
        pay_amount = wave_amount
        ref_str    = f"Paiement {vendor} {ref}".strip()

        match_indicator = "✓" if abs(wave_amount - odoo_total) < 0.05 else "~"
        print(f"  {match_indicator} {pay_date} | {vendor:20s} | {ref:25s} | "
              f"Wave={wave_amount:.2f} Odoo={odoo_total:.2f} | {p['pay_account']}")

        if not dry_run:
            try:
                move_id    = create_payment_entry(pay_date, journal_id, account_id, pay_amount, ref_str)
                reconciled = reconcile_with_bill(move_id, bill["id"])
                if reconciled:
                    paid += 1
                    bill_used.add(bill["id"])
                    print(f"         ✓ move={move_id} payé")
                else:
                    errors.append(f"{vendor}/{ref}: réconciliation échouée")
                    print(f"         ✗ réconciliation échouée")
            except Exception as e:
                errors.append(f"{vendor}/{ref}: {str(e)[:100]}")
                print(f"         ✗ {str(e)[:80]}")
        else:
            paid += 1

    print(f"\nPayés: {paid} | Ignorés: {skipped} | Erreurs: {len(errors)}")
    for e in errors:
        print(f"  {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--step", choices=["1","2","3","all"], default="all",
                        help="1=supprimer paiements, 2=corriger bills, 3=payer, all=tout")
    args = parser.parse_args()

    dry = args.dry_run
    if dry:
        print("=== MODE DRY-RUN ===\n")

    if args.step in ("1", "all"):
        print("ÉTAPE 1 : Suppression des écritures de paiement")
        if not dry:
            delete_payment_moves()
        else:
            moves = call("account.move", "search",
                         [[["id", ">=", 78], ["move_type", "=", "entry"]]])
            print(f"  [DRY] {len(moves)} moves seraient supprimés")

    if args.step in ("2", "all"):
        print("\nÉTAPE 2 : Correction taxes + montants des bills")
        fix_and_retax_bills(dry_run=dry)
        create_missing_amazon_bill(dry_run=dry)

    if args.step in ("3", "all"):
        print("\nÉTAPE 3 : Enregistrement des paiements")
        run_payments(dry_run=dry)


if __name__ == "__main__":
    main()
