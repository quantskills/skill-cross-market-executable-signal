"""Small synthetic archives: no copied source databases or network access."""
import importlib
import io
import csv

import pytest


def api():
    assert importlib.util.find_spec("archive_quotes"), "archive quote parser is not implemented"
    return importlib.import_module("archive_quotes")


WTI = "Crude Oil (WTI, Cushing, Oklahoma)"


def eia(headers=("Fri 12/29", "Tue 1/2"), rows=None, encoding="utf-8-sig"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["STUB_1", "STUB_2", *headers])
    writer.writerows(rows if rows is not None else [[WTI, "March-2024", "70", "77"]])
    return output.getvalue().encode(encoding)


def test_eia_year_boundary():
    quotes = api().parse_eia_quotes(eia(), "2024-01-10")
    assert quotes == [
        dict(source="eia", release_date="2024-01-10", observation_date="2023-12-29", delivery_month="2024-03", value=70.0),
        dict(source="eia", release_date="2024-01-10", observation_date="2024-01-02", delivery_month="2024-03", value=77.0),
    ]


@pytest.mark.parametrize("headers,release", [
    (("Wed 2/28", "Thu 2/29"), "2024-03-04"),
    (("Fri 12/29", "Mon 1/1"), "2024-01-03"),
])
def test_eia_valid_calendar(headers, release):
    assert len(api().parse_eia_quotes(eia(headers), release)) == 2


@pytest.mark.parametrize("headers", [
    ("Mon 12/29", "Tue 1/2"), ("Fri 12/29", "Thu 1/11"),
    ("Mon 11/20", "Tue 1/2"), ("Tue 1/2", "Tue 1/2"),
    ("Tue 1/2", "Fri 12/29"), ("Fri 12/29",),
    ("Friday 12/29", "Tue 1/2"), ("Wed 2/29", "Tue 1/2"),
])
def test_eia_rejects_bad_headers(headers):
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia(headers), "2024-01-10")


@pytest.mark.parametrize("contract", ["Mar-2024", "March/2024", "March-24", "March-2024x", "2024-03"])
def test_eia_rejects_bad_maturity(contract):
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia(rows=[[WTI, contract, "70", "77"]]), "2024-01-10")


def test_eia_accepts_space_and_checks_all_widths():
    assert api().parse_eia_quotes(eia(rows=[[WTI, "March 2024", "70", "77"], ["Other", "ignore", "x", "y"]]), "2024-01-10")
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia(rows=[[WTI, "March-2024", "70", "77"], ["Other", "ignored", "x"]]), "2024-01-10")


@pytest.mark.parametrize("rows", [[], [["Other", "March-2024", "70", "77"]], [[WTI, "March-2024", "70", "77"], [WTI, "March 2024", "71", "78"]]])
def test_eia_rejects_missing_or_duplicate_wti(rows):
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia(rows=rows), "2024-01-10")


@pytest.mark.parametrize("marker", ["", "-", "–", "—"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_eia_missing_markers(marker, encoding):
    quotes = api().parse_eia_quotes(eia(rows=[[WTI, "March-2024", marker, "-37.63"]], encoding=encoding), "2024-01-10")
    assert [q["value"] for q in quotes] == [None, -37.63]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "1,234", "1.2.3", "ND", "unknown"])
def test_eia_rejects_unknown_or_nonfinite_numbers(value):
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia(rows=[[WTI, "March-2024", value, "0"]]), "2024-01-10")


def test_eia_rejects_missing_stub():
    with pytest.raises(ValueError):
        api().parse_eia_quotes(eia().replace(b"STUB_2", b"WRONG"), "2024-01-10")


def fed(headers=("Dec. 29", "Jan. 2"), rows=None, release="January 8, 2024", units="currency units per U.S. dollar"):
    rows = rows if rows is not None else [["CHINA, P.R.", "YUAN", "7.0", "7.1"]]
    table = "<table><tr>" + "".join(f"<th>{v}</th>" for v in ["COUNTRY", "CURRENCY", *headers]) + "</tr>"
    table += "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows) + "</table>"
    return f"<html><body><div>Release Date: {release}</div><p>Rates in {units}</p>{table}</body></html>".encode()


def test_fed_nested_markup_year_boundary_and_direction():
    raw = fed(rows=[["CH<span>INA</span>, P.R.", "<b>YUAN</b>", "<i>7.0</i>", "7.1"]])
    quotes = api().parse_fed_quotes(raw, "2024-01-08")
    assert quotes == [
        dict(source="fed", release_date="2024-01-08", observation_date="2023-12-29", pair="CNY/USD", value=7.0),
        dict(source="fed", release_date="2024-01-08", observation_date="2024-01-02", pair="CNY/USD", value=7.1),
    ]


