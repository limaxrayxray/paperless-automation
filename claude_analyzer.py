"""Analyse de documents via claude CLI — texte OCR ou vision (image directe)."""

import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import ALLOWED_TAGS
from config import CLAUDE_BIN
from config import MAX_CONTENT_LENGTH

MEDIA_ROOT = "/opt/paperless/media/documents/originals"


def _date_context() -> str:
    """Contexte temporel injecté en tête de prompt — évite la confusion d'année.

    Sans la date du jour, Claude tend à rabattre les années vers son « présent »
    d'entraînement, et l'OCR thermique lit parfois 2026 comme 2025. On ancre donc
    le modèle sur aujourd'hui et on rappelle le format AA/MM/JJ des terminaux.
    """
    return (
        f"CONTEXTE TEMPOREL — Date du jour : {date.today().isoformat()}.\n"
        "Ne suppose JAMAIS l'année : lis-la sur le document. Les reçus et terminaux "
        "de paiement impriment souvent la date en AA/MM/JJ (ex. 26/06/05 = "
        "2026-06-05, PAS 2025). Un document est normalement scanné peu après son "
        "émission : une date nettement antérieure à aujourd'hui est suspecte "
        "(probable confusion d'année) — dans le doute, baisse date_confidence.\n\n"
    )

PROMPT_VISION = """\
Tu es un système expert d'extraction de données de documents financiers (Québec, Canada).
Analyse ce document et retourne UNIQUEMENT un objet JSON valide, sans markdown, sans texte avant ou après.

{
  "doc_type": "<type>",
  "context": "<contexte>",
  "suggested_title": "<titre suggéré>",
  "correspondent": "<nom fournisseur/émetteur ou null>",
  "correspondent_is_new": <true|false>,
  "date": "<YYYY-MM-DD ou null>",
  "date_confidence": <0.0 à 1.0>,
  "invoice_number": "<numéro facture/transaction ou null>",
  "total": "<montant total avant pourboire, décimal ex: 66.81 ou null>",
  "currency": "<devise du document: CAD | USD | EUR | etc.>",
  "supplier_foreign": <true si le fournisseur/émetteur est hors Canada (adresse US, Chine, Europe, etc.), false si canadien>,
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "line_amounts_include_tax": <true si les prix par ligne incluent déjà les taxes (ex: SAQ), false sinon>,
  "line_items": [{"description": "<nom produit/service>", "amount": <montant ligne décimal>, "taxable": <true|false>, "sku": "<code produit du fournisseur tel qu'affiché, ou null>", "qty": <quantité entière, défaut 1>, "unit_price": <prix unitaire décimal>}],
  "tags_to_add": ["<tag1>"],
  "confidence": <0.0 à 1.0>,
  "notes": "<observations importantes>"
}

Valeurs possibles:
- doc_type: facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre
- context: rapidetech | personnel (informatif seulement)
- tags_to_add: parmi: facture, recu, releve, contrat, assurance, rapport, certificat, gouvernement, transport, internet, telephone, autre, medical, Olivia, Leticia

Règles:
1. Pour facture/recu: tps et tvq TOUJOURS un nombre (0.00 si non applicable), jamais null
2. total = montant avant pourboire (si pourboire présent sur le document)
3. date_confidence=1.0 seulement si date explicite et non ambiguë
4. correspondent = nom de l'émetteur/fournisseur (pas le destinataire)
5. suggested_title: format strict "[Fournisseur] [YYYY-MM]" — UNIQUEMENT le nom du fournisseur et la date, rien d'autre. Exemples: "Bell Canada 2026-03", "RONA 2026-02", "Anthropic 2026-04". Pas de description, pas de numéro de facture, pas de tirets supplémentaires.
6. Si document multi-colonnes (ex: 2 reçus côte à côte): analyser les DEUX et prendre les données de la facture principale (avec détail des taxes)
7. Ne jamais mettre personnel/impots dans tags_to_add
8. Contexte fiscal Québec: TPS 5%, TVQ 9.975%. Congé fiscal fédéral déc 2024 – fév 2025: TPS=0.00 sur certains articles
8b. currency: devise du document (CAD par défaut si non précisée).
8c. supplier_foreign: true si le FOURNISSEUR est hors Canada (adresse US, Chine, Europe, etc.), peu importe la devise de paiement. Un fournisseur étranger (Cloudflare US, AliExpress Chine, DigitalOcean US...) ne perçoit normalement NI TPS NI TVQ même payé en CAD → tps=0.00 et tvq=0.00 est alors NORMAL, pas une erreur. Sauf s'il affiche un numéro TPS/TVQ canadien ET charge une taxe ventilée.
9. gouvernement UNIQUEMENT pour documents émis par une autorité gouvernementale (Revenu Québec, ARC, SAAQ, municipalité, etc.). Un commerce privé (RONA, Canadian Tire, Amazon, etc.) n'est JAMAIS gouvernement même s'il perçoit des taxes.
10. Document médical (clinique, hôpital, pharmacie, optométriste, dentiste, etc.): mettre doc_type=medical ET ajouter "medical" dans tags_to_add. Si c'est aussi une facture/reçu, ajouter "facture" ou "recu" en plus.
11. Personnes: si le document concerne Olivia → ajouter "Olivia" dans tags_to_add. Si Leticia → ajouter "Leticia". Aucun tag pour Alexandre.
12. line_items: extraire CHAQUE ligne de produit/service avec son montant et si elle est taxable (TPS+TVQ). Si les lignes ne sont pas clairement identifiables (reçu global, montant unique), mettre line_items=[]. Normalement les montants line_items sont AVANT taxes et leur somme égale total-tps-tvq.
13. Par ligne (en plus de amount/taxable):
    - sku: le code produit du fournisseur tel qu'AFFICHÉ sur le document, quel qu'en soit le format (UPC/code-barres, ASIN Amazon, n° d'article Canadian Tire, référence DigitalOcean, etc.), sinon null. NE JAMAIS deviner ni compléter un code partiel — mieux vaut null qu'un code faux. But: pouvoir ré-identifier le même item d'un achat à l'autre.
    - qty: la quantité, ENTIER >= 1 (défaut 1 si non indiquée). Pas de décimale.
    - unit_price: le prix unitaire affiché (décimal). Doit respecter amount = qty x unit_price. Si seul le montant de ligne est visible → qty=1 et unit_price=amount.
14. line_amounts_include_tax: mettre true UNIQUEMENT si les prix par ligne incluent DÉJÀ les taxes (TPS+TVQ), typique des reçus SAQ. Dans ce cas:
    - reporter les prix AFFICHÉS tels quels dans amount/unit_price (le système les ramènera en HT à partir des totaux TPS/TVQ — ne PAS les convertir toi-même);
    - la consigne/dépôt est une ligne DISTINCTE avec taxable=false (elle n'est pas taxée).
    Sinon false (cas par défaut: prix déjà HT, taxes ventilées à part).
"""

