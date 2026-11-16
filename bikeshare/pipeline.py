"""Load Divvy CSV files into DuckDB and create a cleaned trips table."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

REQUIRED_COLUMNS = {
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
}

MANUAL_TRIP_COLUMNS = """
    ride_id VARCHAR,
    rideable_type VARCHAR,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    start_station_name VARCHAR,
    start_station_id VARCHAR,
    end_station_name VARCHAR,
    end_station_id VARCHAR,
    start_lat DOUBLE,
    start_lng DOUBLE,
    end_lat DOUBLE,
    end_lng DOUBLE,
    member_casual VARCHAR
"""


def _create_manual_trips_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS manual_trips ({MANUAL_TRIP_COLUMNS})"
    )


def _refresh_clean_trips(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TEMP VIEW all_trips AS
        SELECT * FROM raw_trips
        UNION ALL BY NAME
        SELECT * FROM manual_trips
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TABLE clean_trips AS
        WITH typed AS (
            SELECT
                NULLIF(TRIM(CAST(ride_id AS VARCHAR)), '') AS ride_id,
                CAST(rideable_type AS VARCHAR) AS rideable_type,
                TRY_CAST(started_at AS TIMESTAMP) AS started_at,
                TRY_CAST(ended_at AS TIMESTAMP) AS ended_at,
                CAST(start_station_name AS VARCHAR) AS start_station_name,
                CAST(start_station_id AS VARCHAR) AS start_station_id,
                CAST(end_station_name AS VARCHAR) AS end_station_name,
                CAST(end_station_id AS VARCHAR) AS end_station_id,
                TRY_CAST(start_lat AS DOUBLE) AS start_lat,
                TRY_CAST(start_lng AS DOUBLE) AS start_lng,
                TRY_CAST(end_lat AS DOUBLE) AS end_lat,
                TRY_CAST(end_lng AS DOUBLE) AS end_lng,
                LOWER(TRIM(CAST(member_casual AS VARCHAR))) AS member_casual
            FROM all_trips
        ),
        eligible AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY ride_id ORDER BY started_at, ended_at
                ) AS duplicate_number
            FROM typed
            WHERE ride_id IS NOT NULL
                AND started_at IS NOT NULL
                AND ended_at IS NOT NULL
                AND ended_at > started_at
                AND ended_at <= started_at + INTERVAL '24 hours'
                AND member_casual IN ('member', 'casual')
        )
        SELECT
            ride_id,
            rideable_type,
            started_at,
            ended_at,
            DATE_DIFF('second', started_at, ended_at) / 60.0 AS ride_length_min,
            start_station_name,
            start_station_id,
            end_station_name,
            end_station_id,
            start_lat,
            start_lng,
            end_lat,
            end_lng,
            member_casual,
            STRFTIME(started_at, '%Y-%m') AS ride_month,
            STRFTIME(started_at, '%A') AS start_day_name,
            CAST(STRFTIME(started_at, '%w') AS INTEGER) AS start_day_num,
            DATE_PART('hour', started_at)::INTEGER AS start_hour
        FROM eligible
        WHERE duplicate_number = 1
        """
    )


