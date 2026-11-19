"""Import one complete CSV snapshot and read it consistently from SQLite."""

import argparse
import csv
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3

import pandas as pd

COLUMNS = {
    "candidates": ("candidate_id", "channel", "applied_on"),
    "hires": ("employee_id", "candidate_id", "unit", "hired_on", "exited_on"),
    "assessments": ("employee_id", "assessed_on", "score"),
    "performance": ("employee_id", "reviewed_on", "score"),
}


def database_path() -> Path:
    return Path(os.environ.get("TALENT_SIGNAL_DB", "var/talent_signal.sqlite3"))


def import_snapshot(directory: Path, db: Path, cutoff: date) -> None:
    tables = {}
    for table, columns in COLUMNS.items():
        path = directory / f"{table}.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file, strict=True)
            if reader.fieldnames != list(columns):
                raise ValueError(f"{path.name} 的列应为 {', '.join(columns)}")
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"{path.name} 第 {reader.line_num} 行列数不符")
                rows.append(tuple(row[column].strip() for column in columns))
            tables[table] = rows

    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, autocommit=False)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS snapshot (cutoff TEXT NOT NULL)")
        connection.execute("DELETE FROM snapshot")
        connection.execute("INSERT INTO snapshot VALUES (?)", (cutoff.isoformat(),))
        for table, columns in COLUMNS.items():
            schema = ", ".join(f"{column} TEXT NOT NULL" for column in columns)
            fields = ", ".join(columns)
            placeholders = ", ".join("?" for column in columns)
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} ({schema})")
            connection.execute(f"DELETE FROM {table}")
            connection.executemany(
                f"INSERT INTO {table} ({fields}) VALUES ({placeholders})", tables[table]
            )


def read_snapshot(db: Path) -> tuple[date, dict[str, pd.DataFrame]]:
    if not db.is_file():
        raise FileNotFoundError(db)
    with closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        metadata = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'snapshot'"
        ).fetchone()
        if metadata is None:
            raise FileNotFoundError(db)
        cutoff, = connection.execute("SELECT cutoff FROM snapshot").fetchone()
        tables = {
            table: pd.read_sql_query(f"SELECT * FROM {table} ORDER BY rowid", connection)
            for table in COLUMNS
        }
    return date.fromisoformat(cutoff), tables


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入完整的 CSV 数据快照")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    try:
        import_snapshot(args.directory, database_path(), args.as_of)
    except (OSError, ValueError, csv.Error, sqlite3.Error) as error:
        parser.exit(1, f"导入失败：{error}\n")
    print(f"已导入截至 {args.as_of} 的数据快照")
