import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

from bikeshare.pipeline import add_manual_trip, build_database, sync_manual_trips


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = PROJECT_ROOT / "data" / "sample_tripdata.csv"


class PipelineTests(unittest.TestCase):
    def test_builds_clean_table_and_excludes_invalid_and_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "trips.duckdb"
            counts = build_database([SAMPLE_CSV], database_path)

            self.assertEqual(counts, {
                "raw_rows": 10,
                "clean_rows": 5,
                "rejected_rows": 5,
            })
            with duckdb.connect(str(database_path), read_only=True) as connection:
                rows = connection.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT ride_id),
                           MIN(ride_length_min), MAX(ride_length_min)
                    FROM clean_trips
                    """
                ).fetchone()

            self.assertEqual(rows[0], 5)
            self.assertEqual(rows[1], 5)
            self.assertGreater(rows[2], 0)
            self.assertLessEqual(rows[3], 24 * 60)

    def test_fails_with_a_clear_error_when_required_columns_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "incomplete.csv"
            csv_path.write_text("ride_id,started_at\n1,2025-01-01\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required columns"):
                build_database([csv_path], Path(temp_dir) / "trips.duckdb")

    def test_fails_when_a_csv_path_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.csv"
            with self.assertRaisesRegex(FileNotFoundError, "CSV file not found"):
                build_database([missing_path], Path(temp_dir) / "trips.duckdb")

    def test_manual_trip_is_included_and_survives_database_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "trips.duckdb"
            build_database([SAMPLE_CSV], database_path)
            with duckdb.connect(str(database_path), read_only=True) as connection:
                initial_clean_count = connection.execute(
                    "SELECT COUNT(*) FROM clean_trips"
                ).fetchone()[0]

            add_manual_trip(
                database_path,
                "manual-trip-1",
                "classic_bike",
                datetime(2025, 2, 1, 9, 0),
                datetime(2025, 2, 1, 9, 20),
                "member",
                "Manual start",
                end_station_name="Manual end",
            )
            with duckdb.connect(str(database_path), read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM clean_trips"
                    ).fetchone()[0],
                    initial_clean_count + 1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT start_station_name FROM clean_trips "
                        "WHERE ride_id = 'manual-trip-1'"
                    ).fetchone()[0],
                    "Manual start",
                )

            with self.assertRaisesRegex(ValueError, "Ride ID already exists"):
                add_manual_trip(
                    database_path,
                    "manual-trip-1",
                    "classic_bike",
                    datetime(2025, 2, 1, 9, 0),
                    datetime(2025, 2, 1, 9, 20),
                    "member",
                )

            build_database([SAMPLE_CSV], database_path)
            with duckdb.connect(str(database_path), read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM clean_trips"
                    ).fetchone()[0],
                    initial_clean_count + 1,
                )

    def test_sync_manual_trips_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "trips.duckdb"
            build_database([SAMPLE_CSV], database_path)
            trip = {
                "ride_id": "persisted-trip-1",
                "rideable_type": "electric_bike",
                "started_at": datetime(2025, 3, 1, 8, 0),
                "ended_at": datetime(2025, 3, 1, 8, 15),
                "start_station_name": "Persistent start",
                "start_station_id": None,
                "end_station_name": "Persistent end",
                "end_station_id": None,
                "member_casual": "casual",
            }

            sync_manual_trips(database_path, [trip])
            sync_manual_trips(database_path, [trip])

            with duckdb.connect(str(database_path), read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM clean_trips "
                        "WHERE ride_id = 'persisted-trip-1'"
                    ).fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