@pytest.mark.parametrize("missing", ["ND", ""])
def test_fed_missing_quotes(missing):
    assert api().parse_fed_quotes(fed(rows=[["CHINA, P.R.", "YUAN", missing, "7.1"]]), "2024-01-08")[0]["value"] is None


@pytest.mark.parametrize("headers", [("Dec. 29", "Dec 29"), ("Jan 2", "Dec 29"), ("Nov 20", "Jan 2"), ("Jan 2", "Jan 9"), ("Dec 29",), ("December 29", "Jan 2")])
def test_fed_rejects_bad_headers(headers):
    with pytest.raises(ValueError):
        api().parse_fed_quotes(fed(headers), "2024-01-08")


@pytest.mark.parametrize("rows", [[], [["JAPAN", "YEN", "7", "8"]], [["CHINA, P.R.", "YEN", "7", "8"]], [["CHINA, P.R.", "YUAN", "7"]], [["CHINA, P.R.", "YUAN", "7", "8"], ["CHINA, P.R.", "YUAN", "7", "8"]]])
def test_fed_rejects_bad_china_row(rows):
    with pytest.raises(ValueError):
        api().parse_fed_quotes(fed(rows=rows), "2024-01-08")


@pytest.mark.parametrize("value", ["0", "-7", "NaN", "Infinity", "1e999", "—", "unknown"])
def test_fed_rejects_bad_values(value):
    with pytest.raises(ValueError):
        api().parse_fed_quotes(fed(rows=[["CHINA, P.R.", "YUAN", value, "7.1"]]), "2024-01-08")


@pytest.mark.parametrize("raw", [fed(release="January 9, 2024"), fed(units="U.S. dollars per currency unit"), fed().replace(b"Release Date:", b"Index Date:"), fed() + fed(), fed() + b"<div>Release Date: January 8, 2024</div>"])
def test_fed_rejects_ambiguous_release_or_units(raw):
    with pytest.raises(ValueError):
        api().parse_fed_quotes(raw, "2024-01-08")


def test_fed_ignores_nonvisible_release_and_preserves_visible_date():
    raw = fed().replace(b"<body>", b'<body><script>Release Date: January 9, 2024</script><div hidden>Release Date: January 10, 2024</div>')
    assert len(api().parse_fed_quotes(raw, "2024-01-08")) == 2


def test_fed_rejects_multiple_candidate_tables():
    raw = fed()
    table = raw[raw.index(b"<table>"):raw.index(b"</table>") + len(b"</table>")]
    with pytest.raises(ValueError):
        api().parse_fed_quotes(raw + table, "2024-01-08")


def quote(day="2024-01-02", value=77.0, maturity="2024-03", source="eia"):
    result = dict(source=source, release_date="2024-01-10", observation_date=day, value=value)
    result["delivery_month" if source == "eia" else "pair"] = maturity if source == "eia" else "CNY/USD"
    return result


def test_feature_selects_nearest_qualifying_contract_and_last_nonmissing():
    quotes = [quote("2024-01-03", None), quote("2023-12-29", 70), quote()]
    quotes += [quote("2023-12-29", 10, "2024-02"), quote(value=50, maturity="2024-02")]
    quotes += [quote("2023-12-29", 20, "2024-04"), quote(value=80, maturity="2024-04")]
    result = api().release_feature(quotes, "eia", "2024-01-10")
    assert result == dict(source="eia", release_date="2024-01-10", delivery_month="2024-03", status="ok", value=pytest.approx(.1), from_date="2023-12-29", to_date="2024-01-02")


def test_feature_default_with_parsed_quotes():
    result = api().release_feature(api().parse_eia_quotes(eia(), "2024-01-10"), "eia", "2024-01-10")
    assert result["value"] == pytest.approx(.1)


@pytest.mark.parametrize("value", [0, -37.63])
def test_feature_no_fallback_from_latest_nonpositive(value):
    quotes = [quote("2023-12-27", 65), quote("2023-12-29", 70), quote(value=value)]
    quotes += [quote("2023-12-29", 20, "2024-04"), quote(value=80, maturity="2024-04")]
    result = api().release_feature(quotes, "eia", "2024-01-10")
    assert (result["status"], result["value"], result["delivery_month"]) == ("nonpositive_price", None, "2024-03")


def test_feature_no_maturity_fallback_for_missing_values():
    quotes = [quote(value=None), quote("2023-12-29", 20, "2024-04"), quote(value=80, maturity="2024-04")]
    result = api().release_feature(quotes, "eia", "2024-01-10")
    assert result == dict(source="eia", release_date="2024-01-10", delivery_month="2024-03", status="insufficient_observations", value=None)


