"""Campi di un documento (DTO): servono a ricerche precise come "bollette sopra 100 €"."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .amount import parse_amount
from .document_kind import DocumentKind

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# risposte generiche (il modello ripete l'esempio dello schema): non sono un emittente
_GENERIC_ISSUERS = {"shop", "store", "bank", "company", "negozio", "banca", "azienda", "supermarket",
                    "supermercato", "issuer", "emittente", "unknown", "sconosciuto"}


@dataclass(frozen=True)
class DocumentFields:
    kind: DocumentKind = DocumentKind.OTHER
    date: str | None = None          # YYYY-MM-DD
    amount: float | None = None
    currency: str | None = None      # ISO: EUR, USD…
    issuer: str | None = None        # chi l'ha emesso (banca, negozio, azienda)
    number: str | None = None        # numero di fattura, ricevuta, pratica…
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.kind is DocumentKind.OTHER and not any((self.date, self.amount, self.issuer, self.number))

    def search_terms(self) -> list[str]:
        """Parole da rendere cercabili (tag): emittente e numero non sempre sono nel testo letto."""
        return [t.lower() for t in (self.issuer, self.number) if t]

    def merged_with(self, other: "DocumentFields") -> "DocumentFields":
        """I campi mancanti si prendono dall'altra lettura (es. immagine + riassunto del testo)."""
        mine, theirs = self.to_dict(), other.to_dict()
        merged = {k: mine[k] if mine[k] not in (None, "", DocumentKind.OTHER.value, {}) else theirs[k] for k in mine}
        return DocumentFields.from_dict(merged)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DocumentFields":
        """Da JSON del modello o dell'indice, tollerando valori sporchi ("null", "1.234,00 €"…)."""
        data = data or {}

        def clean(key: str) -> str | None:
            value = str(data.get(key) or "").strip()
            return value[:120] if value and value.lower() not in ("null", "none", "n/a", "-") else None

        amount, currency = parse_amount(data.get("amount"))
        date = clean("date")
        return cls(
            kind=DocumentKind.parse(data.get("kind") or data.get("doc_kind")),
            date=date if date and _DATE.match(date) else None,
            amount=amount,
            currency=(clean("currency") or currency or "").upper()[:3] or None,
            issuer=None if (clean("issuer") or "").lower() in _GENERIC_ISSUERS else clean("issuer"),
            number=clean("number"),
            extra={str(k): str(v)[:120] for k, v in (data.get("extra") or {}).items()} if isinstance(
                data.get("extra"), dict) else {},
        )

    @staticmethod
    def schema_hint() -> str:
        """Parte di schema JSON da chiedere ai modelli (testo o immagine)."""
        return ('"fields": {"kind": "<one of: ' + DocumentKind.choices() + '>", "date": "<YYYY-MM-DD or null>", '
                '"amount": <total amount as a number or null>, "currency": "<ISO code or null>", '
                '"issuer": "<the proper name of the company, bank or shop that issued it, exactly as '
                'written on the document (e.g. the name in the header), or null>", '
                '"number": "<invoice/receipt/document number or null>"}')
