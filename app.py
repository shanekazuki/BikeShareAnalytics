"""Interactive Streamlit dashboard for the cleaned bike-share database."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import psycopg
import streamlit as st

from bikeshare.pipeline import add_manual_trip, build_database, sync_manual_trips
from bikeshare.storage import load_manual_trips, save_manual_trip

DEFAULT_DB = Path(__file__).parent / "data" / "processed" / "bikeshare.duckdb"
SAMPLE_CSV = Path(__file__).parent / "data" / "sample_tripdata.csv"

st.set_page_config(page_title="Bike-Share Trip Analytics", page_icon="🚲", layout="wide")
st.title("Chicago Bike-Share Trip Analytics")
st.caption("Explore trip patterns by rider type, time, and station.")

database_path = Path(st.sidebar.text_input("DuckDB database", str(DEFAULT_DB)))
if not database_path.is_file():
    if database_path.resolve() == DEFAULT_DB.resolve() and SAMPLE_CSV.is_file():
        try:
            build_database([SAMPLE_CSV], database_path)
        except (OSError, ValueError, duckdb.Error) as error:
            st.error(f"Could not initialize the sample database: {error}")
            st.stop()
    else:
        st.error(
            "Database not found. Run the pipeline first; see the README for instructions."
        )
        st.stop()


@st.cache_data
def load_options(db_path: str, modified_time: float) -> tuple[list[str], list[str]]:
    del modified_time
    with duckdb.connect(db_path, read_only=True) as connection:
        months = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT ride_month FROM clean_trips ORDER BY ride_month"
            ).fetchall()
        ]
        rider_types = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT member_casual FROM clean_trips ORDER BY member_casual"
            ).fetchall()
        ]
    return months, rider_types


@st.cache_data
def load_metrics(
    db_path: str, modified_time: float, months: tuple[str, ...], rider_types: tuple[str, ...]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    del modified_time
    conditions = []
    parameters: list[list[str]] = []
    if months:
        conditions.append("ride_month IN (SELECT UNNEST(?))")
        parameters.append(list(months))
    if rider_types:
        conditions.append("member_casual IN (SELECT UNNEST(?))")
        parameters.append(list(rider_types))
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    with duckdb.connect(db_path, read_only=True) as connection:
        summary = connection.execute(
            f"""
            SELECT COUNT(*) AS trips,
                   ROUND(AVG(ride_length_min), 1) AS avg_minutes,
                   COUNT(DISTINCT start_station_name) AS start_stations,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE member_casual = 'member')
                         / NULLIF(COUNT(*), 0), 1) AS member_share
            FROM clean_trips{where_clause}
            """,
            parameters,
        ).fetchdf()
        by_month = connection.execute(
            f"""
            SELECT ride_month, member_casual, COUNT(*) AS trips
            FROM clean_trips{where_clause}
            GROUP BY ride_month, member_casual ORDER BY ride_month
            """,
            parameters,
        ).fetchdf()
        by_hour = connection.execute(
            f"""
            SELECT start_hour, member_casual, COUNT(*) AS trips
            FROM clean_trips{where_clause}
            GROUP BY start_hour, member_casual ORDER BY start_hour
            """,
            parameters,
        ).fetchdf()
        by_weekday = connection.execute(
            f"""
            SELECT start_day_num, start_day_name, member_casual, COUNT(*) AS trips
            FROM clean_trips{where_clause}
            GROUP BY start_day_num, start_day_name, member_casual
            ORDER BY start_day_num
            """,
            parameters,
        ).fetchdf()
        station_where = where_clause or " WHERE TRUE"
        stations = connection.execute(
            """
            SELECT start_station_name, COUNT(*) AS trips
            FROM clean_trips"""
            + station_where
            + """
              AND start_station_name IS NOT NULL
            GROUP BY start_station_name ORDER BY trips DESC
            LIMIT 10
            """,
            parameters,
        ).fetchdf()

    return summary, by_month, by_hour, by_weekday, stations


try:
    postgres_dsn = st.secrets["postgres"]["dsn"]
except (KeyError, FileNotFoundError):
    postgres_dsn = None
    st.warning(
        "Persistent trip storage is not configured. Add the PostgreSQL DSN to "
        "Streamlit Secrets to enable manual trip entry."
    )

try:
    database_path = database_path.resolve(strict=True)
    if postgres_dsn:
        sync_manual_trips(database_path, load_manual_trips(postgres_dsn))
    db_mtime = database_path.stat().st_mtime
    month_options, rider_options = load_options(str(database_path), db_mtime)
except (OSError, ValueError, duckdb.Error, psycopg.Error) as error:
    st.error(f"Could not read the analytics database: {error}")
    st.stop()

selected_months = st.sidebar.multiselect("Month", month_options, default=month_options)
selected_riders = st.sidebar.multiselect(
    "Rider type", rider_options, default=rider_options
)

with st.expander("Add a trip manually"):
    st.caption(
        "Anyone with this app link can submit trips. Entries are stored in PostgreSQL."
    )
    with st.form("add_trip_form"):
        ride_id = st.text_input("Ride ID")
        rideable_type = st.selectbox(
            "Bike type", ["classic_bike", "electric_bike", "docked_bike"]
        )
        member_casual = st.selectbox("Rider type", ["member", "casual"])
        default_start = datetime.now().replace(second=0, microsecond=0)
        start_time = st.datetime_input("Started at", value=default_start)
        end_time = st.datetime_input(
            "Ended at", value=default_start + timedelta(minutes=15)
        )
        start_station_name = st.text_input("Start station (optional)")
        start_station_id = st.text_input("Start station ID (optional)")
        end_station_name = st.text_input("End station (optional)")
        end_station_id = st.text_input("End station ID (optional)")
        submitted = st.form_submit_button("Save trip", disabled=not postgres_dsn)

    if submitted:
        try:
            with duckdb.connect(str(database_path), read_only=True) as connection:
                exists_in_csv = connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM raw_trips "
                    "WHERE TRIM(CAST(ride_id AS VARCHAR)) = ?)",
                    [ride_id.strip()],
                ).fetchone()[0]
            if exists_in_csv:
                raise ValueError(f"Ride ID already exists: {ride_id.strip()}")
            save_manual_trip(
                postgres_dsn,
                ride_id,
                rideable_type,
                start_time,
                end_time,
                member_casual,
                start_station_name,
                start_station_id,
                end_station_name,
                end_station_id,
            )
        except (ValueError, OSError, duckdb.Error, psycopg.Error) as error:
            st.error(f"Could not persist trip: {error}")
        else:
            try:
                add_manual_trip(
                    database_path,
                    ride_id,
                    rideable_type,
                    start_time,
                    end_time,
                    member_casual,
                    start_station_name,
                    start_station_id,
                    end_station_name,
                    end_station_id,
                )
            except (ValueError, OSError, duckdb.Error) as error:
                st.warning(
                    "The trip was saved to PostgreSQL, but the local analytics "
                    f"could not refresh: {error}. Reload the app to sync it."
                )
            else:
                st.cache_data.clear()
                st.success("Trip saved and analytics updated.")
                st.rerun()

try:
    summary, by_month, by_hour, by_weekday, stations = load_metrics(
        str(database_path),
        db_mtime,
        tuple(selected_months),
        tuple(selected_riders),
    )
except duckdb.Error as error:
    st.error(f"Could not load dashboard metrics: {error}")
    st.stop()

metrics = summary.iloc[0]
col1, col2, col3, col4 = st.columns(4)
col1.metric("Trips", f"{int(metrics['trips']):,}")
average_minutes = 0 if pd.isna(metrics["avg_minutes"]) else metrics["avg_minutes"]
member_share = 0 if pd.isna(metrics["member_share"]) else metrics["member_share"]
col2.metric("Average trip", f"{average_minutes:.1f} min")
col3.metric("Start stations", f"{int(metrics['start_stations']):,}")
col4.metric("Member share", f"{member_share:.1f}%")

left, right = st.columns(2)
with left:
    st.subheader("Trips by month")
    st.plotly_chart(
        px.bar(
            by_month,
            x="ride_month",
            y="trips",
            color="member_casual",
            barmode="group",
            labels={"ride_month": "Month", "trips": "Trips", "member_casual": "Rider"},
        ),
        use_container_width=True,
    )
with right:
    st.subheader("Trips by starting hour")
    st.plotly_chart(
        px.line(
            by_hour,
            x="start_hour",
            y="trips",
            color="member_casual",
            markers=True,
            labels={"start_hour": "Hour of day", "trips": "Trips", "member_casual": "Rider"},
        ),
        use_container_width=True,
    )

st.subheader("Trips by weekday")
st.plotly_chart(
    px.bar(
        by_weekday,
        x="start_day_name",
        y="trips",
        color="member_casual",
        barmode="group",
        category_orders={
            "start_day_name": [
                "Sunday",
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
            ]
        },
        labels={
            "start_day_name": "Weekday",
            "trips": "Trips",
            "member_casual": "Rider",
        },
    ),
    use_container_width=True,
)

st.subheader("Most popular start stations")
st.dataframe(stations, use_container_width=True, hide_index=True)