def test_feature_no_qualifying_contract_and_empty_fed():
    assert api().release_feature([quote(maturity="2024-02")], "eia", "2024-01-10") == dict(source="eia", release_date="2024-01-10", delivery_month=None, status="no_qualifying_contract", value=None)
    assert api().release_feature([], "fed", "2024-01-10") == dict(source="fed", release_date="2024-01-10", pair="CNY/USD", status="insufficient_observations", value=None)


def test_feature_offset_calendar_arithmetic():
    quotes = [dict(quote("2024-11-29", 70, "2025-02"), release_date="2024-12-10"), dict(quote("2024-12-02", 77, "2025-02"), release_date="2024-12-10")]
    assert api().release_feature(quotes, "eia", "2024-12-10")["value"] == pytest.approx(.1)
    assert api().release_feature([quote(maturity="2024-01")], "eia", "2024-01-10", 0)["delivery_month"] == "2024-01"


@pytest.mark.parametrize("offset", [-1, True, 2.0, "2", None])
def test_feature_rejects_bad_offset(offset):
    with pytest.raises(ValueError):
        api().release_feature([], "eia", "2024-01-10", offset)


@pytest.mark.parametrize("change", [
    {"source": "fed"}, {"release_date": "2024-01-09"},
    {"observation_date": "2024-01-11"}, {"observation_date": "2023-12-05"},
    {"observation_date": "2024-02-30"}, {"observation_date": "2024-1-2"},
    {"delivery_month": "2024-13"}, {"delivery_month": "2024-3"},
    {"value": float("inf")}, {"value": float("nan")}, {"value": "77"}, {"value": True},
])
def test_feature_validates_every_quote_even_unselected(change):
    invalid = dict(quote(maturity="2024-01"), **change)
    with pytest.raises(ValueError):
        api().release_feature([quote("2023-12-29", 70), quote(), invalid], "eia", "2024-01-10")


@pytest.mark.parametrize("source", ["eia", "fed"])
def test_feature_rejects_duplicate_keys(source):
    row = quote(source=source)
    with pytest.raises(ValueError):
        api().release_feature([row, row.copy()], source, "2024-01-10")


def test_feature_fed_last_nonmissing_and_pair_validation():
    quotes = [quote("2023-12-29", 7, source="fed"), quote(value=7.1, source="fed"), quote("2024-01-03", None, source="fed")]
    result = api().release_feature(quotes, "fed", "2024-01-10")
    assert result["pair"] == "CNY/USD"
    assert result["value"] == pytest.approx(7.1 / 7 - 1)
    with pytest.raises(ValueError):
        api().release_feature([dict(quotes[0], pair="USD/CNY")], "fed", "2024-01-10")


@pytest.mark.parametrize("source,release", [("other", "2024-01-10"), ("eia", "2024-02-30"), ("fed", "2024-1-10")])
def test_feature_rejects_invalid_release_or_source(source, release):
    with pytest.raises(ValueError):
        api().release_feature([], source, release)


@pytest.mark.parametrize("release", ["January 8, 20240", "January 8, 2024x"])
def test_fed_rejects_malformed_body_year(release):
    with pytest.raises(ValueError):
        api().parse_fed_quotes(fed(release=release), "2024-01-08")


def test_eia_keeps_zero_and_accepts_exact_35_day_boundary():
    quotes = api().parse_eia_quotes(eia(("Wed 12/6", "Tue 1/2"), [[WTI, "March-2024", "0", "77"]]), "2024-01-10")
    assert quotes[0]["value"] == 0.0
    assert quotes[0]["observation_date"] == "2023-12-06"


def test_feature_does_not_skip_previous_nonpositive():
    quotes = [quote("2023-12-27", 65), quote("2023-12-29", 0), quote()]
    assert api().release_feature(quotes, "eia", "2024-01-10")["status"] == "nonpositive_price"


def test_fed_accepts_leap_day_and_periodless_month():
    quotes = api().parse_fed_quotes(fed(("Feb 28", "Feb. 29"), release="March 4, 2024"), "2024-03-04")
    assert [q["observation_date"] for q in quotes] == ["2024-02-28", "2024-02-29"]


def test_feature_rejects_overflow_from_finite_positive_quotes():
    quotes = [quote("2023-12-29", 1e-320), quote(value=7.1)]
    with pytest.raises(ValueError, match="Nonfinite release feature"):
        api().release_feature(quotes, "eia", "2024-01-10")
