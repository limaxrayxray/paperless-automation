#!/usr/bin/env python3
"""
Test rapide: analyse d'un document Paperless via Ollama (vision).
Usage: python3 test_ollama.py <doc_id> [modele]
"""

import base64
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import urllib.request
import urllib.error

OLLAMA_URL = "http://192.168.99.62:11434"
MEDIA_ROOT = "/opt/paperless/media/documents/originals"
DEFAULT_MODEL = "gemma4-e4b-unsloth:latest"

PROMPT = """\
Tu es un système expert d'extraction de données de documents financiers (Québec, Canada).
Analyse ce document et retourne UNIQUEMENT un objet JSON valide, sans markdown, sans texte avant ou après.

{
  "doc_type": "<type>",
  "context": "<contexte>",
  "suggested_title": "<titre suggéré>",
  "correspondent": "<nom fournisseur/émetteur ou null>",
  "date": "<YYYY-MM-DD ou null>",
  "date_confidence": <0.0 à 1.0>,
  "invoice_number": "<numéro facture/transaction ou null>",
  "total": "<montant total avant pourboire, décimal ex: 66.81 ou null>",
  "tps": "<montant TPS — 0.00 si absent/exonéré, null si indéterminable>",
  "tvq": "<montant TVQ — 0.00 si absent/exonéré, null si indéterminable>",
  "tags_to_add": ["<tag1>"],
  "confidence": <0.0 à 1.0>,
  "notes": "<observations importantes>"
}

Valeurs possibles:
- doc_type: facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre
- context: rapidetech | personnel
- tags_to_add: parmi: facture, recu, releve, contrat, assurance, rapport, transport, internet, telephone, autre, medical

Règles:
1. Pour facture/recu: tps et tvq TOUJOURS un nombre (0.00 si non applicable), jamais null
2. total = montant AVANT taxes et AVANT pourboire
3. date_confidence=1.0 seulement si date explicite et non ambiguë
4. correspondent = nom de l'émetteur/fournisseur (pas le destinataire)
5. suggested_title: format "[Correspondant] [YYYY-MM]"
6. Contexte fiscal Québec: TPS 5%, TVQ 9.975% (appelée aussi QST/TVQ)
"""


def pdf_to_images(doc_id: int) -> list[str]:
    pdf_path = Path(MEDIA_ROOT) / f"{doc_id:07d}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF non trouvé: {pdf_path}")
    tmpdir = tempfile.mkdtemp()
    subprocess.run(
        ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", "2",
         str(pdf_path), f"{tmpdir}/page"],
        check=True, capture_output=True,
    )
    images = sorted(glob.glob(f"{tmpdir}/page-*.png"))
    if not images:
        raise RuntimeError("Aucune image générée")
    return images


def call_ollama_vision(model: str, images: list[str]) -> str:
    imgs_b64 = []
    for img_path in images[:2]:
        with open(img_path, "rb") as f:
            imgs_b64.append(base64.b64encode(f.read()).decode())

    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": imgs_b64,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read())
    return result.get("response", "")


def extract_json(text: str) -> dict:
    import re
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    raise ValueError(f"JSON introuvable dans:\n{text[:800]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_ollama.py <doc_id> [modele]")
        sys.exit(1)

    doc_id = int(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    print(f"Document ID: {doc_id}")
    print(f"Modèle: {model}")
    print("Conversion PDF → images...")

    images = pdf_to_images(doc_id)
    print(f"{len(images)} image(s) générée(s): {images}")

    print(f"Envoi à Ollama ({OLLAMA_URL})...")
    import time
    t0 = time.time()
    raw = call_ollama_vision(model, images)
    elapsed = time.time() - t0
    print(f"Réponse reçue en {elapsed:.1f}s")
    print(f"\n--- Réponse brute ---\n{raw}\n")

    try:
        result = extract_json(raw)
        print("--- JSON parsé ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(f"Erreur parsing JSON: {e}")


if __name__ == "__main__":
    main()
