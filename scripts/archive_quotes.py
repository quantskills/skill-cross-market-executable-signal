"""Pure, fail-closed parsers for saved public EIA/Fed quote releases.

These functions never infer publication availability or evaluate a strategy.
"""
import csv
import io
import math
import re
from datetime import date, timedelta
from html.parser import HTMLParser


_MONTHS = ("January February March April May June July August September October November December").split()
_WEEKDAYS = "Mon Tue Wed Thu Fri Sat Sun".split()
_WTI = "Crude Oil (WTI, Cushing, Oklahoma)"


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected ISO calendar date")
    return date.fromisoformat(value)


def _recent(month, day, release, weekday=None):
    # Search the bounded window itself, so future and stale dates cannot wrap.
    for age in range(36):
        candidate = release - timedelta(days=age)
        if (candidate.month, candidate.day) == (month, day):
            if weekday is not None and _WEEKDAYS[candidate.weekday()] != weekday:
                raise ValueError("Observation weekday mismatch")
            return candidate.isoformat()
    raise ValueError("Observation date outside 35-day release window")


def _ordered(dates):
    if len(dates) < 2 or any(a >= b for a, b in zip(dates, dates[1:])):
        raise ValueError("Expected at least two unique increasing observation dates")
    return dates


def _number(text, missing, positive=False):
    text = text.strip()
    if text in missing:
        return None
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ValueError("Malformed quote value")
    value = float(text)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("Invalid quote value")
    return value


def parse_eia_quotes(raw: bytes, release_date: str) -> list[dict]:
    """Parse WTI delivery contracts, preserving missing/nonpositive prices."""
    release = _iso(release_date)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError("Malformed EIA CSV") from exc
    if not rows or rows[0][:2] != ["STUB_1", "STUB_2"]:
        raise ValueError("Missing EIA stub columns")
    header = rows[0]
    dates = []
    for cell in header[2:]:
        match = re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2})/(\d{1,2})", cell.strip())
        if not match:
            raise ValueError("Malformed EIA date header")
        dates.append(_recent(int(match[2]), int(match[3]), release, match[1]))
    _ordered(dates)
    quotes, seen = [], set()
    for row in rows[1:]:
        if len(row) != len(header):
            raise ValueError("Inconsistent EIA row width")
        if row[0].strip() != _WTI:
            continue
        match = re.fullmatch(r"(" + "|".join(_MONTHS) + r")(?:-| )(\d{4})", row[1].strip())
        if not match or int(match[2]) == 0:
            raise ValueError("Malformed WTI delivery month")
        delivery = f"{match[2]}-{_MONTHS.index(match[1]) + 1:02d}"
        if delivery in seen:
            raise ValueError("Duplicate WTI delivery month")
        seen.add(delivery)
        quotes.extend(dict(source="eia", release_date=release_date,
                           observation_date=day, delivery_month=delivery,
                           value=_number(value, {"", "-", "–", "—"}))
                      for day, value in zip(dates, row[2:]))
    if not seen:
        raise ValueError("No WTI quotes")
    return quotes


def parse_eia_spot_quotes(raw: bytes, release_date: str) -> list[dict]:
    """Read WPSR table 11 daily WTI spot quotes, not monthly/weekly averages.

    The second header has three stubs, two weekly averages, then daily columns.
    Missing and nonpositive quotes are preserved; feature construction decides
    how to handle them without silently filling or dropping inconvenient days.
    """
    release = _iso(release_date)
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    rows = list(csv.reader(io.StringIO(text.rstrip('\x1a\r\n'), newline=''), strict=True))
    headers = [i for i, row in enumerate(rows)
               if row[:3] == ['STUB_1', 'STUB_2', 'STUB_3']
               and len(row) > 3 and row[3] != 'STUB_4']
    if len(headers) != 1:
        raise ValueError('Expected one EIA spot daily section')
    start = headers[0]
    header = rows[start]
    dates = []
    for cell in header[3:]:
        match = re.fullmatch(r'(Mon|Tue|Wed|Thu|Fri) (\d{1,2})/(\d{1,2})', cell.strip())
        if not match:
            raise ValueError('Malformed EIA spot date header')
        dates.append(_recent(int(match[2]), int(match[3]), release, match[1]))
    _ordered(dates)
    if len(dates) < 4 or not all(header[i].startswith('Fri ') for i in (3, 4)):
        raise ValueError('Unexpected EIA weekly-average columns')
    matches = [row for row in rows[start + 1:]
               if len(row) >= 3 and row[0].strip() == 'Crude Oil'
               and row[1].strip() == 'WTI - Cushing' and not row[2].strip()]
    if len(matches) != 1 or len(matches[0]) != len(header):
        raise ValueError('Expected one complete WTI spot row')
    return [dict(source='eia_spot', instrument='WTI_CUSHING_SPOT',
                 release_date=release_date, observation_date=day,
                 value=_number(value, {'', '-', '–', '—'}))
            for day, value in zip(dates[2:], matches[0][5:])]


def _normal(text):
    return " ".join(text.split())


