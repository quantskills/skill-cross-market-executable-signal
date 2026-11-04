from __future__ import annotations

import hashlib
import base64
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "collect_archive_snapshots.ps1"
FED_URL = "https://www.federalreserve.gov/releases/h10/20240108/"
FED_BODY = b"<html>Release Date: January 8, 2024<table><tr><td>CHINA</td><td>YUAN</td></tr></table></html>"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def study(tmp_path, source="fed", url=FED_URL, link_date="2024-01-08"):
    root = tmp_path / "study"
    root.mkdir()
    (root / "config.json").write_text('{"source_min_interval_seconds": 1.25}', encoding="utf-8")
    (root / "PREREGISTRATION.md").write_text("Frozen scope", encoding="utf-8")
    freeze = {"files": [{"name": name, "sha256": digest((root / name).read_bytes())}
                        for name in ("config.json", "PREREGISTRATION.md")]}
    (root / "scope-freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    (root / "archive-jobs.json").write_text(json.dumps({source: [
        {"source": source, "link_date": link_date, "url": url}]}), encoding="utf-8")
    return root


def seed(root, url=FED_URL, body=FED_BODY, source="fed", link_date="2024-01-08"):
    cache = root / "raw" / source
    cache.mkdir(parents=True, exist_ok=True)
    key = digest(url.encode())[:16]
    body_path = cache / f"{key}.body"
    body_path.write_bytes(body)
    metadata = {"url": url, "final_url": url, "status": 200, "sha256": digest(body),
                "bytes": len(body), "retrieved_at": "2026-11-03T12:00:00Z",
                "last_modified": None, "content_type": "text/html",
                "source": source, "link_date": link_date}
    (cache / f"{key}.meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return body_path


def run(root, source="fed", *args, forbid_network=True, transport=None):
    assert SCRIPT.is_file(), f"missing implementation: {SCRIPT}"
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 must be available"
    arguments = [str(SCRIPT), "-StudyRoot", str(root), "-Source", source, *args]
    if forbid_network or transport:
        quoted = " ".join(argument if argument in {"-StudyRoot", "-Source", "-Limit", "-Offset"}
                          else "'" + argument.replace("'", "''") + "'" for argument in arguments)
        prelude = transport or "function Invoke-WebRequest { throw 'TEST_NETWORK_FORBIDDEN' }"
        invocation = ["-Command", prelude + "; & " + quoted]
    else:
        invocation = ["-File", *arguments]
    return subprocess.run([pwsh, "-NoProfile", *invocation], capture_output=True, text=True,
                          encoding="utf-8", timeout=15)


def offline_downloader(root, bodies, *, fail=False, initial_delay_ms=0):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    mappings = "; ".join(f"{quote(url)}={quote(base64.b64encode(body).decode())}"
                         for url, body in bodies.items())
    event_path = quote(root / "transport-events.jsonl")
    return f"""
    $global:downloadCalls = 0; $global:transportBodies = @{{ {mappings} }};
    function Invoke-WebRequest {{
        param($Uri, [switch]$PassThru, $OutFile, $TimeoutSec, $MaximumRedirection)
        $global:downloadCalls++
        if ($global:downloadCalls -eq 1) {{ Start-Sleep -Milliseconds {initial_delay_ms} }}
        $start = [DateTimeOffset]::UtcNow.ToString('o')
        Start-Sleep -Milliseconds 40
        $end = [DateTimeOffset]::UtcNow.ToString('o')
        $event = @{{ start = $start; end = $end; uri = $Uri; timeout = $TimeoutSec;
            redirects = $MaximumRedirection; pass_thru = $PassThru.IsPresent; out_file = $OutFile }}
        [IO.File]::AppendAllText({event_path}, (($event | ConvertTo-Json -Compress) + "`n"))
        if (${str(fail).lower()}) {{ throw 'TEST_DOWNLOAD_FAILED' }}
        [IO.File]::WriteAllBytes($OutFile, [Convert]::FromBase64String($global:transportBodies[$Uri]))
        [pscustomobject]@{{ StatusCode = 200; Headers = @{{}}; BaseResponse = [pscustomobject]@{{
            RequestMessage = [pscustomobject]@{{ RequestUri = [Uri]$Uri }} }} }}
    }}
    """


def transport_events(root):
    return [json.loads(line) for line in (root / "transport-events.jsonl").read_text().splitlines()]


def parsed_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_freeze_drift_fails_before_collection(tmp_path):
    root = study(tmp_path)
    (root / "config.json").write_text("{}", encoding="utf-8")
    result = run(root)
    assert result.returncode != 0
    assert "freeze" in result.stderr.lower()
    assert not (root / "raw").exists()


def test_forbidden_host_fails_before_collection(tmp_path):
    root = study(tmp_path, url="https://example.invalid/releases/h10/20240108/")
    result = run(root)
    assert result.returncode != 0
    assert "url" in result.stderr.lower()
    assert not (root / "raw").exists()


@pytest.mark.parametrize("damage", ["orphan", "modified", "part"])
def test_damaged_cache_is_rejected(tmp_path, damage):
    root = study(tmp_path)
    body = seed(root)
    if damage == "orphan":
        body.with_suffix(".meta.json").unlink()
    elif damage == "modified":
        body.write_bytes(b"modified")
    else:
        body.with_suffix(".body.part").write_bytes(b"unfinished")
    result = run(root)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert not list((root / "raw").rglob("*.completed.json"))
    assert list((root / "raw").rglob("*.failed.json"))


def test_cached_fed_resumes_without_network_and_emits_receipt(tmp_path):
    root = study(tmp_path, url=FED_URL.rstrip("/"))
    body = seed(root)
    result = run(root)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.strip())
    assert receipt["source"] == "fed"
    assert receipt["job_url"] == FED_URL.rstrip("/")
    assert receipt["release_date"] == "2024-01-08"
    assert receipt["page"]["sha256"] == digest(FED_BODY)
    assert Path(receipt["page"]["body_path"]) == body
    assert receipt["page"]["cached"] is True
    assert Path(receipt["receipt_path"]).is_file()
    assert not (body.parent / "last_request.json").exists()
    repeated = run(root)
    assert repeated.returncode == 0, repeated.stderr