PROMPT_TEXT = """\
Tu es un système expert d'extraction de données de documents financiers (Québec, Canada).
Titre actuel: {title}
Contenu OCR:
{content}

Retourne UNIQUEMENT un objet JSON valide, sans markdown:
{{
  "doc_type": "<type>",
  "context": "<contexte>",
  "suggested_title": "<titre suggéré>",
  "correspondent": "<nom fournisseur/émetteur ou null>",
  "correspondent_is_new": <true|false>,
  "date": "<YYYY-MM-DD ou null>",
  "date_confidence": <0.0 à 1.0>,
  "invoice_number": "<numéro facture ou null>",
  "total": "<montant total décimal ou null>",
  "currency": "<devise du document: CAD | USD | EUR | etc.>",
  "supplier_foreign": <true si fournisseur hors Canada (US, Chine, Europe...), false si canadien>,
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "line_amounts_include_tax": <true si les prix par ligne incluent déjà les taxes (ex: SAQ), false sinon>,
  "line_items": [{{"description": "<nom produit/service>", "amount": <montant ligne décimal>, "taxable": <true|false>, "sku": "<code produit du fournisseur tel qu'affiché, ou null>", "qty": <quantité entière, défaut 1>, "unit_price": <prix unitaire décimal>}}],
  "tags_to_add": ["<tag1>"],
  "confidence": <0.0 à 1.0>,
  "notes": "<observations>"
}}

Valeurs doc_type: facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre
Tags autorisés: {allowed_tags}
Règles:
- Pour facture/recu: tps/tvq toujours un nombre (0.00 si non applicable). Ne pas mettre personnel/impots dans tags_to_add.
- suggested_title: format strict "[Fournisseur] [YYYY-MM]" — UNIQUEMENT le nom du fournisseur et la date, rien d'autre. Exemples: "Bell Canada 2026-03", "RONA 2026-02", "Anthropic 2026-04". Pas de description, pas de numéro de facture, pas de tirets supplémentaires.
- gouvernement UNIQUEMENT pour documents d'autorités gouvernementales (Revenu Québec, ARC, SAAQ, etc.) — jamais pour un commerce privé.
- Document médical (clinique, pharmacie, dentiste, etc.): doc_type=medical ET "medical" dans tags_to_add. Si c'est aussi une facture/reçu, ajouter "facture" ou "recu" en plus.
- Si document concerne Olivia → ajouter "Olivia" dans tags_to_add. Si Leticia → ajouter "Leticia". Aucun tag pour Alexandre.
- line_items: extraire chaque ligne produit/service avec montant et taxable (true/false). Si non identifiable clairement → line_items=[]. Normalement amounts en HT, somme = total-tps-tvq.
- Par ligne aussi: sku (code produit du fournisseur tel qu'affiché — UPC, ASIN Amazon, n° d'article Canadian Tire, etc. — sinon null, ne jamais deviner), qty (entier >= 1, défaut 1), unit_price (prix unitaire affiché, amount = qty x unit_price; si seul amount visible → qty=1, unit_price=amount).
- line_amounts_include_tax: true UNIQUEMENT si les prix par ligne incluent déjà les taxes (ex: SAQ) → reporter les prix affichés tels quels (le système les ramènera en HT), et mettre la consigne/dépôt en ligne distincte taxable=false. Sinon false.
- currency: devise du document (CAD par défaut).
- supplier_foreign: true si fournisseur hors Canada (adresse US/Chine/Europe...), peu importe la devise. Fournisseur étranger (Cloudflare, AliExpress, DigitalOcean...) → tps=0.00/tvq=0.00 NORMAL même payé en CAD.
"""


