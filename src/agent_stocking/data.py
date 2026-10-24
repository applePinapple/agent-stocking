"""Import and validate single-security daily OHLCV data.

CSV volume units are explicit. Native TongdaXin ``.day`` files store prices in
cents and volume in shares. Neither format supplies reliable trading status;
missing buy/sell permissions remain ``None`` for the execution layer to handle.
"""

from __future__ import annotations

import csv
import re
import struct
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .models import Bar


_COLUMNS = (
    "date", "open", "high", "low", "close", "volume", "amount",
    "buy_allowed", "sell_allowed",
)
_REQUIRED = frozenset(_COLUMNS[:6])
_ALIASES = {name: name for name in _COLUMNS}
_ALIASES.update({
    "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
    "收盘": "close", "成交量": "volume", "成交额": "amount",
})
_DAY_RECORD = struct.Struct("<IIIIIfII")


def _fail(location: str, message: str) -> ValueError:
    return ValueError(f"{location}: {message}")


def _validate_bar(bar: Bar, previous: date | None, location: str) -> None:
    if not isinstance(bar, Bar):
        raise _fail(location, "expected a Bar")
    if type(bar.date) is not date:
        raise _fail(location, "date must be a datetime.date")
    if previous is not None and bar.date <= previous:
        problem = "duplicate date" if bar.date == previous else "dates must be increasing"
        raise _fail(location, f"{problem}: {bar.date.isoformat()}")
    for name in ("open", "high", "low", "close"):
        value = getattr(bar, name)
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise _fail(location, f"{name} must be a positive finite Decimal")
    if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
        raise _fail(location, "OHLC values must satisfy low <= open/close <= high")
    if type(bar.volume) is not int or bar.volume < 0:
        raise _fail(location, "volume must be a nonnegative integer in shares")
    if bar.amount is not None and (
        not isinstance(bar.amount, Decimal)
        or not bar.amount.is_finite()
        or bar.amount < 0
    ):
        raise _fail(location, "amount must be a nonnegative finite Decimal or None")
    for name in ("buy_allowed", "sell_allowed"):
        value = getattr(bar, name)
        if value is not None and type(value) is not bool:
            raise _fail(location, f"{name} must be true, false, or None")


def validate_bars(bars: Iterable[Bar]) -> None:
    """Reject empty, invalid, duplicate-date, or unordered daily data."""
    previous = None
    count = 0
    for count, bar in enumerate(bars, start=1):
        _validate_bar(bar, previous, f"bar {count}")
        previous = bar.date
    if count == 0:
        raise ValueError("bars must not be empty")


def _parse_date(value: str, location: str) -> date:
    if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", value):
        if value[4] != value[7]:
            raise _fail(location, f"invalid date {value!r}")
        digits = value.replace("-", "").replace("/", "")
    elif re.fullmatch(r"\d{8}", value):
        digits = value
    else:
        raise _fail(location, f"invalid date {value!r}; use YYYY-MM-DD, YYYY/MM/DD, or YYYYMMDD")
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError as exc:
        raise _fail(location, f"invalid date {value!r}") from exc