@pytest.mark.parametrize("body, error", [
    (FED_BODY.replace(b"YUAN", b"USD"), "china"),
])
def test_cached_fed_validates_publication_and_currency_row(tmp_path, body, error):
    root = study(tmp_path)
    seed(root, body=body)
    result = run(root)
    assert result.returncode != 0
    assert error in result.stderr.lower()
    assert not list((root / "raw").rglob("*.completed.json"))


def test_cached_eia_uses_csv_link_from_exact_row(tmp_path):
    url = "https://www.eia.gov/petroleum/supply/weekly/archive/2024/2024_01_08/wpsr_2024_01_08.php"
    root = study(tmp_path, "eia", url)
    page = b'''Release Date: January 8, 2024<table><tr><td>NYMEX Futures Prices</td>
        <td><a href="csv/custom.csv">CSV</a></td></tr></table>'''
    seed(root, url, page, "eia")
    csv_url = url.rsplit("/", 1)[0] + "/csv/custom.csv"
    csv_body = seed(root, csv_url, b"header\n", "eia")
    result = run(root, "eia")
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert Path(receipt["price_csv"]["body_path"]) == csv_body
    assert receipt["price_csv"]["sha256"] == digest(b"header\n")


def test_spot_mode_selects_spot_not_futures_and_resumes(tmp_path):
    url = "https://www.eia.gov/petroleum/supply/weekly/archive/2024/release.php"
    root = study(tmp_path, "eia", url)
    config = {"source_min_interval_seconds": 1.25, "eia_price_kind": "spot"}
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    freeze = {"files": [{"name": name, "sha256": digest((root / name).read_bytes())}
                        for name in ("config.json", "PREREGISTRATION.md")]}
    (root / "scope-freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    page = b'''Release Date: January 8, 2024<table>
      <tr><td>NYMEX Futures Prices</td><td><a href="csv/futures.csv">CSV</a></td></tr>
      <tr><td>Spot Prices of Crude Oil, Motor Gasoline, and Heating Oil</td>
      <td><a href="csv/spot.csv">CSV</a></td></tr></table>'''
    seed(root, url, page, "eia")
    csv_url = url.rsplit("/", 1)[0] + "/csv/spot.csv"
    seed(root, csv_url, b"spot\n", "eia")
    for _ in range(2):
        result = run(root, "eia")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["price_csv"]["url"] == csv_url


def test_eia_csv_outside_archive_directory_is_rejected(tmp_path):
    url = "https://www.eia.gov/petroleum/supply/weekly/archive/2024/release.php"
    root = study(tmp_path, "eia", url)
    seed(root, url, b'''Release Date: January 8, 2024<tr><td>NYMEX Futures Prices</td>
         <td><a href="../../outside.csv">CSV</a></td></tr>''', "eia")
    result = run(root, "eia")
    assert result.returncode != 0
    assert "csv" in result.stderr.lower()
    assert not list((root / "raw").rglob("*.completed.json"))


def test_changed_completed_receipt_is_not_silently_reused(tmp_path):
    root = study(tmp_path)
    seed(root)
    first = run(root)
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    receipt_path = Path(receipt["receipt_path"])
    receipt["page"]["body_path"] = "wrong-file.body"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    changed = receipt_path.read_bytes()
    repeated = run(root)
    assert repeated.returncode != 0
    assert "receipt" in repeated.stderr.lower()
    assert receipt_path.read_bytes() == changed


@pytest.mark.parametrize("url", [
    "https://user@www.federalreserve.gov/releases/h10/20240108/",
    FED_URL + "?download=1",
    FED_URL + "#fragment",
    "http://www.federalreserve.gov/releases/h10/20240108/",
])
def test_invalid_url_forms_are_rejected_before_collection(tmp_path, url):
    root = study(tmp_path, url=url)
    result = run(root)
    assert result.returncode != 0
    assert "url" in result.stderr.lower()
    assert not (root / "raw").exists()


def test_entire_source_job_array_is_checked_before_offset_selection(tmp_path):
    root = study(tmp_path)
    path = root / "archive-jobs.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["fed"].append({"source": "fed", "link_date": "2024-01-15", "url": "https://invalid.example/"})
    path.write_text(json.dumps(document), encoding="utf-8")
    result = run(root, "fed", "-Limit", "1")
    assert result.returncode != 0
    assert "url" in result.stderr.lower()
    assert not (root / "raw").exists()


def test_same_source_lock_blocks_concurrent_collection(tmp_path):
    root = study(tmp_path)
    body = seed(root)
    lock_path = body.parent / ".collector.lock"
    escaped_path = str(lock_path).replace("'", "''")
    holder = subprocess.Popen([
        shutil.which("pwsh"), "-NoProfile", "-Command",
        f"$f=[IO.File]::Open('{escaped_path}', 'OpenOrCreate', 'ReadWrite', 'None'); "
        "[Console]::WriteLine('LOCKED'); [Console]::Out.Flush(); Start-Sleep -Seconds 30; $f.Dispose()",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        result = run(root)
        assert result.returncode != 0
        assert not list((root / "raw").rglob("*.completed.json"))
        assert not (body.parent / "last_request.json").exists()
    finally:
        holder.terminate()
        holder.communicate(timeout=5)
    resumed = run(root)
    assert resumed.returncode == 0, resumed.stderr
    assert lock_path.exists()


def test_oversized_cached_body_is_not_accepted(tmp_path):
    root = study(tmp_path)
    seed(root, body=FED_BODY + b" " * (2 * 1024 * 1024))
    result = run(root)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert not list((root / "raw").rglob("*.completed.json"))


def test_completed_job_missing_both_cache_files_cannot_redownload(tmp_path):
    root = study(tmp_path)
    body = seed(root)
    first = run(root)
    assert first.returncode == 0, first.stderr
    receipt_path = Path(json.loads(first.stdout)["receipt_path"])
    original_receipt = receipt_path.read_bytes()
    body.unlink()
    body.with_suffix(".meta.json").unlink()
    result = run(root, forbid_network=True)
    assert result.returncode != 0
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert "cache" in result.stderr.lower() or "receipt" in result.stderr.lower()
    assert not (body.parent / "last_request.json").exists()
    assert receipt_path.read_bytes() == original_receipt


@pytest.mark.parametrize("field,value,missing", [
    ("retrieved_at", None, True),
    ("last_modified", None, True),
    ("content_type", None, True),
    ("retrieved_at", "", False),
    ("retrieved_at", None, False),
    ("retrieved_at", "not a timestamp", False),
    ("retrieved_at", "2026-11-03T12:00:00", False),
    ("retrieved_at", 123, False),
    ("last_modified", 123, False),
    ("last_modified", [], False),
    ("content_type", {}, False),
    ("content_type", True, False),
])
def test_incomplete_or_incompatible_cache_metadata_is_rejected(tmp_path, field, value, missing):
    root = study(tmp_path)
    body = seed(root)
    path = body.with_suffix(".meta.json")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if missing:
        del metadata[field]
    else:
        metadata[field] = value
    path.write_text(json.dumps(metadata), encoding="utf-8")
    result = run(root, forbid_network=True)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not list((root / "raw").rglob("*.completed.json"))


@pytest.mark.parametrize("retrieved,header", [
    ("2026-11-03T12:00:00Z", None),
    ("2026-11-03T20:00:00+08:00", ""),
    ("2026-11-03T07:00:00-05:00", "uninterpreted header value"),
])
def test_valid_metadata_and_explicit_absent_headers_are_preserved(tmp_path, retrieved, header):
    root = study(tmp_path)
    body = seed(root)
    path = body.with_suffix(".meta.json")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(retrieved_at=retrieved, last_modified=header, content_type=header)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    original = path.read_bytes()
    result = run(root, forbid_network=True)
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == original


def test_completed_eia_job_missing_csv_pair_cannot_redownload(tmp_path):
    url = "https://www.eia.gov/petroleum/supply/weekly/archive/2024/release.php"
    root = study(tmp_path, "eia", url)
    seed(root, url, b'''Release Date: January 8, 2024<tr><td>NYMEX Futures Prices</td>
         <td><a href="csv/actual.csv">CSV</a></td></tr>''', "eia")
    csv_body = seed(root, url.rsplit("/", 1)[0] + "/csv/actual.csv", b"header\n", "eia")
    first = run(root, "eia")
    assert first.returncode == 0, first.stderr
    csv_body.unlink()
    csv_body.with_suffix(".meta.json").unlink()
    result = run(root, "eia", forbid_network=True)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not (csv_body.parent / "last_request.json").exists()


def test_completed_batch_preflight_happens_before_any_new_job_request(tmp_path):
    root = study(tmp_path)
    body = seed(root)
    assert run(root).returncode == 0
    path = root / "archive-jobs.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["fed"].insert(0, {"source": "fed", "link_date": "2024-01-01",
                               "url": FED_URL.replace("20240108", "20240101")})
    path.write_text(json.dumps(document), encoding="utf-8")
    body.unlink()
    body.with_suffix(".meta.json").unlink()
    result = run(root, forbid_network=True)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not (body.parent / "last_request.json").exists()


def test_time_only_retrieval_cannot_invent_calendar_date(tmp_path):
    root = study(tmp_path)
    body = seed(root)
    path = body.with_suffix(".meta.json")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["retrieved_at"] = "12:00:00Z"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    result = run(root, forbid_network=True)
    assert result.returncode != 0
    assert "cache" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr


def test_first_download_records_response_and_request_end_time(tmp_path):
    root = study(tmp_path)
    result = run(root, transport=offline_downloader(root, {FED_URL: FED_BODY}))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["page"]["cached"] is False
    assert Path(receipt["page"]["body_path"]).read_bytes() == FED_BODY
    metadata = json.loads(Path(receipt["page"]["metadata_path"]).read_text())
    assert metadata["status"] == 200 and metadata["bytes"] == len(FED_BODY)
    assert metadata["sha256"] == digest(FED_BODY)
    assert metadata["last_modified"] == "" and metadata["content_type"] == ""
    event, = transport_events(root)
    assert event["timeout"] == 25 and event["redirects"] == 0 and event["pass_thru"] is True
    assert event["out_file"].endswith(".part")
    pacing = json.loads((root / "raw/fed/last_request.json").read_text())
    assert parsed_timestamp(pacing["last_request_at"]) >= parsed_timestamp(event["end"])


def test_consecutive_downloads_wait_full_interval_from_previous_end(tmp_path):
    root = study(tmp_path)
    second_url = FED_URL.replace("20240108", "20240115")
    path = root / "archive-jobs.json"
    document = json.loads(path.read_text())
    document["fed"].append({"source": "fed", "link_date": "2024-01-15", "url": second_url})
    path.write_text(json.dumps(document), encoding="utf-8")
    transport = offline_downloader(root, {FED_URL: FED_BODY,
        second_url: FED_BODY.replace(b"January 8", b"January 15")}, initial_delay_ms=120)
    result = run(root, transport=transport)
    assert result.returncode == 0, result.stderr
    first, second = transport_events(root)
    assert (parsed_timestamp(second["start"]) - parsed_timestamp(first["start"])).total_seconds() >= 1.25
    assert (parsed_timestamp(second["start"]) - parsed_timestamp(first["end"])).total_seconds() >= 1.25
    assert len(result.stdout.strip().splitlines()) == 2


def test_failed_download_persists_end_time_and_failure_receipt(tmp_path):
    root = study(tmp_path)
    result = run(root, transport=offline_downloader(root, {}, fail=True))
    assert result.returncode != 0
    assert "TEST_DOWNLOAD_FAILED" in result.stderr
    event, = transport_events(root)
    cache = root / "raw/fed"
    pacing = json.loads((cache / "last_request.json").read_text())
    assert parsed_timestamp(pacing["last_request_at"]) >= parsed_timestamp(event["end"])
    assert len(list(cache.glob("*.failed.json"))) == 1
    assert not list(cache.rglob("*.completed.json"))


def test_fed_holiday_archive_preserves_index_and_actual_release_and_resumes(tmp_path):
    url = FED_URL.replace("20240108", "20180903")
    root = study(tmp_path, url=url, link_date="2018-09-03")
    body = FED_BODY.replace(b"January 8, 2024", b"September 4, 2018")
    path = seed(root, url, body, link_date="2018-09-03")
    original_body = path.read_bytes()
    original_meta = path.with_suffix(".meta.json").read_bytes()
    first = run(root)
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    assert receipt["archive_index_date"] == "2018-09-03"
    assert receipt["release_date"] == "2018-09-04"
    assert receipt["page"]["cached"] is True
    assert "available_at" not in receipt
    repeated = run(root)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["release_date"] == "2018-09-04"
    assert path.read_bytes() == original_body
    assert path.with_suffix(".meta.json").read_bytes() == original_meta
    assert not (path.parent / "last_request.json").exists()


@pytest.mark.parametrize("date_text", [
    b"Release Date: January 8, 2024 Release Date: January 8, 2024",
    b"Release Date: January 8, 2024 Release Date: malformed",
    b"Release Date: malformed",
    b"Release Date: February 30, 2024",
    b"No publication date",
])
def test_fed_rejects_duplicate_malformed_or_missing_body_release_date(tmp_path, date_text):
    root = study(tmp_path)
    body = FED_BODY.replace(b"Release Date: January 8, 2024", date_text)
    seed(root, body=body)
    result = run(root)
    assert result.returncode != 0
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not list((root / "raw").rglob("*.completed.json"))


@pytest.mark.parametrize("field,changed", [("release_date", "2018-09-03"),
                                          ("archive_index_date", "2018-09-04")])
def test_fed_holiday_completed_receipt_dates_are_independently_checked(tmp_path, field, changed):
    url = FED_URL.replace("20240108", "20180903")
    root = study(tmp_path, url=url, link_date="2018-09-03")
    body = seed(root, url, FED_BODY.replace(b"January 8, 2024", b"September 4, 2018"), link_date="2018-09-03")
    first = run(root)
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    receipt[field] = changed
    receipt_path = Path(receipt["receipt_path"])
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = run(root)
    assert result.returncode != 0
    assert "receipt" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not (body.parent / "last_request.json").exists()


def test_eia_still_rejects_release_date_different_from_archive_index(tmp_path):
    url = "https://www.eia.gov/petroleum/supply/weekly/archive/2024/release.php"
    root = study(tmp_path, "eia", url)
    seed(root, url, b'''Release Date: January 9, 2024<tr><td>NYMEX Futures Prices</td>
         <td><a href="csv/actual.csv">CSV</a></td></tr>''', "eia")
    result = run(root, "eia")
    assert result.returncode != 0
    assert "release" in result.stderr.lower()
    assert "TEST_NETWORK_FORBIDDEN" not in result.stderr
    assert not list((root / "raw").rglob("*.completed.json"))
