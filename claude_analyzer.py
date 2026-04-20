"""Analyse de documents via claude CLI — texte OCR ou vision (image directe)."""

import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import ALLOWED_TAGS, CLAUDE_BIN, MAX_CONTENT_LENGTH

MEDIA_ROOT = "/opt/paperless/media/documents/originals"

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
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "line_items": [{"description": "<nom produit/service>", "amount": <montant HT décimal>, "taxable": <true|false>}],
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
5. suggested_title: format "[Correspondant] [YYYY-MM]"
6. Si document multi-colonnes (ex: 2 reçus côte à côte): analyser les DEUX et prendre les données de la facture principale (avec détail des taxes)
7. Ne jamais mettre personnel/impots dans tags_to_add
8. Contexte fiscal Québec: TPS 5%, TVQ 9.975%. Congé fiscal fédéral déc 2024 – fév 2025: TPS=0.00 sur certains articles
9. gouvernement UNIQUEMENT pour documents émis par une autorité gouvernementale (Revenu Québec, ARC, SAAQ, municipalité, etc.). Un commerce privé (RONA, Canadian Tire, Amazon, etc.) n'est JAMAIS gouvernement même s'il perçoit des taxes.
10. Document médical (clinique, hôpital, pharmacie, optométriste, dentiste, etc.): mettre doc_type=medical ET ajouter "medical" dans tags_to_add. Si c'est aussi une facture/reçu, ajouter "facture" ou "recu" en plus.
11. Personnes: si le document concerne Olivia → ajouter "Olivia" dans tags_to_add. Si Leticia → ajouter "Leticia". Aucun tag pour Alexandre.
12. line_items: extraire CHAQUE ligne de produit/service avec son montant HT et si elle est taxable (TPS+TVQ). Si les lignes ne sont pas clairement identifiables (reçu global, montant unique), mettre line_items=[]. Les montants line_items sont AVANT taxes. La somme des amounts doit égaler total-tps-tvq.
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
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "line_items": [{{"description": "<nom produit/service>", "amount": <montant HT décimal>, "taxable": <true|false>}}],
  "tags_to_add": ["<tag1>"],
  "confidence": <0.0 à 1.0>,
  "notes": "<observations>"
}}

Valeurs doc_type: facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre
Tags autorisés: {allowed_tags}
Règles:
- Pour facture/recu: tps/tvq toujours un nombre (0.00 si non applicable). Ne pas mettre personnel/impots dans tags_to_add.
- gouvernement UNIQUEMENT pour documents d'autorités gouvernementales (Revenu Québec, ARC, SAAQ, etc.) — jamais pour un commerce privé.
- Document médical (clinique, pharmacie, dentiste, etc.): doc_type=medical ET "medical" dans tags_to_add. Si c'est aussi une facture/reçu, ajouter "facture" ou "recu" en plus.
- Si document concerne Olivia → ajouter "Olivia" dans tags_to_add. Si Leticia → ajouter "Leticia". Aucun tag pour Alexandre.
- line_items: extraire chaque ligne produit/service avec montant HT et taxable (true/false). Si non identifiable clairement → line_items=[]. Somme des amounts = total-tps-tvq.
"""


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
        raise RuntimeError(f"Claude CLI erreur (code {result.returncode}): {result.stderr[:300]}")

    for line in result.stdout.strip().split("\n"):
        try:
            obj = json.loads(line)
            if obj.get("type") == "result":
                return obj.get("result", "")
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

    # Détection incohérence fiscale (scan multi-colonnes raté)
    total = data.get("total")
    tvq = data.get("tvq")
    if (data.get("doc_type") == "recu" and total is not None
            and tvq == "0.00" and float(total) > 20.0):
        data["confidence"] = min(data.get("confidence", 0.5), 0.60)
        data["notes"] = (data.get("notes", "") +
                         " [ATTENTION: total présent mais TVQ=0.00 — vérifier]")

    # Valider line_items
    raw_items = data.get("line_items", [])
    clean_items = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                clean_items.append({
                    "description": str(item.get("description", "")).strip(),
                    "amount":      round(float(item.get("amount", 0)), 2),
                    "taxable":     bool(item.get("taxable", True)),
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
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
            })
        content_parts.append({"type": "text", "text": PROMPT_VISION})

    message = {
        "type": "user",
        "message": {"role": "user", "content": content_parts}
    }
    text = _call_claude(message)
    return _validate_and_clean(_extract_json(text))


def analyze_document(title: str, content: str) -> dict:
    """Fallback texte OCR — utilisé si la vision échoue."""
    if len(content) > MAX_CONTENT_LENGTH:
        half = MAX_CONTENT_LENGTH // 2
        content = content[:half] + "\n[...tronqué...]\n" + content[-half:]

    allowed_str = ", ".join(sorted(ALLOWED_TAGS))
    prompt = PROMPT_TEXT.format(title=title, content=content or "(aucun contenu OCR)", allowed_tags=allowed_str)

    message = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}
    }
    text = _call_claude(message)
    return _validate_and_clean(_extract_json(text))
