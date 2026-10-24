"""Publication-aware historical facts. No current-value backfill is permitted."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable
from pathlib import Path
import hashlib
import json


def aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class HistoricalFact:
    symbol: str
    field: str
    value: Decimal
    effective_at: datetime
    published_at: datetime
    source: str
    unit: str

    def __post_init__(self) -> None:
        aware(self.effective_at, "effective_at")
        aware(self.published_at, "published_at")
        if not all(isinstance(v, str) and v.strip() for v in (self.symbol, self.field, self.source, self.unit)):
            raise ValueError("Fact identifiers, source and units must be nonempty")
        if not isinstance(self.value, Decimal) or not self.value.is_finite() or self.value < 0:
            raise ValueError("Historical values must be finite nonnegative Decimals")


@dataclass(frozen=True, slots=True)
class HistoricalCoverage:
    """Audited event-history completeness, not a tradable future value.

    The evidence may be assembled later, but every numeric fact still has its
    original publication gate. A current company snapshot alone cannot justify
    historical coverage.
    """
    symbol: str
    field: str
    start_at: datetime
    end_at: datetime
    source: str

    def __post_init__(self):
        aware(self.start_at,"coverage start")
        aware(self.end_at,"coverage end")
        if self.end_at < self.start_at or not self.symbol or not self.field or not self.source:
            raise ValueError("Explicit audited historical coverage interval required")


class PointInTimeTable:
    def __init__(self, facts: Iterable[HistoricalFact], coverage: Iterable[HistoricalCoverage] = ()):
        unique: dict[tuple, HistoricalFact] = {}
        for fact in facts:
            if not isinstance(fact, HistoricalFact):
                raise ValueError("Expected HistoricalFact")
            key = (fact.symbol, fact.field, fact.effective_at, fact.published_at)
            if key in unique and (unique[key].value, unique[key].unit) != (fact.value, fact.unit):
                raise ValueError(f"Conflicting historical facts: {key}")
            unique.setdefault(key, fact)
        self._facts = tuple(sorted(unique.values(), key=lambda f: (f.symbol, f.field, f.effective_at, f.published_at)))
        self._coverage=tuple(coverage)
        if any(not isinstance(c,HistoricalCoverage) for c in self._coverage):
            raise ValueError("Expected HistoricalCoverage")

    def covers(self,symbol: str,field: str,decision_at: datetime) -> bool:
        aware(decision_at,"decision_at")
        return any(c.symbol==symbol and c.field==field and c.start_at<=decision_at<=c.end_at for c in self._coverage)

    def at_decision(self, decision_at: datetime) -> PointInTimeTable:
        """Give a strategy a numeric-fact snapshot with no future values inside.

        Coverage audits remain offline completeness metadata. Every retained
        numeric fact has already passed both its effective and publication gate.
        """
        aware(decision_at,"decision_at")
        return PointInTimeTable((f for f in self._facts if f.effective_at<=decision_at
                                and f.published_at<=decision_at),self._coverage)

    def as_of(self, symbol: str, field: str, decision_at: datetime, *, unit: str) -> HistoricalFact | None:
        aware(decision_at, "decision_at")
        candidates = [f for f in self._facts if f.symbol == symbol and f.field == field
                      and f.effective_at <= decision_at and f.published_at <= decision_at]
        if not candidates:
            return None
        result = max(candidates, key=lambda f: (f.effective_at, f.published_at))
        if result.unit != unit:
            raise ValueError(f"Unit mismatch: expected {unit}, found {result.unit}")
        return result

    def latest_published(self, symbol: str, field: str, decision_at: datetime, *, unit: str) -> HistoricalFact | None:
        """Latest known as-of snapshot, then its latest public version.

        A later correction wins only after publication. An old snapshot remains
        usable while no new public snapshot exists; unreported real changes are
        not inferred. Same-publication-time disagreements require resolution.
        This method alone does not certify a complete publication directory.
        """
        aware(decision_at,"decision_at")
        eligible=[f for f in self._facts if f.symbol==symbol and f.field==field
                  and f.effective_at<=decision_at and f.published_at<=decision_at]
        if not eligible:return None
        latest_asof=max(f.effective_at for f in eligible)
        dated=[f for f in eligible if f.effective_at==latest_asof]
        latest=max(f.published_at for f in dated)
        newest=[f for f in dated if f.published_at==latest]
        if len({(f.value,f.unit) for f in newest})>1:
            raise ValueError("Conflicting same-publication-time share snapshots; explicit source resolution required")
        result=newest[0]
        if result.unit!=unit:raise ValueError(f"Unit mismatch: expected {unit}, found {result.unit}")
        return result

    def market_cap(self, symbol: str, decision_at: datetime, unadjusted_close: Decimal, *,
                   share_fact_semantics: str = "actual_effective_with_audited_coverage") -> tuple[Decimal, HistoricalFact] | None:
        """A configured historical share model times UNADJUSTED close.

        Publication coverage certifies acquired public notices, not the absence
        of unreported real-world capital changes. The two models never fall
        back to one another and a current-only snapshot is not a historical fact.
        """
        if not isinstance(unadjusted_close, Decimal) or not unadjusted_close.is_finite() or unadjusted_close <= 0:
            raise ValueError("Market-cap price must be a finite positive Decimal")
        if share_fact_semantics=="actual_effective_with_audited_coverage":
            if not self.covers(symbol,"total_shares",decision_at):return None
            shares=self.as_of(symbol,"total_shares",decision_at,unit="shares")
        elif share_fact_semantics=="latest_published_known":
            if not self.covers(symbol,"total_shares_publications",decision_at):return None
            shares=self.latest_published(symbol,"total_shares",decision_at,unit="shares")
        else:raise ValueError("Explicit supported historical share fact semantics required")
        return None if shares is None else (shares.value * unadjusted_close, shares)


def load_verified_fact_bundle(path: Path) -> PointInTimeTable:
    """Load a source-hashed publication ledger, not current financial snapshots.

    Date-only announcements become available conservatively at 23:59:59 in the
    stated timezone. Coverage audits are offline completeness evidence; they do
    not relax any fact's original publication gate.
    """
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError(f"Duplicate JSON field: {key}")
            result[key]=value
        return result
    root=path.resolve().parent
    raw=json.loads(path.read_text(encoding="utf-8-sig"),object_pairs_hook=unique)
    if raw.get("schema_version")!=1 or raw.get("status")!="verified_point_in_time":
        raise ValueError("Historical share bundle is pending or unverified")
    sources={}
    for item in raw["sources"]:
        identity=item["id"]
        source=(root/item["path"]).resolve()
        if not source.is_relative_to(root) or identity in sources:
            raise ValueError("Source escapes evidence bundle or duplicates an identifier")
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("Historical evidence source hash mismatch")
        published=datetime.fromisoformat(item["published_at"])
        aware(published,"source published_at")
        precision=item["timestamp_precision"]
        if precision not in ("date","timestamp"):
            raise ValueError("Explicit publication timestamp precision required")
        if precision=="date" and (published.hour,published.minute,published.second)!=(23,59,59):
            raise ValueError("Date-only source must use conservative end-of-day availability")
        sources[identity]=(item,published)
    facts=[]
    for item in raw["facts"]:
        identity=item["source_id"]
        source,published=sources[identity]
        available=datetime.fromisoformat(item["published_at"])
        if available!=published:
            raise ValueError("Fact publication timestamp must match its bound evidence")
        if not isinstance(item["value"], str) or not item["value"].strip():
            raise ValueError("Historical values must use explicit decimal strings")
        facts.append(HistoricalFact(item["symbol"],item["field"],Decimal(item["value"]),
                     datetime.fromisoformat(item["effective_at"]),available,
                     f"{identity} sha256:{source['sha256']}",item["unit"]))
    coverage=[]
    for item in raw["coverage"]:
        if item["field"]=="total_shares_publications":
            if item.get("publication_history_complete") is not True:
                raise ValueError("Public share-notice history coverage has not been verified")
        elif item.get("event_history_complete") is not True:
            raise ValueError("Historical total-shares event coverage has not been verified")
        evidence_id=item["source_id"]
        if evidence_id not in sources:
            raise ValueError("Coverage needs its own bound audit evidence")
        coverage.append(HistoricalCoverage(item["symbol"],item["field"],datetime.fromisoformat(item["start_at"]),datetime.fromisoformat(item["end_at"]),evidence_id))
    return PointInTimeTable(facts,coverage)
