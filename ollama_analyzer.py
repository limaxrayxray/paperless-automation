"""Analyse de documents via Ollama vision (Qwen3-VL)."""

import base64
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://192.168.99.62:11434/api/generate"
OLLAMA_MODEL = "voytas26/openclaw-qwen3vl-8b-opt:latest"
MEDIA_ROOT = "/opt/paperless/media/documents/originals"

PROMPT = """\
Tu es un système expert d'extraction de données de documents financiers (Québec, Canada).
Analyse ce document et retourne UNIQUEMENT un objet JSON valide, sans markdown, sans texte avant ou après.

{
  "doc_type": "<type>",
  "context": "<contexte>",
  "suggested_title": "<titre suggéré>",
  "correspondent": "<nom fournisseur/émetteur>",
  "correspondent_is_new": <true|false>,
  "date": "<YYYY-MM-DD ou null>",
  "date_confidence": <0.0 à 1.0>,
  "invoice_number": "<numéro facture/transaction ou null>",
  "total": "<montant total avant pourboire, décimal ex: 66.81>",
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "tags_to_add": ["<tag1>"],
  "confidence": <0.0 à 1.0>,
  "notes": "<observations importantes>"
}

Valeurs possibles:
- doc_type: facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre
- context: rapidetech | personnel (informatif seulement)
- tags_to_add: parmi: facture, recu, releve, contrat, assurance, rapport, certificat, gouvernement, transport, internet, telephone, autre

Règles:
1. Pour facture/recu: tps et tvq TOUJOURS un nombre (0.00 si non applicable), jamais null
2. total = montant avant pourboire (si pourboire présent)
3. date_confidence=1.0 seulement si date explicite et non ambiguë
4. correspondent = nom de l'émetteur/fournisseur (pas le destinataire)
5. suggested_title: format "[Correspondant] [YYYY-MM]"
6. Si document multi-colonnes (ex: 2 reçus côte à côte): analyser les DEUX et prendre les données de la facture principale (avec détail des taxes)
7. Ne jamais mettre personnel/medical/impots dans tags_to_add
"""


def pdf_to_images_b64(doc_id: int, max_pages: int = 2) -> list[str]:
    """Convertit les premières pages d'un PDF en liste de base64 PNG."""
    pdf_path = Path(MEDIA_ROOT) / f"{doc_id:07d}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF non trouvé: {pdf_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_prefix = f"{tmpdir}/page"
        subprocess.run(
            ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", str(max_pages),
             str(pdf_path), out_prefix],
            check=True,
            capture_output=True,
        )
        images = sorted(Path(tmpdir).glob("page-*.png"))
        result = []
        for img_path in images[:max_pages]:
            with open(img_path, "rb") as f:
                result.append(base64.b64encode(f.read()).decode())
        return result


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Retirer les balises <thinking>...</thinking> et \boxed{} si présentes
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"\\boxed\{(.*?)\}", r"\1", text, flags=re.DOTALL).strip()
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

    allowed_tags = {
        "facture", "recu", "releve", "rapport", "certificat", "gouvernement",
        "contrat", "assurance", "autre", "transport", "internet", "telephone",
    }
    raw_tags = data.get("tags_to_add", [])
    data["tags_to_add"] = [t for t in raw_tags if t in allowed_tags] if isinstance(raw_tags, list) else []

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

    inv = data.get("invoice_number")
    data["invoice_number"] = str(inv).strip() if inv and str(inv).lower() != "null" else None

    date_val = data.get("date")
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_val)):
        data["date"] = None
        data["date_confidence"] = 0.0

    corr = data.get("correspondent")
    data["correspondent"] = str(corr).strip() if corr and str(corr).lower() != "null" else None

    return data


def analyze_document(doc_id: int, title: str) -> dict:
    """
    Analyse un document via Ollama vision.
    Convertit le PDF en image(s) et envoie au modèle Qwen3-VL.
    """
    images_b64 = pdf_to_images_b64(doc_id)
    if not images_b64:
        raise RuntimeError("Aucune image générée depuis le PDF")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": PROMPT,
        "images": images_b64,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Extraction déterministe
        }
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        result = json.load(r)

    response_text = result.get("response", "")
    analysis = _extract_json(response_text)
    return _validate_and_clean(analysis)
