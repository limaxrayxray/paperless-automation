"""Client API Paperless-ngx."""

import json
import urllib.error
import urllib.request
from typing import Any

from config import PAPERLESS_TOKEN
from config import PAPERLESS_URL


def _request(
    method: str,
    endpoint: str,
    data: dict | None = None,
) -> dict:
    url = f"{PAPERLESS_URL}/{endpoint.lstrip('/')}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Token {PAPERLESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as r:
            if r.status == 204:
                return {}
            raw = r.read()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                ctype = r.headers.get("Content-Type", "?")
                snippet = raw[:200].decode(errors="replace")
                raise RuntimeError(
                    f"Réponse non-JSON de {method} {url} "
                    f"(Content-Type={ctype}). Vérifie PAPERLESS_API_URL "
                    f"(doit viser /api, pas le frontend). Début du corps: {snippet!r}",
                ) from e
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {body}") from e


def get_document(doc_id: int) -> dict:
    return _request("GET", f"/documents/{doc_id}/")


def get_document_content(doc_id: int) -> str:
    """Retourne le texte OCR extrait du document."""
    doc = get_document(doc_id)
    return doc.get("content", "") or ""


def get_all_correspondents() -> dict[str, int]:
    """Retourne {nom: id} de tous les correspondants."""
    result = _request("GET", "/correspondents/?page_size=500")
    return {c["name"]: c["id"] for c in result.get("results", [])}


def find_or_create_correspondent(name: str) -> int:
    """Trouve un correspondant existant (insensible à la casse) ou en crée un."""
    correspondents = get_all_correspondents()
    # Recherche insensible à la casse
    for existing_name, cid in correspondents.items():
        if existing_name.lower() == name.lower():
            return cid
    # Créer
    result = _request("POST", "/correspondents/", {"name": name})
    return result["id"]


def get_correspondent(corr_id: int) -> dict:
    """Retourne les données d'un correspondant par ID."""
    return _request("GET", f"/correspondents/{corr_id}/")


def get_documents_by_tag(tag_id: int, page_size: int = 50) -> list[dict]:
    """Retourne les documents ayant un tag donné."""
    result = _request("GET", f"/documents/?tags__id__in={tag_id}&page_size={page_size}")
    return result.get("results", [])


def get_all_documents_by_tag(tag_id: int) -> list[dict]:
    """Retourne TOUS les documents (non supprimés) ayant un tag, avec pagination."""
    docs = []
    endpoint = f"/documents/?tags__id__all={tag_id}&page_size=100"
    while endpoint:
        result = _request("GET", endpoint)
        docs.extend(result.get("results", []))
        nxt = result.get("next")
        endpoint = nxt[len(PAPERLESS_URL):] if nxt else None
    return docs


def get_recent_documents(count: int = 10) -> list[dict]:
    """Retourne les `count` documents les plus récents (par date de création desc)."""
    result = _request("GET", f"/documents/?ordering=-created&page_size={count}")
    return result.get("results", [])


def patch_document(doc_id: int, payload: dict) -> dict:
    """Met à jour partiellement un document."""
    return _request("PATCH", f"/documents/{doc_id}/", payload)


def delete_document(doc_id: int) -> None:
    """Supprime définitivement un document."""
    _request("DELETE", f"/documents/{doc_id}/")


def build_custom_fields_payload(
    existing_custom_fields: list[dict],
    updates: dict[str, Any],
    field_id_map: dict[str, int],
) -> list[dict]:
    """
    Construit la liste custom_fields pour l'API.
    Garde les valeurs existantes et applique les updates.
    """
    # Index des valeurs existantes par field_id
    current = {cf["field"]: cf["value"] for cf in existing_custom_fields}

    for field_name, value in updates.items():
        field_id = field_id_map.get(field_name)
        if field_id is not None and value is not None:
            current[field_id] = value

    return [{"field": fid, "value": val} for fid, val in current.items()]


def get_tag_ids_by_name(names: list[str]) -> dict[str, int]:
    """Récupère les IDs des tags par nom."""
    result = _request("GET", "/tags/?page_size=200")
    tag_map = {t["name"]: t["id"] for t in result.get("results", [])}
    return {n: tag_map[n] for n in names if n in tag_map}


def get_custom_fields() -> list[dict]:
    """Retourne tous les champs personnalisés définis dans Paperless."""
    result = _request("GET", "/custom_fields/?page_size=200")
    return result.get("results", [])


def create_custom_field(name: str, data_type: str = "string") -> dict:
    """Crée un champ personnalisé et retourne sa représentation (incl. 'id')."""
    return _request("POST", "/custom_fields/", {"name": name, "data_type": data_type})


def find_or_create_custom_field(name: str, data_type: str = "string") -> dict:
    """Trouve un champ personnalisé par nom (insensible à la casse) ou le crée.
    Idempotent : ne crée jamais de doublon."""
    for field in get_custom_fields():
        if field.get("name", "").lower() == name.lower():
            return field
    return create_custom_field(name, data_type)
