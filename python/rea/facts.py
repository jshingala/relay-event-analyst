"""Typed view of the fact sheet produced by rea-core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str  # number | text | flag | missing
    value: Any
    display: str
    unit: str = ""
    label: str = ""
    method: str = ""
    reason: str = ""
    priority: int = 2
    decimals: int | None = None

    @property
    def is_missing(self) -> bool:
        return self.kind == "missing"

    def render(self) -> str:
        """`display unit` exactly as the narrator must quote it."""
        if self.is_missing:
            return "unknown"
        unit = f" {self.unit}" if self.unit and self.kind == "number" else ""
        return f"{self.display}{unit}"


class FactSheet:
    def __init__(self, data: dict):
        if data.get("schema") != "rea.facts/1":
            raise ValueError("not a rea.facts/1 document")
        self.raw = data
        self.facts: list[Fact] = []
        for f in data.get("facts", []):
            kind = f["kind"]
            display = f.get("display", "unknown" if kind == "missing" else str(f.get("value")))
            self.facts.append(
                Fact(
                    id=f["id"],
                    kind=kind,
                    value=f.get("value"),
                    display=display,
                    unit=f.get("unit", ""),
                    label=f.get("label", ""),
                    method=f.get("method", ""),
                    reason=f.get("reason", ""),
                    priority=int(f.get("priority", 2)),
                    decimals=f.get("decimals"),
                )
            )
        self.by_id = {f.id: f for f in self.facts}
        self.warnings: list[str] = list(data.get("warnings", []))
        self.meta: dict = dict(data.get("meta", {}))

    def __contains__(self, fact_id: str) -> bool:
        return fact_id in self.by_id

    def get(self, fact_id: str) -> Fact | None:
        return self.by_id.get(fact_id)

    def value(self, fact_id: str, default: Any = None) -> Any:
        f = self.by_id.get(fact_id)
        return default if f is None or f.is_missing else f.value

    def numbers(self) -> list[Fact]:
        return [f for f in self.facts if f.kind == "number"]

    def missing(self) -> list[Fact]:
        return [f for f in self.facts if f.is_missing]

    def select(self, max_priority: int = 2) -> list[Fact]:
        return [f for f in self.facts if f.priority <= max_priority]

    def lines(self, facts: Iterable[Fact]) -> list[str]:
        out = []
        for f in facts:
            if f.is_missing:
                out.append(f"- {f.id}: UNKNOWN ({f.reason})")
            else:
                out.append(f"- {f.id} = {f.render()}  ({f.label})")
        return out
