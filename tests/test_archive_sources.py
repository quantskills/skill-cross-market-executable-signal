from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_archive_sources():
    module_path = Path(__file__).parents[1] / "scripts" / "archive_sources.py"
    assert module_path.is_file(), f"missing implementation: {module_path}"
    spec = importlib.util.spec_from_file_location("archive_sources", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conservative_available_at_uses_two_day_bound():
    module = _load_archive_sources()
    assert module.conservative_available_at(
        "2024-01-10",
        "Wed, 10 Jan 2024 13:06:31 GMT",
        "2026-11-03T12:00:00Z",
    ) == "2024-01-12T00:00:00+00:00"


def test_later_version_modification_shifts_availability_later():
    available_at = _load_archive_sources().conservative_available_at
    assert available_at(
        "2024-01-10",
        "Wed, 10 Jan 2024 13:06:31 GMT",
        "2026-11-03T12:00:00Z",
    ) == "2024-01-12T00:00:00+00:00"
    assert available_at(
        "2024-01-10",
        "Fri, 12 Jan 2024 13:06:31 GMT",
        "2026-11-03T12:00:00Z",
    ) == "2024-01-14T00:00:00+00:00"


def test_modified_timestamp_is_normalized_to_utc_before_taking_date():
    available_at = _load_archive_sources().conservative_available_at
    assert available_at(
        "2024-01-10",
        "Thu, 11 Jan 2024 00:30:00 +0200",
        "2026-11-03T12:00:00Z",
    ) == "2024-01-12T00:00:00+00:00"


def test_dst_boundary_dates_use_utc_calendar_days_not_new_york_clock():
    available_at = _load_archive_sources().conservative_available_at
    for release_date, last_modified, expected in (
        (
            "2024-03-10",
            "Sun, 10 Mar 2024 12:00:00 GMT",
            "2024-03-12T00:00:00+00:00",
        ),
        (
            "2024-11-03",
            "Sun, 03 Nov 2024 12:00:00 GMT",
            "2024-11-05T00:00:00+00:00",
        ),
    ):
        assert available_at(
            release_date,
            last_modified,
            "2026-11-03T12:00:00Z",
        ) == expected


def test_release_date_one_day_ahead_of_retrieval_is_allowed():
    available_at = _load_archive_sources().conservative_available_at
    assert available_at(
        "2024-01-11",
        "Wed, 10 Jan 2024 13:06:31 GMT",
        "2024-01-10T23:00:00Z",
    ) == "2024-01-13T00:00:00+00:00"


def test_timezone_less_last_modified_is_rejected():
    available_at = _load_archive_sources().conservative_available_at
    with pytest.raises(ValueError):
        available_at(
            "2024-01-10",
            "Wed, 10 Jan 2024 13:06:31",
            "2026-11-03T12:00:00Z",
        )


def test_release_date_allowance_uses_utc_retrieval_calendar_date():
    available_at = _load_archive_sources().conservative_available_at
    retrieved_at = "2024-01-10T23:30:00-02:00"  # 2024-01-11 in UTC
    last_modified = "Wed, 10 Jan 2024 13:06:31 GMT"

    assert available_at("2024-01-12", last_modified, retrieved_at) == "2024-01-14T00:00:00+00:00"
    with pytest.raises(ValueError):
        available_at("2024-01-13", last_modified, retrieved_at)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("2024-01-10", None, "2026-11-03T12:00:00Z"), id="missing-last-modified"),
        pytest.param(("2024-01-10", "", "2026-11-03T12:00:00Z"), id="absent-last-modified"),
        pytest.param(("2024-01-10", "not a date", "2026-11-03T12:00:00Z"), id="malformed-last-modified"),
        pytest.param(("2024-01-10", "Wed, 10 Jan 2024 13:06:31 GMT", "2026-11-03T12:00:00"), id="naive-retrieval"),
        pytest.param(("2024-01-10", "Wed, 10 Jan 2024 13:06:31 GMT", "not a timestamp"), id="malformed-retrieval"),
        pytest.param(("2024-01-10", "Thu, 05 Nov 2026 13:00:00 GMT", "2026-11-03T12:00:00Z"), id="future-modification"),
        pytest.param(("2026-11-05", "Wed, 10 Jan 2024 13:06:31 GMT", "2026-11-03T12:00:00Z"), id="future-release-date"),
        pytest.param(("2024-13-10", "Wed, 10 Jan 2024 13:06:31 GMT", "2026-11-03T12:00:00Z"), id="malformed-release-date"),
        pytest.param((123, "Wed, 10 Jan 2024 13:06:31 GMT", "2026-11-03T12:00:00Z"), id="release-date-type"),
        pytest.param(("2024-01-10", 123, "2026-11-03T12:00:00Z"), id="last-modified-type"),
        pytest.param(("2024-01-10", "Wed, 10 Jan 2024 13:06:31 GMT", 123), id="retrieval-type"),
        pytest.param(("2024-01-10", "Wed, 10 Jan 2024 13:06:31 GMT"), id="missing-retrieval"),
    ],
)
def test_invalid_metadata_and_times_raise_value_error(case):
    available_at = _load_archive_sources().conservative_available_at
    with pytest.raises(ValueError):
        available_at(*case)


def test_two_day_bound_overflow_raises_value_error():
    import pytest

    available_at = _load_archive_sources().conservative_available_at
    with pytest.raises(ValueError):
        available_at(
            "9999-12-30",
            "Thu, 30 Dec 9999 00:00:00 GMT",
            "9999-12-30T00:00:00Z",
        )
