from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import Contact, ContactState

PHONE_HEADERS = {
    "phone",
    "phone_number",
    "mobile",
    "mobile_number",
    "number",
    "whatsapp",
    "whatsapp_number",
    "contact",
    "contact_number",
}

NAME_HEADERS = {
    "name",
    "contact_name",
    "workshop",
    "business_name",
    "company",
}


class ContactFileError(ValueError):
    pass


def _clean_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_phone(
    raw_value: Any, default_country_code: str = "60"
) -> tuple[str, str]:
    """Return an E.164-compatible number without '+' and an error message.

    The function intentionally performs structural validation only. WhatsApp is
    the final authority on whether a number is registered on its service.
    """

    raw = _cell_text(raw_value)
    if not raw:
        return "", "Phone number is empty"

    if re.search(r"(?:ext\.?|extension|x)\s*\d+\s*$", raw, flags=re.IGNORECASE):
        return "", "Extensions are not supported"

    compact = re.sub(r"[\s().-]+", "", raw)
    if compact.startswith("+"):
        digits = compact[1:]
    elif compact.startswith("00"):
        digits = compact[2:]
    else:
        digits = compact
        country = re.sub(r"\D", "", default_country_code)
        if not country:
            return "", "Default country code is invalid"

        if digits.startswith("0"):
            digits = country + digits.lstrip("0")
        elif not digits.startswith(country):
            # Treat a number without an international prefix as a local number.
            digits = country + digits

    if not digits.isdigit():
        return "", "Contains unsupported characters"
    if digits.startswith("0"):
        return "", "International number cannot start with zero"
    if not 8 <= len(digits) <= 15:
        return "", "Must contain 8 to 15 digits after normalization"

    return digits, ""


def load_contacts(path: str | Path, default_country_code: str = "60") -> list[Contact]:
    source = Path(path).expanduser()
    if not source.exists():
        raise ContactFileError(f"File does not exist: {source}")
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ContactFileError("Choose an .xlsx or .xlsm workbook")

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ContactFileError(
            "Excel support is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        workbook = load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise ContactFileError(f"Could not read workbook: {exc}") from exc

    try:
        worksheet = workbook[workbook.sheetnames[0]]
        rows = worksheet.iter_rows(values_only=True)
        try:
            header_row = next(rows)
        except StopIteration as exc:
            raise ContactFileError("The first worksheet is empty") from exc

        headers = [_clean_header(value) for value in header_row]
        phone_index = next(
            (i for i, value in enumerate(headers) if value in PHONE_HEADERS), None
        )
        name_index = next(
            (i for i, value in enumerate(headers) if value in NAME_HEADERS), None
        )
        if phone_index is None:
            accepted = ", ".join(sorted(PHONE_HEADERS))
            raise ContactFileError(
                f"No phone column found. Accepted headers: {accepted}"
            )

        contacts: list[Contact] = []
        seen: set[str] = set()
        for row_number, row in enumerate(rows, start=2):
            raw_phone = row[phone_index] if phone_index < len(row) else None
            raw_name = (
                row[name_index]
                if name_index is not None and name_index < len(row)
                else None
            )
            original = _cell_text(raw_phone)
            name = _cell_text(raw_name)

            if not original and not name:
                continue

            phone, error = normalize_phone(raw_phone, default_country_code)
            if error:
                contact = Contact(
                    row_number=row_number,
                    original_phone=original,
                    name=name,
                    state=ContactState.INVALID,
                    detail=error,
                )
            elif phone in seen:
                contact = Contact(
                    row_number=row_number,
                    original_phone=original,
                    phone=phone,
                    name=name,
                    state=ContactState.DUPLICATE,
                    detail="Duplicate number",
                )
            else:
                seen.add(phone)
                contact = Contact(
                    row_number=row_number,
                    original_phone=original,
                    phone=phone,
                    name=name,
                )
            contacts.append(contact)
    finally:
        workbook.close()

    if not contacts:
        raise ContactFileError("No contact rows were found")
    return contacts