class RateLimitError(Exception):
    """Levée quand Claude retourne une erreur de limite de taux."""


# Motifs (sur stderr OU stdout) trahissant une limite/surcharge temporaire : il
# faut alors mettre en file de retry (RateLimitError), PAS taguer erreur-traitement.
_RATELIMIT_PATTERNS = (
    "429", "too many requests", "overloaded", "rate limit", "rate_limit",
    "usage limit", "usage_limit", "reached your", "resets at", "try again later",
    "service unavailable", "503", "529",
)


def _call_claude(input_json: dict) -> str:
    """Appelle claude CLI en mode stream-json et retourne le texte résultat."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    result = subprocess.run(
        [CLAUDE_BIN, "-p", "--input-format", "stream-json",
         "--output-format", "stream-json", "--verbose"],
        input=json.dumps(input_json),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    if result.returncode != 0:
        # Le CLI écrit souvent son motif d'erreur sur stdout (résultat stream-json),
        # pas sur stderr : on inspecte les deux pour ne pas perdre la cause.
        combined = (result.stderr + " " + result.stdout).lower()
        if any(kw in combined for kw in _RATELIMIT_PATTERNS):
            detail = (result.stderr.strip() or result.stdout.strip())[:300]
            raise RateLimitError(f"Claude limite/surcharge: {detail}")
        detail = result.stderr.strip() or result.stdout.strip()[:500] or "(aucune sortie)"
        raise RuntimeError(f"Claude CLI erreur (code {result.returncode}): {detail}")

    for line in result.stdout.strip().split("\n"):
        try:
            obj = json.loads(line)

            # rate_limit_event est toujours présent — seulement lever si status=rejected
            if obj.get("type") == "rate_limit_event":
                info = obj.get("rate_limit_info", {})
                if info.get("status") == "rejected":
                    raise RateLimitError(f"Claude rate limit rejeté: {info}")

            if obj.get("type") == "result":
                if obj.get("is_error"):
                    status = obj.get("api_error_status", "")
                    if status in (429, "429"):
                        raise RateLimitError(f"Claude API 429: {obj}")
                    raise RuntimeError(f"Claude erreur API: {obj}")
                return obj.get("result", "")

        except (RateLimitError, RuntimeError):
            raise
        except json.JSONDecodeError:
            continue

    raise RuntimeError("Aucun résultat trouvé dans la sortie Claude")


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Impossible d'extraire JSON:\n{text[:500]}")


def _validate_and_clean(data: dict) -> dict:
    valid_doc_types = {
        "facture", "recu", "releve", "contrat", "assurance",
        "rapport", "certificat", "gouvernement", "medical", "impots", "autre",
    }
    if data.get("doc_type") not in valid_doc_types:
        data["doc_type"] = "autre"
    if data.get("context") not in ("rapidetech", "personnel"):
        data["context"] = "rapidetech"

    for field in ("date_confidence", "confidence"):
        try:
            data[field] = max(0.0, min(1.0, float(data.get(field, 0.5))))
        except (TypeError, ValueError):
            data[field] = 0.5

    raw_tags = data.get("tags_to_add", [])
    data["tags_to_add"] = [t for t in raw_tags if t in ALLOWED_TAGS] if isinstance(raw_tags, list) else []

    for field in ("total", "tps", "tvq"):
        val = data.get(field)
        if val is not None:
            try:
                clean = re.sub(r"[^\d.,]", "", str(val)).replace(",", ".")
                data[field] = f"{float(clean):.2f}" if clean else None
            except (ValueError, AttributeError):
                data[field] = None

    # Pour facture/recu: TPS et TVQ toujours un nombre
    if data.get("doc_type") in ("facture", "recu"):
        if data.get("tps") is None:
            data["tps"] = "0.00"
        if data.get("tvq") is None:
            data["tvq"] = "0.00"

    # Normaliser la devise (CAD par défaut si absente/vide)
    raw_currency = data.get("currency")
    currency = str(raw_currency).strip().upper() if raw_currency else "CAD"
    data["currency"] = currency or "CAD"

    # Normaliser supplier_foreign (bool). Fournisseur étranger = devise non-CAD
    # OU jugé hors Canada par Claude. C'est le pays du fournisseur qui compte,
    # pas la devise de paiement (un fournisseur chinois payé en CAD reste étranger).
    data["supplier_foreign"] = bool(data.get("supplier_foreign"))
    is_foreign = data["supplier_foreign"] or data["currency"] not in ("CAD", "")

    # Source à prix taxes-incluses (SAQ…) : la dé-taxe des lignes est faite par
    # compta_payload à partir des totaux TPS/TVQ. Ici on normalise juste le drapeau.
    data["line_amounts_include_tax"] = bool(data.get("line_amounts_include_tax"))

    # Détection incohérence fiscale fiable — deux cas distincts:
    #  a) Asymétrie TPS>0 mais TVQ=0.00 : impossible au QC (taxes vont de pair)
    #     → colonne TVQ ratée OU anomalie fournisseur réelle. Toujours suspect.
    #  b) Fournisseur canadien avec TVQ=0.00 sur >20$ : reçu thermique mal scanné
    #     (intention d'origine de la règle).
    # On n'alerte PAS un fournisseur étranger sans taxes — c'est normal.
    total = data.get("total")
    tps = data.get("tps")
    tvq = data.get("tvq")
    if (data.get("doc_type") in ("recu", "facture") and total is not None
            and float(total) > 20.0 and tvq == "0.00"):
        asymetrie = tps not in (None, "0.00")
        canadien_sans_tvq = not is_foreign
        if asymetrie or canadien_sans_tvq:
            data["confidence"] = min(data.get("confidence", 0.5), 0.60)
            raison = ("TPS perçue mais TVQ=0.00" if asymetrie
                      else "fournisseur canadien sans TVQ")
            data["notes"] = (data.get("notes", "") +
                             f" [ATTENTION: {raison} sur {total}$ — vérifier]")

    # Valider line_items
    raw_items = data.get("line_items", [])
    clean_items = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                # sku/qty/unit_price : passés tels quels ; la validation (garde-fou
                # checksum UPC, invariant amount==qty*unit_price, dé-taxe) est faite
                # par compta_payload.build_compta_payload. Ici on normalise "null".
                raw_sku = item.get("sku")
                sku = (str(raw_sku).strip()
                       if raw_sku not in (None, "", "null", "None") else None)
                clean_items.append({
                    "description": str(item.get("description", "")).strip(),
                    "amount":      round(float(item.get("amount", 0)), 2),
                    "taxable":     bool(item.get("taxable", True)),
                    "sku":         sku,
                    "qty":         item.get("qty", 1),
                    "unit_price":  item.get("unit_price"),
                })
            except (ValueError, TypeError):
                continue
    data["line_items"] = clean_items

    inv = data.get("invoice_number")
    data["invoice_number"] = str(inv).strip() if inv and str(inv).lower() != "null" else None

    date_val = data.get("date")
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_val)):
        data["date"] = None
        data["date_confidence"] = 0.0

    corr = data.get("correspondent")
    data["correspondent"] = str(corr).strip() if corr and str(corr).lower() != "null" else None

    return data


OCR_MIN_CHARS = 150       # texte OCR jugé suffisant pour éviter la vision
OCR_MIN_CONFIDENCE = 0.70  # confiance minimale du texte pour ne pas fallback vision


def analyze_document_smart(doc_id: int, title: str, content: str) -> dict:
    """
    Stratégie OCR-first :
    1. Si OCR suffisant → texte d'abord
       - confiance >= OCR_MIN_CONFIDENCE → retourner le résultat
       - sinon → fallback vision
    2. Si OCR insuffisant (<150 chars) → vision directement
    """
    if content and len(content.strip()) >= OCR_MIN_CHARS:
        text_result = analyze_document(title, content)
        if text_result.get("confidence", 0) >= OCR_MIN_CONFIDENCE:
            text_result["_method"] = "ocr_text"
            return text_result
        # Confiance trop basse → tenter vision
        try:
            vision_result = analyze_document_vision(doc_id)
            vision_result["_method"] = "vision_fallback"
            return vision_result
        except Exception:
            text_result["_method"] = "ocr_text_only"
            return text_result
    else:
        # OCR insuffisant → vision directement
        vision_result = analyze_document_vision(doc_id)
        vision_result["_method"] = "vision_primary"
        return vision_result


def analyze_document_vision(doc_id: int) -> dict:
    """Analyse via vision — convertit le PDF en image et envoie à Claude."""
    pdf_path = Path(MEDIA_ROOT) / f"{doc_id:07d}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF non trouvé: {pdf_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", "2",
             str(pdf_path), f"{tmpdir}/page"],
            check=True, capture_output=True,
        )
        images = sorted(glob.glob(f"{tmpdir}/page-*.png"))
        if not images:
            raise RuntimeError("Aucune image générée depuis le PDF")

        content_parts = []
        for img_path in images[:2]:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            content_parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
            })
        content_parts.append({"type": "text", "text": _date_context() + PROMPT_VISION})

    message = {
        "type": "user",
        "message": {"role": "user", "content": content_parts},
    }
    text = _call_claude(message)
    return _validate_and_clean(_extract_json(text))


def analyze_document(title: str, content: str) -> dict:
    """Fallback texte OCR — utilisé si la vision échoue."""
    if len(content) > MAX_CONTENT_LENGTH:
        half = MAX_CONTENT_LENGTH // 2
        content = content[:half] + "\n[...tronqué...]\n" + content[-half:]

    allowed_str = ", ".join(sorted(ALLOWED_TAGS))
    prompt = _date_context() + PROMPT_TEXT.format(
        title=title, content=content or "(aucun contenu OCR)", allowed_tags=allowed_str,
    )

    message = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
    }
    text = _call_claude(message)
    return _validate_and_clean(_extract_json(text))
