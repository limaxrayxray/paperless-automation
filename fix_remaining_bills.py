#!/usr/bin/env python3
"""
Correction des 6 bills restants après fix_odoo_taxes.py
"""
import xmlrpc.client

ODOO_URL     = "https://odoo.rapidetech.ca"
ODOO_DB      = "odoo"
ODOO_UID     = 2
ODOO_API_KEY = "3beef8c66decdd41f36c6d8b10d1c9390d04c137"
AP           = 230
TAX          = [(6, 0, [30])]   # 14.975% GST+QST
NO_TAX       = [(6, 0, [])]

_models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

def call(model, method, args, kwargs=None):
    return _models.execute_kw(ODOO_DB, ODOO_UID, ODOO_API_KEY, model, method, args, kwargs or {})

def call_nil(model, method, args, kwargs=None):
    try:
        return call(model, method, args, kwargs)
    except Exception as e:
        if "cannot marshal None" in str(e):
            return None
        raise

def reset(bill_id):
    call_nil("account.move", "button_draft", [[bill_id]])

def post(bill_id):
    call("account.move", "action_post", [[bill_id]])

def del_move(move_id):
    call_nil("account.move", "button_draft", [[move_id]])
    call("account.move", "unlink", [[move_id]])

def get_lines(bill_id):
    return call("account.move.line", "search_read",
                [[["move_id", "=", bill_id], ["display_type", "=", "product"]]],
                {"fields": ["id", "name", "price_unit", "account_id", "quantity"]})

def pay_bill(pay_date, journal_id, account_id, amount, ref, bill_id):
    move_id = call("account.move", "create", [{
        "move_type":  "entry",
        "date":       pay_date,
        "journal_id": journal_id,
        "ref":        ref,
        "line_ids": [
            (0, 0, {"account_id": AP,         "debit": amount, "credit": 0.0, "name": ref}),
            (0, 0, {"account_id": account_id, "debit": 0.0, "credit": amount, "name": ref}),
        ],
    }])
    call("account.move", "action_post", [[move_id]])
    pay_ap  = call("account.move.line", "search_read",
                   [[["move_id","=",move_id],["account_id","=",AP],["reconciled","=",False]]],
                   {"fields": ["id"]})
    bill_ap = call("account.move.line", "search_read",
                   [[["move_id","=",bill_id],["account_id","=",AP],["reconciled","=",False]]],
                   {"fields": ["id"]})
    if pay_ap and bill_ap:
        ids = [l["id"] for l in pay_ap] + [l["id"] for l in bill_ap]
        call_nil("account.move.line", "reconcile", [ids])
    r = call("account.move", "read", [[bill_id]], {"fields": ["payment_state", "amount_residual"]})
    print(f"    → bill {bill_id}: {r[0]['payment_state']} résidu={r[0]['amount_residual']:.2f}")
    return move_id

def bill_status(bill_id):
    r = call("account.move", "read", [[bill_id]],
             {"fields": ["amount_untaxed", "amount_total", "payment_state", "amount_residual"]})
    return r[0]

# ─── FIX 1: RAGE AXE (bill 43) ────────────────────────────────────────────────
print("\n=== FIX 1: Rage Axe (bill 43) ===")
del_move(119)  # supprimer le paiement existant
reset(43)
lines = get_lines(43)
for l in lines:
    print(f"  ligne: {l['name']} | ${l['price_unit']:.2f}")

for l in lines:
    # Advertising & Promotion → taxable; Bank Service Charges → exempt
    if "Advertising" in str(l["name"]) or abs(l["price_unit"] - 102.0) < 0.01:
        call("account.move.line", "write", [[l["id"]], {"tax_ids": TAX}])
        print(f"  → taxable (14.975%): {l['name']}")
    else:
        call("account.move.line", "write", [[l["id"]], {"tax_ids": NO_TAX}])
        print(f"  → exempt: {l['name']}")

post(43)
s = bill_status(43)
print(f"  Bill 43: HT={s['amount_untaxed']:.2f} TTC={s['amount_total']:.2f} (attendu 132.57)")
pay_bill("2026-01-04", 9, 416, 132.57, "Paiement Rage Axe 004833", 43)

