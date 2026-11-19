from datetime import date
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from talent_signal.app import app
from talent_signal.storage import COLUMNS, import_snapshot, read_snapshot

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data"
CUTOFF = date(2025, 7, 31)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    db = tmp_path / "demo snapshot.sqlite3"
    monkeypatch.setenv("TALENT_SIGNAL_DB", str(db))
    source = tmp_path / "csv"
    shutil.copytree(SAMPLE, source)
    import_snapshot(source, db, CUTOFF)
    return TestClient(app), source, db


def test_demo_metrics_and_distinct_units(demo):
    client, _, _ = demo
    response = client.get("/api/channels")
    assert response.status_code == 200
    result = response.json()
    assert result["as_of"] == CUTOFF.isoformat()
    channels = {row["channel"]: row for row in result["channels"]}
    assert channels["招聘平台"] == {
        "channel": "招聘平台",
        "candidates": 6,
        "hires": 4,
        "hire_rate": 4 / 6,
        "eligible_30d": 3,
        "retained_30d": 2,
        "retention_rate_30d": 2 / 3,
        "assessment_count": 4,
        "assessment_mean": 81,
        "performance_180d_count": 2,
        "performance_180d_mean": 86,
    }
    assert channels["员工内推"]["retention_rate_30d"] == 0.5
    assert channels["校园招聘"]["retention_rate_30d"] == 1
    assert channels["社群招聘"]["hires"] == 0
    assert channels["社群招聘"]["retention_rate_30d"] is None
    assert channels["社群招聘"]["performance_180d_mean"] is None
    cohorts = {row["unit"]: row for row in result["monthly_cohorts"]}
    assert len(cohorts) == 11
    assert cohorts["长沙-中电"]["status"] == "observing"
    assert cohorts["长沙-芯城"]["retention_rate_30d"] is None
    assert cohorts["南宁"]["retention_rate_30d"] == 0
    assert cohorts["成都"]["retention_rate_30d"] == 1
    assert cohorts["沈阳"]["exits_observed_30d"] == 1


def test_historical_cutoff_and_calendar_maturity(demo):
    client, _, _ = demo
    before = client.get("/api/channels", params={"as_of": "2025-02-08"}).json()
    referral = next(row for row in before["channels"] if row["channel"] == "员工内推")
    assert referral["eligible_30d"] == 1
    assert referral["retention_rate_30d"] == 0
    assert sum(row["performance_180d_count"] for row in before["channels"]) == 0
    assert all(row["status"] == "observing" for row in before["monthly_cohorts"])
    for cutoff, status in [("2025-03-01", "observing"), ("2025-03-02", "mature")]:
        cohorts = client.get("/api/channels", params={"as_of": cutoff}).json()["monthly_cohorts"]
        assert all(row["status"] == status for row in cohorts)


def test_no_lookahead_for_future_hires_assessments_and_exits(demo):
    client, _, _ = demo
    result = client.get("/api/channels", params={"as_of": "2025-01-09"}).json()
    assert sum(row["hires"] for row in result["channels"]) == 2
    assert sum(row["assessment_count"] for row in result["channels"]) == 0
    assert sum(row["exits_observed_30d"] for row in result["monthly_cohorts"]) == 0


def test_quality_and_html(demo):
    client, _, _ = demo
    quality = client.get("/api/data-quality").json()
    assert quality["status"] == "passed"
    assert quality["row_counts"] == {"candidates": 18, "hires": 11, "assessments": 9, "performance": 3}
    response = client.get("/report")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    for text in ("66.7%", "长沙-中电", "长沙-芯城", "观察中", "2025-07-31", "86.00"):
        assert text in response.text
    assert client.get("/").text == response.text
    assert client.get("/docs").status_code == 200


def test_invalid_data_blocks_metrics_and_reports_counts(demo):
    client, source, db = demo
    bad_rows = {
        "candidates": "C001,重复渠道,2025-01-01\nC020,,2025-02-30\n",
        "hires": "E020,C404,北京,2025-02-01,2025-01-20\nE021,C012,南昌,2025-07-01,\n",
        "assessments": "E404,2025-01-01,NaN\nE004,2025-03-01,105\n",
        "performance": "E002,2025-07-08,80\nE005,2025-08-01,75\n",
    }
    for table, rows in bad_rows.items():
        with (source / f"{table}.csv").open("a", encoding="utf-8") as file:
            file.write(rows)
    import_snapshot(source, db, CUTOFF)
    quality = client.get("/api/data-quality").json()
    assert quality["status"] == "blocked"
    issues = {(row["table"], row["field"], row["code"]): row["count"] for row in quality["issues"]}
    assert issues["candidates", "candidate_id", "duplicate"] == 2
    assert issues["candidates", "channel", "missing"] == 1
    assert issues["candidates", "applied_on", "invalid_date"] == 1
    assert issues["hires", "unit", "unknown_unit"] == 1
    assert issues["hires", "candidate_id", "orphan"] == 1
    assert issues["hires", "hired_on", "before_application"] == 1
    assert issues["hires", "exited_on", "before_hire"] == 1
    assert issues["assessments", "employee_id", "orphan"] == 1
    assert issues["assessments", "score", "invalid_score"] == 2
    assert issues["assessments", "assessed_on", "outside_observation_window"] == 1
    assert issues["performance", "reviewed_on", "after_exit"] == 1
    assert issues["performance", "reviewed_on", "after_snapshot"] == 1
    response = client.get("/api/channels")
    assert response.status_code == 409
    assert response.json()["detail"]["issues"] == quality["issues"]
    report = client.get("/report")
    assert report.status_code == 409
    assert "指标暂停展示" in report.text
    assert "渠道汇总" not in report.text


def test_malformed_csv_does_not_replace_previous_snapshot(demo):
    _, source, db = demo
    with (source / "performance.csv").open("a", encoding="utf-8") as file:
        file.write("too,few\n")
    with pytest.raises(ValueError, match="列数不符"):
        import_snapshot(source, db, date(2025, 8, 1))
    cutoff, tables = read_snapshot(db)
    assert cutoff == CUTOFF
    assert len(tables["performance"]) == 3


def test_repeat_import_and_empty_data(demo):
    client, source, db = demo
    import_snapshot(source, db, CUTOFF)
    assert client.get("/api/data-quality").json()["row_counts"]["hires"] == 11
    for table, columns in COLUMNS.items():
        (source / f"{table}.csv").write_text(",".join(columns) + "\n", encoding="utf-8")
    import_snapshot(source, db, CUTOFF)
    result = client.get("/api/channels").json()
    assert result["channels"] == result["monthly_cohorts"] == []
    assert "暂无候选人" in client.get("/report").text


@pytest.mark.parametrize("as_of", ["2025-08-01", "invalid"])
def test_invalid_as_of(demo, as_of):
    client, _, _ = demo
    assert client.get("/api/channels", params={"as_of": as_of}).status_code == 422


def test_uninitialized_database(tmp_path, monkeypatch):
    db = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("TALENT_SIGNAL_DB", str(db))
    client = TestClient(app)
    assert client.get("/api/data-quality").status_code == 503
    assert not db.exists()
    db.touch()
    assert client.get("/report").status_code == 503
