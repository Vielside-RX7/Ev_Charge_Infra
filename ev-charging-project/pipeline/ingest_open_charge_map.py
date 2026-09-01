"""
Open Charge Map data ingestion script for Karnataka state.

Fetches EV charger POI data from the Open Charge Map API (using bounding-box
or radial search) and upserts it into the ``chargers`` table. Keyed on
``external_id`` so re-running the script updates existing rows in-place and
never creates duplicate records.

Usage:
    python pipeline/ingest_open_charge_map.py

Environment variables (via .env):
    DATABASE_URL        - required (PostgreSQL connection string)
    OCM_API_KEY         - required for OCM API v3 (warns if missing)
    INGEST_MODE         - 'bounding_box' (default) or 'radius'
    BBOX_NORTH          - default 18.45 (Karnataka North)
    BBOX_SOUTH          - default 11.5  (Karnataka South)
    BBOX_WEST           - default 74.0  (Karnataka West)
    BBOX_EAST           - default 78.6  (Karnataka East)
    MAX_TOTAL_RESULTS   - default 10000 (Safety cap)
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Resolve project root so we can import database.models regardless of cwd
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print(
        "[ERROR] DATABASE_URL is not set.\n"
        "  1. Copy .env.example to .env:  cp .env.example .env\n"
        "  2. Start PostgreSQL:           docker compose up -d\n"
        "  3. Initialise the DB:          python database/init_db.py\n"
        "  4. Re-run this script:         python pipeline/ingest_open_charge_map.py",
        file=sys.stderr,
    )
    sys.exit(1)

OCM_API_KEY = os.getenv("OCM_API_KEY", "").strip()
INGEST_MODE = os.getenv("INGEST_MODE", "bounding_box").strip().lower()

# Karnataka approximate bounding box: north=18.45, south=11.5, west=74.0, east=78.6
BBOX_NORTH = float(os.getenv("BBOX_NORTH", "18.45"))
BBOX_SOUTH = float(os.getenv("BBOX_SOUTH", "11.5"))
BBOX_WEST = float(os.getenv("BBOX_WEST", "74.0"))
BBOX_EAST = float(os.getenv("BBOX_EAST", "78.6"))

# Radial fallback config
CITY_LAT = float(os.getenv("CITY_LAT", "12.2958"))
CITY_LON = float(os.getenv("CITY_LON", "76.6394"))
SEARCH_RADIUS_KM = int(os.getenv("SEARCH_RADIUS_KM", "25"))

MAX_TOTAL_RESULTS = int(os.getenv("MAX_TOTAL_RESULTS", "10000"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "1000"))

OCM_API_URL = "https://api.openchargemap.io/v3/poi/"

# ---------------------------------------------------------------------------
# Import ORM model
# ---------------------------------------------------------------------------
from models import Base, Charger  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(obj: dict | None, *keys, default=None):
    """Safely traverse nested dicts/lists."""
    current = obj
    for key in keys:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and key < len(current):
            current = current[key]
        else:
            return default
    return current if current is not None else default


def fetch_pois() -> list[dict]:
    """
    Call the Open Charge Map API and return the raw JSON list of POIs.
    Supports Karnataka state bounding-box mode and radial search mode.
    """
    if not OCM_API_KEY:
        print(
            "\n" + "!" * 78 + "\n"
            "[WARNING] OCM_API_KEY is not set in .env!\n"
            "Open Charge Map API v3 requires an API key. Requests without a key\n"
            "will be rate-limited or rejected with HTTP 403 Forbidden.\n"
            "!" * 78 + "\n",
            file=sys.stderr,
        )

    headers = {"X-API-Key": OCM_API_KEY} if OCM_API_KEY else {}

    params: dict = {
        "output": "json",
        "countrycode": "IN",
        "compact": "false",
        "verbose": "false",
        "maxresults": min(PAGE_SIZE, MAX_TOTAL_RESULTS),
    }
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    if INGEST_MODE == "bounding_box":
        # Format: (south,west),(north,east)
        bbox_str = f"({BBOX_SOUTH},{BBOX_WEST}),({BBOX_NORTH},{BBOX_EAST})"
        params["boundingbox"] = bbox_str
        print(
            f"Fetching chargers from Open Charge Map (Bounding Box Mode) ...\n"
            f"  Region       : Karnataka State Extents\n"
            f"  Bounding Box : {bbox_str} (N={BBOX_NORTH}, S={BBOX_SOUTH}, W={BBOX_WEST}, E={BBOX_EAST})\n"
            f"  Max Results  : {MAX_TOTAL_RESULTS}\n"
        )
    else:
        params["latitude"] = CITY_LAT
        params["longitude"] = CITY_LON
        params["distance"] = SEARCH_RADIUS_KM
        params["distanceunit"] = "KM"
        print(
            f"Fetching chargers from Open Charge Map (Radius Mode) ...\n"
            f"  Location     : ({CITY_LAT}, {CITY_LON})\n"
            f"  Radius       : {SEARCH_RADIUS_KM} km\n"
            f"  Max Results  : {MAX_TOTAL_RESULTS}\n"
        )

    all_pois: list[dict] = []
    page = 1

    print("[Ingestion Progress] Requesting POIs from OCM API...")
    start_time = time.time()

    resp = requests.get(OCM_API_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        print(f"[ERROR] Unexpected API response format: {type(data)}", file=sys.stderr)
        sys.exit(1)

    all_pois.extend(data)
    print(f"  [Progress] Batch {page}: Retrieved {len(data)} POIs (Total so far: {len(all_pois)})")

    # If the response reached PAGE_SIZE and total is under MAX_TOTAL_RESULTS, attempt next batch
    if len(data) >= PAGE_SIZE and len(all_pois) < MAX_TOTAL_RESULTS:
        # Request full dataset up to safety cap
        params["maxresults"] = MAX_TOTAL_RESULTS
        resp = requests.get(OCM_API_URL, params=params, headers=headers, timeout=45)
        if resp.status_code == 200:
            full_data = resp.json()
            if isinstance(full_data, list) and len(full_data) > len(all_pois):
                print(f"  [Progress] Expanded fetch: Retrieved full set of {len(full_data)} POIs")
                all_pois = full_data[:MAX_TOTAL_RESULTS]

    elapsed = time.time() - start_time
    print(f"\n[Ingestion Progress] Completed API retrieval in {elapsed:.2f}s. Total POIs received: {len(all_pois)}\n")
    return all_pois


def parse_poi(poi: dict) -> dict | None:
    """
    Map a single OCM POI dict to a dict of ``Charger`` column values.
    Returns ``None`` if the POI is missing mandatory fields (lat/lon).
    """
    addr = poi.get("AddressInfo") or {}
    lat = addr.get("Latitude")
    lon = addr.get("Longitude")

    if lat is None or lon is None:
        return None

    # Operator
    operator = _safe_get(poi, "OperatorInfo", "Title")

    # First connection entry
    connections = poi.get("Connections") or []
    first_conn = connections[0] if connections else {}
    connector_type = _safe_get(first_conn, "ConnectionType", "Title")
    power_kw = first_conn.get("PowerKW") if first_conn else None

    # Number of ports
    num_points = poi.get("NumberOfPoints")
    num_ports = num_points if num_points else (len(connections) if connections else None)

    town = addr.get("Town") or addr.get("City")
    state = addr.get("StateOrProvince")

    return {
        "external_id": str(poi.get("ID", "")),
        "name": addr.get("Title", "Unknown Charger"),
        "operator": str(operator) if operator else None,
        "address": addr.get("AddressLine1"),
        "city": str(town).strip() if town else None,
        "state": str(state).strip() if state else None,
        "latitude": float(lat),
        "longitude": float(lon),
        "connector_type": str(connector_type).strip() if connector_type else None,
        "charging_power_kw": float(power_kw) if power_kw is not None else None,
        "num_ports": int(num_ports) if num_ports is not None else None,
        "source": "open_charge_map",
    }


def upsert_chargers(pois: list[dict]) -> tuple[int, int, int]:
    """
    Parse POIs and upsert into the ``chargers`` table.
    Returns (inserted, updated, skipped) counts.
    """
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(bind=engine)  # ensure tables exist

    inserted = 0
    updated = 0
    skipped = 0

    print(f"[DB Upsert] Processing {len(pois)} POIs into PostgreSQL...")

    with Session(engine) as session:
        for idx, poi in enumerate(pois, 1):
            parsed = parse_poi(poi)
            if parsed is None:
                skipped += 1
                continue

            ext_id = parsed["external_id"]

            # Check for existing row by external_id
            existing = (
                session.query(Charger)
                .filter(Charger.external_id == ext_id)
                .first()
            )

            if existing:
                # Update all fields except id and created_at
                for key, value in parsed.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                session.add(Charger(**parsed))
                inserted += 1

            if idx % 100 == 0 or idx == len(pois):
                print(f"  [DB Progress] Processed {idx}/{len(pois)} records (Inserted: {inserted}, Updated: {updated}, Skipped: {skipped})")

        session.commit()

    engine.dispose()
    return inserted, updated, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    start_total = time.time()

    pois = fetch_pois()

    if not pois:
        print("No POIs returned by the API. Nothing to ingest.")
        return

    inserted, updated, skipped = upsert_chargers(pois)
    total_time = time.time() - start_total

    print("\n" + "=" * 50)
    print("      KARNATAKA INGESTION SUMMARY")
    print("=" * 50)
    print(f"  Total POIs fetched : {len(pois)}")
    print(f"  Inserted (new)     : {inserted}")
    print(f"  Updated (existing) : {updated}")
    print(f"  Skipped (invalid)  : {skipped}")
    print(f"  Total DB rows      : {inserted + updated}")
    print(f"  Total Elapsed Time : {total_time:.2f} seconds")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"[ERROR] API request failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Ingestion failed: {exc}", file=sys.stderr)
        sys.exit(1)