# ─── FIX 2: SHERWEB (bill 65) ─────────────────────────────────────────────────
print("\n=== FIX 2: Sherweb (bill 65) ===")
del_move(148)  # supprimer le paiement existant
reset(65)
# Ajouter ligne de crédit -$11.98 (COGS Credit) avec 14.975%
call("account.move", "write", [[65], {
    "invoice_line_ids": [(0, 0, {
        "name":       "Sherweb - crédit COGS",
        "account_id": 414,      # Cost of Goods Sold
        "price_unit": -11.98,
        "quantity":   1.0,
        "tax_ids":    TAX,
    })]
}])
post(65)
s = bill_status(65)
print(f"  Bill 65: HT={s['amount_untaxed']:.2f} TTC={s['amount_total']:.2f} (attendu 425.00)")
pay_bill("2026-01-26", 10, 417, 425.00, "Paiement sherweb CSWI4023835", 65)

# ─── FIX 3: TELUS (bill 71) ────────────────────────────────────────────────────
print("\n=== FIX 3: Telus (bill 71) ===")
# Supprimer les paiements existants liés à Telus (aucun dans cette passe, Telus était not_paid)
reset(71)
lines = get_lines(71)
print(f"  Lignes actuelles ({len(lines)}):")
for l in lines:
    print(f"    {l['name']} | ${l['price_unit']:.2f}")

# Supprimer toutes les lignes existantes et recréer proprement
delete_cmds = [(2, l["id"], 0) for l in lines]
new_lines = [
    (0, 0, {
        "name":       "Telus - services taxables (cellulaire)",
        "account_id": 411,      # cellphone
        "price_unit": 80.53,
        "quantity":   1.0,
        "tax_ids":    TAX,
    }),
    (0, 0, {
        "name":       "Telus - services exempts",
        "account_id": 411,
        "price_unit": 35.96,
        "quantity":   1.0,
        "tax_ids":    NO_TAX,
    }),
]
call("account.move", "write", [[71], {"invoice_line_ids": delete_cmds + new_lines}])
post(71)
s = bill_status(71)
print(f"  Bill 71: HT={s['amount_untaxed']:.2f} TTC={s['amount_total']:.2f} (attendu 128.56)")
# Paiement partiel $35.96 via Shareholder Loan (ref TD2568484)
pay_bill("2026-02-14", 9, 416, 35.96, "Paiement partiel Telus TD2568484", 71)

# ─── FIX 4: DIGITAL OCEAN (bills 46 + 74) ─────────────────────────────────────
print("\n=== FIX 4: Digital Ocean (bills 46 et 74) ===")
# Auto-débit carte — pas de paiement Wave, on utilise le Compte courant 910
s46 = bill_status(46)
print(f"  Bill 46 (Jan): TTC={s46['amount_total']:.2f}")
pay_bill("2026-01-31", 8, 415, s46["amount_total"], "Paiement digital ocean 535785962", 46)

s74 = bill_status(74)
print(f"  Bill 74 (Feb): TTC={s74['amount_total']:.2f}")
pay_bill("2026-02-28", 8, 415, s74["amount_total"], "Paiement digital ocean 538078825", 74)

# ─── FIX 5: AMAZON CA62BZX2H9TI (mauvaise assignation) ───────────────────────
print("\n=== FIX 5: Amazon CA62BZX2H9TI (correction assignation) ===")
# Bill 63: TTC=$21.83 → paiement move 142 était $52.85 (overpaid)
# Bill 116: TTC=$52.85 → paiement move 141 était $21.83 (underpaid)
del_move(141)
del_move(142)

# Bon paiement: $21.83 via Shareholder Loan → bill 63
pay_bill("2026-01-22", 9, 416, 21.83, "Paiement amazon CA62BZX2H9TI", 63)
# Bon paiement: $52.85 via Current Account (910) → bill 116
pay_bill("2026-01-22", 8, 415, 52.85, "Paiement amazon CA62BZX2H9TI (2)", 116)

# ─── RÉSUMÉ ────────────────────────────────────────────────────────────────────
print("\n=== RÉSUMÉ FINAL ===")
bills = call("account.move", "search_read",
             [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]],
             {"fields": ["id", "ref", "partner_id", "amount_total", "payment_state", "amount_residual"],
              "order": "id asc"})
paid    = [b for b in bills if b["payment_state"] == "paid"]
partial = [b for b in bills if b["payment_state"] == "partial"]
not_p   = [b for b in bills if b["payment_state"] == "not_paid"]
print(f"Total bills: {len(bills)} | Payés: {len(paid)} | Partiels: {len(partial)} | Non payés: {len(not_p)}")
for b in partial + not_p:
    print(f"  ID={b['id']} | {str(b['partner_id'][1])[:18]:18s} | {b['ref']:25s} | {b['amount_total']:.2f} | {b['payment_state']} résidu={b['amount_residual']:.2f}")
