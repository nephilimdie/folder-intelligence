"""Confronto tra le risposte giuste di un caso e ciò che l'app ha capito del file."""

from __future__ import annotations

from typing import Any

from ..documents import DocumentFields
from .bench_case import BenchCase


def score(case: BenchCase, doc: dict[str, Any]) -> dict[str, bool]:
    meta = doc.get("metadata") or {}
    fields = DocumentFields.from_dict(meta.get("fields") or {})
    text = " ".join(str(doc.get(k) or "") for k in ("text", "description", "title", "tags")).lower()
    checks: dict[str, bool] = {}
    if case.kind:
        checks["kind"] = fields.kind.value == case.kind
    if case.date:
        checks["date"] = case.date in (fields.date, meta.get("date"))
    if case.amount is not None:
        checks["amount"] = fields.amount is not None and abs(fields.amount - case.amount) < 0.01
    if case.issuer:
        found = (fields.issuer or "").lower()
        checks["issuer"] = bool(found) and (case.issuer.lower() in found or found in case.issuer.lower())
    for word in case.words:
        checks[f"«{word}»"] = word.lower() in text
    return checks
