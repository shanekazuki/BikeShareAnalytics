# Bike-Share Trip Analytics

An end-to-end data engineering and analytics portfolio project using Chicago Divvy trip data. The project loads CSV trip files into DuckDB, validates and transforms them into an analysis-ready table, and presents the results in a Streamlit dashboard.

## What this project demonstrates

- Batch ingestion of one or more monthly CSV files
- Schema and data-quality checks with rejected records excluded from analytics
- SQL transformations and deduplication in DuckDB
- Interactive analysis of rider type, trip duration, time patterns, and popular stations
- A small synthetic sample so the pipeline and dashboard can be tried without downloading data

## Requirements

- Python 3.10 or newer
- The packages in `requirements.txt`

## Run the demo

From this directory, in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m bikeshare.pipeline --csv data\sample_tripdata.csv --db data\processed\bikeshare.duckdb
streamlit run app.py
```

The sample records are synthetic and are only for demonstrating the workflow. They do not represent actual Divvy usage.

After starting the dashboard, expand **Add a trip manually** to enter a trip. Saved trips are included in the dashboard and stored separately from the local DuckDB analytics file. Anyone who can access a public deployment can submit trips.

### Configure persistent manual-trip storage

The app uses a Supabase PostgreSQL database for manually entered trips. Create a Supabase project, then add its PostgreSQL connection URI to Streamlit Secrets:

```toml
[postgres]
dsn = "postgresql://..."
```

In Streamlit Community Cloud, add this under **App settings → Secrets**. For local development, put it in `.streamlit\secrets.toml` (this file is ignored by Git). The app creates its manual-trip table on first start and syncs those records into DuckDB for analytics. Do not commit the connection URI.

Run the automated pipeline tests with:

```powershell
python -m unittest discover -s tests -v
```

## Use real data

1. Download one or more monthly Divvy trip-data CSV files from the [Divvy trip data page](https://divvy-tripdata.s3.amazonaws.com/index.html) and extract them into a local folder, for example `data\raw`.
2. Run the pipeline, passing all the monthly CSV paths:

   ```powershell
   python -m bikeshare.pipeline --csv data\raw\202501-divvy-tripdata.csv data\raw\202502-divvy-tripdata.csv --db data\processed\bikeshare.duckdb
   ```

   Repeat with all months you want to analyze. Each run rebuilds the raw and clean tables from the CSV files supplied to that run.
3. Start the dashboard:

   ```powershell
   streamlit run app.py
   ```

The pipeline expects the current Divvy schema: `ride_id`, `rideable_type`, `started_at`, `ended_at`, `start_station_name`, `start_station_id`, `end_station_name`, `end_station_id`, `start_lat`, `start_lng`, `end_lat`, `end_lng`, and `member_casual`. Monthly files with different schemas must be normalized before loading.

## Data quality rules

- Keep the first row for each non-null `ride_id`.
- Require valid start/end timestamps and a positive trip duration no longer than 24 hours.
- Keep only `member` and `casual` rider types.
- Allow missing station names, station IDs, and coordinates; these do not invalidate a trip.
- Keep the source tables in DuckDB and report raw, clean, and rejected row counts after each run.

The 24-hour limit is a project assumption to exclude likely bad records, not a claim that longer rides are impossible. Document changes to this rule when comparing results.

## Project layout

```text
app.py                       Streamlit dashboard
bikeshare/
  __init__.py
  pipeline.py                CSV ingestion, validation, and SQL transformation
  storage.py                 Persistent manual-trip storage
 data/
  sample_tripdata.csv        Synthetic demo data
  processed/                 Generated DuckDB database (ignored by Git)
tests/
  test_pipeline.py
requirements.txt
```

## Suggested analysis

- Compare member and casual ride counts and average ride duration.
- Find peak trip hours and weekdays for each rider type.
- Track trip volume by month.
- Rank start stations, while excluding unknown station names.

Use the source provider's terms and attribution requirements when redistributing data or publishing results. Do not commit full monthly source files or the generated database.