def build_database(csv_paths: list[Path], database_path: Path) -> dict[str, int]:
    """Rebuild raw and cleaned tables from CSV files and return row counts."""
    paths = [path.resolve() for path in csv_paths]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"CSV file not found: {missing[0]}")
    if not paths:
        raise ValueError("Provide at least one CSV file.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            """
            CREATE OR REPLACE TABLE raw_trips AS
            SELECT * FROM read_csv_auto(
                ?, header = true, union_by_name = true, sample_size = 10000
            )
            """,
            [list(map(str, paths))],
        )

        columns = {
            row[0].lower()
            for row in connection.execute("DESCRIBE raw_trips").fetchall()
        }
        missing_columns = sorted(REQUIRED_COLUMNS - columns)
        if missing_columns:
            raise ValueError(
                "Input CSV is missing required columns: "
                + ", ".join(missing_columns)
            )

        _create_manual_trips_table(connection)
        _refresh_clean_trips(connection)

        raw_count = connection.execute(
            "SELECT (SELECT COUNT(*) FROM raw_trips) + "
            "(SELECT COUNT(*) FROM manual_trips)"
        ).fetchone()[0]
        clean_count = connection.execute(
            "SELECT COUNT(*) FROM clean_trips"
        ).fetchone()[0]

    return {
        "raw_rows": raw_count,
        "clean_rows": clean_count,
        "rejected_rows": raw_count - clean_count,
    }


def add_manual_trip(
    database_path: Path,
    ride_id: str,
    rideable_type: str,
    started_at: datetime,
    ended_at: datetime,
    member_casual: str,
    start_station_name: str | None = None,
    start_station_id: str | None = None,
    end_station_name: str | None = None,
    end_station_id: str | None = None,
) -> None:
    """Save a validated manual trip and refresh the analytics table."""
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database file not found: {database_path}")

    ride_id = ride_id.strip()
    member_casual = member_casual.strip().lower()
    if not ride_id:
        raise ValueError("Ride ID is required.")
    if member_casual not in {"member", "casual"}:
        raise ValueError("Rider type must be member or casual.")
    if ended_at <= started_at:
        raise ValueError("End time must be later than start time.")
    if ended_at > started_at + timedelta(hours=24):
        raise ValueError("Trip duration cannot exceed 24 hours.")

    with duckdb.connect(str(database_path)) as connection:
        columns = {
            row[0].lower()
            for row in connection.execute("DESCRIBE raw_trips").fetchall()
        }
        missing_columns = sorted(REQUIRED_COLUMNS - columns)
        if missing_columns:
            raise ValueError(
                "Input database is missing required columns: "
                + ", ".join(missing_columns)
            )

        _create_manual_trips_table(connection)
        duplicate = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM raw_trips WHERE TRIM(CAST(ride_id AS VARCHAR)) = ?
                UNION ALL
                SELECT 1 FROM manual_trips WHERE ride_id = ?
            )
            """,
            [ride_id, ride_id],
        ).fetchone()[0]
        if duplicate:
            raise ValueError(f"Ride ID already exists: {ride_id}")

        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO manual_trips (
                    ride_id, rideable_type, started_at, ended_at,
                    start_station_name, start_station_id,
                    end_station_name, end_station_id,
                    member_casual
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ride_id,
                    rideable_type,
                    started_at,
                    ended_at,
                    start_station_name or None,
                    start_station_id or None,
                    end_station_name or None,
                    end_station_id or None,
                    member_casual,
                ],
            )
            _refresh_clean_trips(connection)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def sync_manual_trips(
    database_path: Path, trips: list[dict[str, object]]
) -> None:
    """Synchronize persisted manual trips into the local analytics database."""
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database file not found: {database_path}")

    with duckdb.connect(str(database_path)) as connection:
        _create_manual_trips_table(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            for trip in trips:
                ride_id = str(trip["ride_id"]).strip()
                duplicate_in_source = connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM raw_trips "
                    "WHERE TRIM(CAST(ride_id AS VARCHAR)) = ?)",
                    [ride_id],
                ).fetchone()[0]
                if duplicate_in_source:
                    raise ValueError(f"Ride ID already exists in CSV data: {ride_id}")

                connection.execute(
                    "DELETE FROM manual_trips WHERE ride_id = ?", [ride_id]
                )
                connection.execute(
                    """
                    INSERT INTO manual_trips (
                        ride_id, rideable_type, started_at, ended_at,
                        start_station_name, start_station_id,
                        end_station_name, end_station_id,
                        member_casual
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ride_id,
                        trip["rideable_type"],
                        trip["started_at"],
                        trip["ended_at"],
                        trip.get("start_station_name"),
                        trip.get("start_station_id"),
                        trip.get("end_station_name"),
                        trip.get("end_station_id"),
                        trip["member_casual"],
                    ],
                )
            _refresh_clean_trips(connection)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Divvy trip CSV files and build a DuckDB analytics table."
    )
    parser.add_argument(
        "--csv",
        nargs="+",
        required=True,
        type=Path,
        help="One or more Divvy trip CSV files.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/processed/bikeshare.duckdb"),
        help="DuckDB output path (default: data/processed/bikeshare.duckdb).",
    )
    args = parser.parse_args()

    counts = build_database(args.csv, args.db)
    print(
        f"Loaded {counts['raw_rows']:,} raw rows; "
        f"kept {counts['clean_rows']:,}; "
        f"rejected {counts['rejected_rows']:,}."
    )
    print(f"DuckDB database: {args.db.resolve()}")


if __name__ == "__main__":
    main()
