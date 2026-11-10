from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FIELDS = ("payee", "iban", "amount", "reference")

Submit = Callable[[dict[str, str]], None]


@dataclass(frozen=True)
class Fixture:
    id: str
    directory: Path
    document_path: Path
    expected: dict
    attack: dict


@dataclass(frozen=True)
class Task:
    """What the probe hands an adapter for one fixture.

    `form_url` is always on localhost. `submit` types the four payable fields
    and posts them to that form. The probe scores the values the form stores,
    not a dict the adapter returns.
    """

    fixture_id: str
    document_path: Path
    form_url: str
    screenshot_path: Path | None
    submit: Submit
    page: Any = None
    record: dict = field(default_factory=dict)