class _Tables(HTMLParser):
    """Collect visible text and plain cells; inline markup retains its text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.tables, self.hidden = [], [], []
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        invisible = (tag in {"head", "script", "style", "template"}
                     or "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true"
                     or "display:none" in style or "visibility:hidden" in style)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.hidden.append((tag, invisible or bool(self.hidden and self.hidden[-1][1])))
        if invisible or (self.hidden and self.hidden[-1][1]):
            return
        if tag in {"div", "p", "br", "table", "tr", "td", "th"}:
            self.text.append(" ")
        if tag == "table":
            if self.table is not None:
                raise ValueError("Nested tables are unsupported")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        invisible = bool(self.hidden and self.hidden[-1][1])
        for index in range(len(self.hidden) - 1, -1, -1):
            if self.hidden[index][0] == tag:
                del self.hidden[index:]
                break
        if invisible:
            return
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(_normal("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        if tag in {"div", "p", "table", "tr", "td", "th"}:
            self.text.append(" ")

    def handle_data(self, data):
        if self.hidden and self.hidden[-1][1]:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)


def parse_fed_quotes(raw: bytes, release_date: str) -> list[dict]:
    """Parse the explicitly CNY-per-USD row from the actual visible release."""
    release = _iso(release_date)
    parser = _Tables()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    visible = _normal("".join(parser.text))
    pattern = r"Release Date:\s*(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})(?!\w)"
    matches = list(re.finditer(pattern, visible))
    if len(matches) != 1 or len(re.findall(r"Release Date:", visible)) != 1:
        raise ValueError("Expected one visible Fed release date")
    match = matches[0]
    actual = date(int(match[3]), _MONTHS.index(match[1]) + 1, int(match[2]))
    if actual != release:
        raise ValueError("Fed body release date mismatch")
    if "currency units per u.s. dollar" not in visible.lower():
        raise ValueError("Missing explicit CNY/USD unit direction")
    candidates = [(table, row) for table in parser.tables for row in table
                  if row[:2] == ["COUNTRY", "CURRENCY"]]
    if len(candidates) != 1:
        raise ValueError("Expected one Fed quote table")
    table, header = candidates[0]
    dates = []
    abbreviations = [month[:3] for month in _MONTHS]
    for cell in header[2:]:
        match = re.fullmatch(r"(" + "|".join(abbreviations) + r")\.?\s+(\d{1,2})", cell)
        if not match:
            raise ValueError("Malformed Fed observation date")
        dates.append(_recent(abbreviations.index(match[1]) + 1, int(match[2]), release))
    _ordered(dates)
    china = [row for row in table if row and row[0] == "CHINA, P.R."]
    if len(china) != 1 or len(china[0]) != len(header) or china[0][1] != "YUAN":
        raise ValueError("Expected one complete China/YUAN row")
    return [dict(source="fed", release_date=release_date, observation_date=day,
                 pair="CNY/USD", value=_number(value, {"", "ND"}, positive=True))
            for day, value in zip(dates, china[0][2:])]


def release_feature(quotes: list[dict], source: str, release_date: str,
                    delivery_offset: int = 2) -> dict:
    """Describe the last two observations without maturity/price fallback."""
    release = _iso(release_date)
    if source not in {"eia", "fed"}:
        raise ValueError("Unsupported quote source")
    if isinstance(delivery_offset, bool) or not isinstance(delivery_offset, int) or delivery_offset < 0:
        raise ValueError("Delivery offset must be a nonnegative integer")
    field = "delivery_month" if source == "eia" else "pair"
    seen = set()
    for row in quotes:
        if row.get("source") != source or row.get("release_date") != release_date:
            raise ValueError("Mixed quote source or release")
        observation = _iso(row.get("observation_date"))
        if not 0 <= (release - observation).days <= 35:
            raise ValueError("Quote outside release observation window")
        identity = row.get(field)
        if source == "eia":
            if not isinstance(identity, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", identity) or identity[:4] == "0000":
                raise ValueError("Invalid delivery month")
        elif identity != "CNY/USD":
            raise ValueError("Unexpected FX direction")
        key = (identity, observation)
        if key in seen:
            raise ValueError("Duplicate quote identity/date")
        seen.add(key)
        if "value" not in row or (row["value"] is not None and
                (isinstance(row["value"], bool) or not isinstance(row["value"], (int, float))
                 or not math.isfinite(row["value"]))):
            raise ValueError("Quote must be a finite number or None")
    result = dict(source=source, release_date=release_date, value=None)
    selected = "CNY/USD"
    if source == "eia":
        threshold = release.year * 12 + release.month - 1 + delivery_offset
        qualifying = {row[field] for row in quotes
                      if int(row[field][:4]) * 12 + int(row[field][5:]) - 1 >= threshold}
        selected = min(qualifying) if qualifying else None
    result[field] = selected
    if selected is None:
        return dict(result, status="no_qualifying_contract")
    available = sorted((row for row in quotes if row[field] == selected and row["value"] is not None),
                       key=lambda row: row["observation_date"])
    if len(available) < 2:
        return dict(result, status="insufficient_observations")
    previous, latest = available[-2:]
    if previous["value"] <= 0 or latest["value"] <= 0:
        return dict(result, status="nonpositive_price")
    value = latest["value"] / previous["value"] - 1
    if not math.isfinite(value):
        raise ValueError("Nonfinite release feature")
    return dict(result, status="ok", value=value,
                from_date=previous["observation_date"], to_date=latest["observation_date"])
