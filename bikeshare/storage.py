"""Persistent storage for manual trips entered in the dashboard."""

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _ensure_manual_trips_table(connection: psycopg.Connection[Any]) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bikeshare_manual_trips (
            ride_id TEXT PRIMARY KEY CHECK (length(trim(ride_id)) > 0),
            rideable_type TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP NOT NULL,
            start_station_name TEXT,
            start_station_id TEXT,
            end_station_name TEXT,
            end_station_id TEXT,
            member_casual TEXT NOT NULL
                CHECK (member_casual IN ('member', 'casual')),
            CHECK (
                ended_at > started_at
                AND ended_at <= started_at + INTERVAL '24 hours'
            )
        )
        """
    )


def load_manual_trips(dsn: str) -> list[dict[str, object]]:
    """Return all persisted manual trips and create the table if needed."""
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        _ensure_manual_trips_table(connection)
        rows = connection.execute(
            """
            SELECT ride_id, rideable_type, started_at, ended_at,
                   start_station_name, start_station_id,
                   end_station_name, end_station_id, member_casual
            FROM bikeshare_manual_trips
            ORDER BY ride_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_manual_trip(
    dsn: str,
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
    """Persist one manual trip, raising a clear error for duplicate IDs."""
    try:
        with psycopg.connect(dsn) as connection:
            _ensure_manual_trips_table(connection)
            connection.execute(
                """
                INSERT INTO bikeshare_manual_trips (
                    ride_id, rideable_type, started_at, ended_at,
                    start_station_name, start_station_id,
                    end_station_name, end_station_id, member_casual
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ride_id.strip(),
                    rideable_type,
                    started_at,
                    ended_at,
                    start_station_name or None,
                    start_station_id or None,
                    end_station_name or None,
                    end_station_id or None,
                    member_casual.strip().lower(),
                ),
            )
    except psycopg.errors.UniqueViolation as error:
        raise ValueError(f"Ride ID already exists: {ride_id}") from error
