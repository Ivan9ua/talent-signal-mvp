from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .analysis import channel_metrics, check_quality
from .storage import database_path, read_snapshot

app = FastAPI(title="Talent Signal MVP", description="招聘渠道人才质量分析 · 合成数据演示")
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
Database = Annotated[Path, Depends(database_path)]


def _load(db: Path):
    try:
        return read_snapshot(db)
    except FileNotFoundError as error:
        raise HTTPException(503, "请先导入数据快照") from error


def _report_data(db: Path, as_of: date | None):
    cutoff, tables = _load(db)
    as_of = as_of or cutoff
    if as_of > cutoff:
        raise HTTPException(422, "统计截止日不能晚于数据快照截止日")
    issues = check_quality(tables, cutoff)
    return {
        "snapshot_cutoff": cutoff,
        "as_of": as_of,
        "quality": {"status": "blocked" if issues else "passed", "issues": issues},
        "metrics": None if issues else channel_metrics(tables, as_of),
    }


@app.get("/api/data-quality")
def data_quality(db: Database):
    cutoff, tables = _load(db)
    issues = check_quality(tables, cutoff)
    return {
        "snapshot_cutoff": cutoff,
        "status": "blocked" if issues else "passed",
        "row_counts": {table: len(frame) for table, frame in tables.items()},
        "issues": issues,
    }


@app.get("/api/channels", responses={409: {"description": "数据质量检查未通过"}})
def channels(db: Database, as_of: date | None = None):
    report = _report_data(db, as_of)
    if report["quality"]["issues"]:
        raise HTTPException(409, detail=report["quality"])
    return {"as_of": report["as_of"], "snapshot_cutoff": report["snapshot_cutoff"], **report["metrics"]}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/report", response_class=HTMLResponse)
def report(request: Request, db: Database, as_of: date | None = None):
    data = _report_data(db, as_of)
    return templates.TemplateResponse(
        request=request, name="report.html", context=data,
        status_code=409 if data["quality"]["issues"] else 200,
    )
