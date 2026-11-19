"""Validate snapshot records and calculate observed recruiting outcomes."""

from calendar import monthrange
from datetime import date
import math
import re

import pandas as pd

UNITS = (
    "南昌", "南宁", "太原", "晋中", "上海", "成都", "武汉", "郑州",
    "沈阳", "长沙-中电", "长沙-芯城",
)
DATE_FIELDS = {
    "candidates": ("applied_on",),
    "hires": ("hired_on", "exited_on"),
    "assessments": ("assessed_on",),
    "performance": ("reviewed_on",),
}
KEYS = {
    "candidates": ("candidate_id",),
    "hires": ("employee_id", "candidate_id"),
    "assessments": ("employee_id",),
    "performance": ("employee_id",),
}


def _date(value: str) -> date | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def check_quality(tables: dict[str, pd.DataFrame], cutoff: date) -> list[dict]:
    issues = []

    def flag(table, field, code, mask):
        count = int(mask.sum())
        if count:
            issues.append({"table": table, "field": field, "code": code, "count": count})

    for table, frame in tables.items():
        for field in frame.columns:
            if field != "exited_on":
                flag(table, field, "missing", frame[field].eq(""))
        for key in KEYS[table]:
            flag(table, key, "duplicate", frame[key].ne("") & frame[key].duplicated(keep=False))
        for field in DATE_FIELDS[table]:
            raw = frame[field]
            values = raw.map(_date)
            flag(table, field, "invalid_date", raw.ne("") & values.isna())
            flag(table, field, "after_snapshot", values.map(lambda value: value is not None and value > cutoff))
        if "score" in frame:
            scores = pd.to_numeric(frame["score"], errors="coerce")
            flag(table, "score", "invalid_score", frame["score"].ne("") & ~scores.between(0, 100))

    candidates = tables["candidates"]
    hires = tables["hires"]
    flag("hires", "unit", "unknown_unit", hires["unit"].ne("") & ~hires["unit"].isin(UNITS))
    flag("hires", "candidate_id", "orphan", hires["candidate_id"].ne("") &
         ~hires["candidate_id"].isin(candidates["candidate_id"]))
    for table in ("assessments", "performance"):
        ids = tables[table]["employee_id"]
        flag(table, "employee_id", "orphan", ids.ne("") & ~ids.isin(hires["employee_id"]))

    applications = candidates.drop_duplicates("candidate_id", keep=False).set_index("candidate_id")

    def before(first, second):
        return first is not None and second is not None and first < second

    hired = hires["hired_on"].map(_date)
    exited = hires["exited_on"].map(_date)
    applied = hires["candidate_id"].map(applications["applied_on"]).fillna("").map(_date)
    flag("hires", "hired_on", "before_application", pd.Series(
        [before(hire, application) for hire, application in zip(hired, applied)], dtype=bool
    ))
    flag("hires", "exited_on", "before_hire", pd.Series(
        [before(exit_day, hire) for exit_day, hire in zip(exited, hired)], dtype=bool
    ))

    employees = hires.drop_duplicates("employee_id", keep=False).set_index("employee_id")
    for table, field, minimum, maximum in (
        ("assessments", "assessed_on", 0, 30),
        ("performance", "reviewed_on", 180, 180),
    ):
        frame = tables[table]
        hire_dates = frame["employee_id"].map(employees["hired_on"]).fillna("").map(_date)
        exit_dates = frame["employee_id"].map(employees["exited_on"]).fillna("").map(_date)
        event_dates = frame[field].map(_date)
        records = list(zip(event_dates, hire_dates, exit_dates))
        flag(table, field, "outside_observation_window", pd.Series([
            day is not None and hire is not None and not minimum <= (day - hire).days <= maximum
            for day, hire, _ in records
        ], dtype=bool))
        flag(table, field, "after_exit", pd.Series([
            before(exit_day, day) for day, _, exit_day in records
        ], dtype=bool))
    return issues


def _visible(frame: pd.DataFrame, field: str, as_of: date) -> pd.DataFrame:
    return frame.loc[frame[field].le(as_of.isoformat())]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(scores: pd.Series) -> float | None:
    value = pd.to_numeric(scores).mean()
    return round(float(value), 2) if math.isfinite(value) else None


def _month_mature(month: str, as_of: date) -> bool:
    first = date.fromisoformat(f"{month}-01")
    last = first.replace(day=monthrange(first.year, first.month)[1])
    return (as_of - last).days >= 30


def channel_metrics(tables: dict[str, pd.DataFrame], as_of: date) -> dict:
    candidates = _visible(tables["candidates"], "applied_on", as_of)
    hires = _visible(tables["hires"], "hired_on", as_of)
    assessments = _visible(tables["assessments"], "assessed_on", as_of)
    performance = _visible(tables["performance"], "reviewed_on", as_of)
    employees = (
        hires.merge(candidates[["candidate_id", "channel"]], on="candidate_id", validate="one_to_one")
        .merge(assessments[["employee_id", "score"]].rename(columns={"score": "assessment_score"}),
               on="employee_id", how="left", validate="one_to_one")
        .merge(performance[["employee_id", "score"]].rename(columns={"score": "performance_score"}),
               on="employee_id", how="left", validate="one_to_one")
    )
    employees["mature_30d"] = employees["hired_on"].map(
        lambda hired: (as_of - date.fromisoformat(hired)).days >= 30
    ).astype(bool)
    employees["exited_30d"] = pd.Series([
        bool(exited and exited <= as_of.isoformat() and
             (date.fromisoformat(exited) - date.fromisoformat(hired)).days <= 30)
        for hired, exited in zip(employees["hired_on"], employees["exited_on"])
    ], index=employees.index, dtype=bool)
    employees["hire_month"] = employees["hired_on"].str[:7]

    channels = []
    by_channel = employees.groupby("channel")
    for channel, applications in candidates.groupby("channel", sort=True):
        cohort = by_channel.get_group(channel) if channel in by_channel.groups else employees.iloc[:0]
        mature = cohort.loc[cohort["mature_30d"]]
        retained = len(mature) - int(mature["exited_30d"].sum())
        channels.append({
            "channel": channel,
            "candidates": len(applications),
            "hires": len(cohort),
            "hire_rate": _ratio(len(cohort), len(applications)),
            "eligible_30d": len(mature),
            "retained_30d": retained,
            "retention_rate_30d": _ratio(retained, len(mature)),
            "assessment_count": int(cohort["assessment_score"].count()),
            "assessment_mean": _mean(cohort["assessment_score"]),
            "performance_180d_count": int(cohort["performance_score"].count()),
            "performance_180d_mean": _mean(cohort["performance_score"]),
        })

    monthly = []
    for (month, unit, channel), cohort in employees.groupby(["hire_month", "unit", "channel"]):
        mature = _month_mature(month, as_of)
        exits = int(cohort["exited_30d"].sum())
        monthly.append({
            "hire_month": month,
            "unit": unit,
            "channel": channel,
            "status": "mature" if mature else "observing",
            "hires": len(cohort),
            "exits_observed_30d": exits,
            "retention_rate_30d": _ratio(len(cohort) - exits, len(cohort)) if mature else None,
        })
    return {"channels": channels, "monthly_cohorts": monthly}
