"""Tipi di documento riconosciuti nell'estrazione dei campi."""

from __future__ import annotations

from enum import Enum


class DocumentKind(str, Enum):
    RECEIPT = "receipt"
    INVOICE = "invoice"
    BILL = "bill"
    BANK_STATEMENT = "bank_statement"
    PAYSLIP = "payslip"
    CONTRACT = "contract"
    TICKET = "ticket"
    ID_DOCUMENT = "id_document"
    LETTER = "letter"
    OTHER = "other"

    @classmethod
    def parse(cls, value: object) -> "DocumentKind":
        text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        try:
            return cls(text)
        except ValueError:
            return cls.OTHER

    @classmethod
    def choices(cls) -> str:
        return ", ".join(k.value for k in cls)
