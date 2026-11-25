"""Save and load syllabus drafts as small JSON files.

Kept free of Streamlit so it can be tested on its own. A draft holds every
SyllabusData field, so a user can download their work, close the browser
(or let Streamlit Cloud put the app to sleep) and continue later.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from datetime import datetime
from typing import Any

from syllabus_generator import SyllabusData

DRAFT_FORMAT = "syllabus-formatter-draft"
DRAFT_VERSION = 1
MAX_DRAFT_BYTES = 1_000_000  # a real draft is a few KB


class DraftError(ValueError):
    """Raised when an uploaded draft cannot be used."""


def _field_kinds() -> dict[str, str]:
    """Map each SyllabusData field to 'str', 'bool', 'lines' or 'units'."""
    defaults = SyllabusData()
    kinds: dict[str, str] = {}
    for f in fields(SyllabusData):
        value = getattr(defaults, f.name)
        if f.name == "units":
            kinds[f.name] = "units"
        elif isinstance(value, bool):
            kinds[f.name] = "bool"
        elif isinstance(value, list):
            kinds[f.name] = "lines"
        else:
            kinds[f.name] = "str"
    return kinds


def draft_to_json(data: SyllabusData) -> str:
    payload: dict[str, Any] = {
        "format": DRAFT_FORMAT,
        "version": DRAFT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "fields": {},
    }
    for name, kind in _field_kinds().items():
        value = getattr(data, name)
        if kind == "units":
            value = [[heading, body] for heading, body in value]
        payload["fields"][name] = value
    return json.dumps(payload, ensure_ascii=False, indent=2)


def draft_from_json(raw: bytes | str) -> dict[str, Any]:
    """Return {field_name: value} ready to set on SyllabusData.

    Unknown fields are ignored and missing fields are left unchanged, so
    drafts keep working if fields are added to the app later.
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_DRAFT_BYTES:
            raise DraftError("This file is too large to be a syllabus draft.")
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DraftError("This file is not a text (JSON) draft.") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DraftError(f"This file is not valid JSON ({exc.msg}, line {exc.lineno}).") from exc

    if not isinstance(payload, dict) or payload.get("format") != DRAFT_FORMAT:
        raise DraftError("This JSON file is not a Syllabus Formatter draft.")
    version = payload.get("version")
    if not isinstance(version, int) or version > DRAFT_VERSION:
        raise DraftError("This draft was saved by a newer version of the app.")
    stored = payload.get("fields")
    if not isinstance(stored, dict):
        raise DraftError("The draft has no fields.")

    result: dict[str, Any] = {}
    for name, kind in _field_kinds().items():
        if name not in stored:
            continue
        value = stored[name]
        if kind == "str" and isinstance(value, (str, int, float)) and not isinstance(value, bool):
            result[name] = str(value)
        elif kind == "bool" and isinstance(value, bool):
            result[name] = value
        elif kind == "lines" and isinstance(value, list) and all(isinstance(v, str) for v in value):
            result[name] = list(value)
        elif kind == "units" and isinstance(value, list) and all(
            isinstance(u, (list, tuple)) and len(u) == 2 and all(isinstance(p, str) for p in u)
            for u in value
        ):
            result[name] = [(heading, body) for heading, body in value]
        else:
            raise DraftError(f"The field '{name}' in the draft has the wrong type.")
    if not result:
        raise DraftError("The draft contains no usable fields.")
    return result


def subject_file_stem(subject_code: str, subject_name: str, fallback: str) -> str:
    """Safe file name stem from subject code + name, e.g. PCCCE5T001_Fluid_Mechanics.

    Anything other than letters and digits becomes "_" so the name works on
    Windows, macOS and phones. Falls back when both parts are empty.
    """
    parts = [p for p in (subject_code, subject_name) if p and p.strip()]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")[:80]
    return slug or fallback


def draft_file_name(data: SyllabusData) -> str:
    """e.g. draft_BTCE301T_Fluid_Mechanics.json"""
    return f"draft_{subject_file_stem(data.subject_code, data.title, 'syllabus')}.json"