def _decimal(value: str, field: str, location: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise _fail(location, f"invalid {field}: {value!r}") from exc
    if not number.is_finite():
        raise _fail(location, f"{field} must be finite")
    return number


def _boolean(value: str, field: str, location: str) -> bool | None:
    if not value:
        return None
    normalized = value.casefold()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise _fail(location, f"invalid {field}: {value!r}; use true/false/1/0 or blank")


def _split_line(line: str, delimiter: str | None, location: str) -> list[str]:
    if delimiter is None:
        return line.split()
    try:
        return [cell.strip() for cell in next(csv.reader([line], delimiter=delimiter, strict=True))]
    except csv.Error as exc:
        raise _fail(location, f"invalid delimited row: {exc}") from exc


def _header(line: str, location: str) -> tuple[list[str | None], str | None]:
    delimiter = "," if "," in line else "\t" if "\t" in line else None
    names = _split_line(line, delimiter, location)
    columns = [_ALIASES.get(name.casefold()) for name in names]
    seen = set()
    for column in columns:
        if column is not None:
            if column in seen:
                raise _fail(location, f"duplicate column: {column}")
            seen.add(column)
    missing = _REQUIRED - seen
    if missing:
        raise _fail(location, f"missing required columns: {', '.join(sorted(missing))}")
    return columns, delimiter


def load_csv(path: str | Path, *, volume_unit: str = "shares") -> list[Bar]:
    """Read English/Chinese comma, tab, or whitespace daily data.

    UTF-8 (with or without BOM) and GB18030 exports are supported. A single
    stock-title line before the header and a trailing 数据来源 line are allowed.
    Extra indicator columns are ignored, but every data row must be well formed.
    ``volume_unit='lots'`` converts each input lot to 100 shares.
    """
    if volume_unit not in ("shares", "lots"):
        raise ValueError("volume_unit must be 'shares' or 'lots'")
    path = Path(path)
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise _fail(str(path), "expected UTF-8 or GB18030 text") from exc
    lines = [(number, line) for number, line in enumerate(content.splitlines(), start=1) if line.strip()]
    if not lines:
        raise _fail(str(path), "no header or data")

    # A recognized field makes the first line a header, even if it is incomplete.
    # This prevents a malformed header from being silently treated as a title.
    header_index = 0
    number, line = lines[0]
    guessed_delimiter = "," if "," in line else "\t" if "\t" in line else None
    first_fields = _split_line(line, guessed_delimiter, f"{path}: line {number}")
    if not any(field.casefold() in _ALIASES for field in first_fields):
        header_index = 1
        if len(lines) < 2:
            raise _fail(f"{path}: line {number}", "missing header after stock title")
    number, line = lines[header_index]
    columns, delimiter = _header(line, f"{path}: line {number}")

    bars = []
    previous = None
    metadata_seen = False
    for number, line in lines[header_index + 1:]:
        location = f"{path}: line {number}"
        if metadata_seen:
            raise _fail(location, "unexpected content after 数据来源 metadata")
        if line.lstrip().startswith("数据来源"):
            metadata_seen = True
            continue
        values = _split_line(line, delimiter, location)
        if len(values) != len(columns):
            raise _fail(location, f"expected {len(columns)} columns, found {len(values)}")
        row = {column: value for column, value in zip(columns, values) if column is not None}
        volume = _decimal(row["volume"], "volume", location)
        if volume < 0 or volume != volume.to_integral_value():
            raise _fail(location, "volume must be a nonnegative integer")
        amount_text = row.get("amount", "")
        bar = Bar(
            date=_parse_date(row["date"], location),
            open=_decimal(row["open"], "open", location),
            high=_decimal(row["high"], "high", location),
            low=_decimal(row["low"], "low", location),
            close=_decimal(row["close"], "close", location),
            volume=int(volume) * (100 if volume_unit == "lots" else 1),
            amount=_decimal(amount_text, "amount", location) if amount_text else None,
            buy_allowed=_boolean(row.get("buy_allowed", ""), "buy_allowed", location),
            sell_allowed=_boolean(row.get("sell_allowed", ""), "sell_allowed", location),
        )
        _validate_bar(bar, previous, location)
        bars.append(bar)
        previous = bar.date
    if not bars:
        raise _fail(str(path), "no data rows")
    return bars


def load_tdx_day(path: str | Path) -> list[Bar]:
    """Read 32-byte little-endian TongdaXin daily records.

    The file contains no adjustment factors, listing status, suspension status,
    limit prices, or explicit buy/sell permissions. Prices are returned as stored.
    """
    path = Path(path)
    content = path.read_bytes()
    if not content:
        raise _fail(str(path), "empty .day file")
    if len(content) % _DAY_RECORD.size:
        raise _fail(str(path), f"truncated .day file: {len(content)} bytes is not a multiple of 32")
    bars = []
    previous = None
    for number, record in enumerate(_DAY_RECORD.iter_unpack(content), start=1):
        location = f"{path}: record {number}"
        encoded_date, opening, high, low, close, amount, volume, _reserved = record
        bar = Bar(
            date=_parse_date(str(encoded_date), location),
            open=Decimal(opening) / 100,
            high=Decimal(high) / 100,
            low=Decimal(low) / 100,
            close=Decimal(close) / 100,
            volume=volume,
            amount=Decimal(str(amount)),
        )
        _validate_bar(bar, previous, location)
        bars.append(bar)
        previous = bar.date
    return bars


def write_csv(bars: Iterable[Bar], path: str | Path) -> None:
    """Write validated bars as a UTF-8 BOM CSV with volume in shares."""
    rows = list(bars)
    validate_bars(rows)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_COLUMNS)
        for bar in rows:
            writer.writerow([
                bar.date.isoformat(), *(format(getattr(bar, field), "f") for field in ("open", "high", "low", "close")),
                bar.volume, "" if bar.amount is None else format(bar.amount, "f"),
                "" if bar.buy_allowed is None else str(bar.buy_allowed).lower(),
                "" if bar.sell_allowed is None else str(bar.sell_allowed).lower(),
            ])
