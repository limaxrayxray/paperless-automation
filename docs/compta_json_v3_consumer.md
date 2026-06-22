# compta_json v3 — note pour le consommateur (compta-rapidetech)

Document à passer à Claude dans le projet **compta-rapidetech**. Décrit UNIQUEMENT
ce qui change dans le `compta_json` reçu depuis paperless-automation. Le reste des
changements (garde-fou date, diagnostic d'erreurs) est interne à Paperless et
n'affecte pas le consommateur.

## Résumé

`compta_json.version` passe de **2 → 3**. Changement **additif et rétro-compatible** :
3 nouveaux champs par ligne d'item. Aucun champ existant n'est retiré ni renommé.
Un consommateur v2 lit un payload v3 sans broncher (champs inconnus ignorés).

## Nouveaux champs dans `items[]`

| Champ | Type | Sens |
|-------|------|------|
| `sku` | string \| null | Code produit du fournisseur **tel qu'affiché**, format libre (UPC, ASIN Amazon, n° d'article Canadian Tire, réf. DigitalOcean…). **Repli** : si aucun code lisible, `sku` = la `description` (ex. « Claude Pro »). `null` seulement si ni code ni description. |
| `qty` | int (>=1) | Quantité, entier. Défaut 1. |
| `unit_price_cents` | int | Prix unitaire **avant taxes**, en cents. Invariant : `amount_cents == qty * unit_price_cents` (au cent près). |

`amount_cents` reste le **total de ligne HT autoritatif** (inchangé). En cas
d'arrondi (qté ne divisant pas le montant), c'est `amount_cents` qui fait foi, pas
`qty * unit_price_cents`.

### À propos de `sku` (important pour le matching)

- Le `sku` est conçu pour **ré-identifier le même item d'un achat à l'autre** sans
  imposer de format. Il N'est PAS toujours un UPC.
- Garde-fou qualité côté producteur : un code en 12 chiffres purs est validé comme
  UPC-A (checksum) ; s'il est incohérent (lecture OCR douteuse) il retombe sur la
  description plutôt que d'émettre un faux code.
- Recommandation consommateur : matcher sur **(fournisseur, sku)** en priorité, avec
  repli sur **(fournisseur, description)**. Comme `sku` reprend la description quand
  il n'y a pas de code, un match (fournisseur, sku) couvre déjà les deux cas.

## Sources à prix taxes-incluses (SAQ) — transparent pour le consommateur

Certaines sources (SAQ) affichent des prix par ligne **taxes incluses**. Le producteur
les **ramène en HT** avant d'émettre : `items[].amount_cents` est **toujours HT**,
comme pour les autres factures. La consigne apparaît en ligne distincte avec
`taxable: false` et n'est pas dé-taxée. L'invariant de cohérence reste vrai :

```
somme(items[].amount_cents) + tps_cents + tvq_cents == total_cents
```

Rien de spécial à faire côté consommateur — c'est mentionné pour info.

## Exemple — reçu SAQ (qté, sku, consigne)

```json
{
  "version": 3,
  "doc_type": "recu",
  "fournisseur": "SAQ",
  "date": "2026-05-17",
  "currency": "CAD",
  "supplier_foreign": false,
  "total_cents": 3135,
  "tps_cents": 135,
  "tvq_cents": 269,
  "items": [
    { "description": "LAS NINAS SAUVIGNON BLANC", "amount_cents": 1561, "taxable": true,
      "sku": "15448131", "qty": 1, "unit_price_cents": 1561 },
    { "description": "POPSICLE FIRECRACKER", "amount_cents": 1130, "taxable": true,
      "sku": "15585814", "qty": 4, "unit_price_cents": 283 },
    { "description": "CONSIGNE 10", "amount_cents": 40, "taxable": false,
      "sku": "10001497", "qty": 4, "unit_price_cents": 10 }
  ],
  "needs_review": false,
  "review_reason": null,
  "source_method": "vision_primary"
}
```

## Exemple — facture sans code produit (sku = description)

```json
{
  "version": 3,
  "doc_type": "facture",
  "fournisseur": "Anthropic",
  "date": "2026-06-01",
  "currency": "USD",
  "supplier_foreign": true,
  "total_cents": 2000,
  "tps_cents": 0,
  "tvq_cents": 0,
  "items": [
    { "description": "Claude Pro", "amount_cents": 2000, "taxable": true,
      "sku": "Claude Pro", "qty": 1, "unit_price_cents": 2000 }
  ],
  "needs_review": false,
  "review_reason": null,
  "source_method": "ocr_text"
}
```

## Checklist de migration côté compta-rapidetech

- [ ] Lire `sku` / `qty` / `unit_price_cents` sur chaque item (avec valeurs par défaut
      si payload v2 reçu : `sku=null`, `qty=1`, `unit_price_cents=amount_cents`).
- [ ] Clé de matching `vendor_items` : (fournisseur, sku) prioritaire, repli description.
- [ ] Continuer à traiter `amount_cents` comme le total de ligne HT autoritatif.
- [ ] Lignes `taxable:false` (ex. consigne) : ne portent pas de taxe — les traiter
      comme telles dans la ventilation.

_Référence producteur : commit `d1f76c1`, `SPEC.md`, `docs/compta_json_v3`._
