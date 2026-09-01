"""
Reusable data-cleaning functions for the EV Charging platform.

Each function accepts a pandas DataFrame loaded from the corresponding
database table and returns a cleaned copy.  Cleaning decisions (dropped
rows, flagged anomalies) are logged to stdout so the calling script can
surface them in its summary.

These functions are **not** meant to be run standalone — they are
imported by ``build_features.py`` and any future pipeline scripts.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def clean_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the ``charging_sessions`` table.

    * Drop rows where ``end_time < start_time``
    * Drop rows where ``energy_delivered_kwh < 0``
    * Cast timestamp columns to datetime
    * Flag / fill missing ``soc_start`` / ``soc_end``
    """
    original_len = len(df)
    log_lines: list[str] = []

    # -- Timestamp casting ---------------------------------------------------
    for col in ("start_time", "end_time", "created_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # -- Drop rows where end_time < start_time ------------------------------
    mask_bad_time = df["end_time"].notna() & df["start_time"].notna() & (
        df["end_time"] < df["start_time"]
    )
    n_bad_time = mask_bad_time.sum()
    if n_bad_time:
        log_lines.append(f"  Dropped {n_bad_time} session(s): end_time < start_time")
    df = df[~mask_bad_time].copy()

    # -- Drop rows where energy_delivered_kwh < 0 ---------------------------
    mask_neg_energy = df["energy_delivered_kwh"].notna() & (
        df["energy_delivered_kwh"] < 0
    )
    n_neg_energy = mask_neg_energy.sum()
    if n_neg_energy:
        log_lines.append(f"  Dropped {n_neg_energy} session(s): negative energy_delivered_kwh")
    df = df[~mask_neg_energy].copy()

    # -- SOC handling --------------------------------------------------------
    # Flag rows with missing soc values (don't fabricate data)
    n_soc_start_null = df["soc_start"].isna().sum()
    n_soc_end_null = df["soc_end"].isna().sum()
    if n_soc_start_null:
        log_lines.append(f"  Flagged {n_soc_start_null} session(s): soc_start is null")
    if n_soc_end_null:
        log_lines.append(f"  Flagged {n_soc_end_null} session(s): soc_end is null")

    # Fill missing SOC with NaN (keep them, don't drop — downstream can decide)
    # Already NaN from the database NULL, so no action needed.

    dropped = original_len - len(df)
    log_lines.insert(0, f"[clean_sessions] {original_len} rows -> {len(df)} rows ({dropped} dropped)")
    for line in log_lines:
        logger.info(line)
        print(line)

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def clean_reviews(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the ``reviews`` table.

    * Drop rows with ``rating`` outside 1-5
    * Drop rows with null ``review_text``
    * Strip and normalise whitespace in ``review_text``
    """
    original_len = len(df)
    log_lines: list[str] = []

    # -- Cast created_at -----------------------------------------------------
    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")

    # -- Drop rows with rating outside 1-5 ----------------------------------
    mask_bad_rating = ~df["rating"].between(1, 5)
    n_bad_rating = mask_bad_rating.sum()
    if n_bad_rating:
        log_lines.append(f"  Dropped {n_bad_rating} review(s): rating outside 1-5")
    df = df[~mask_bad_rating].copy()

    # -- Drop rows with null review_text ------------------------------------
    mask_null_text = df["review_text"].isna() | (df["review_text"].str.strip() == "")
    n_null_text = mask_null_text.sum()
    if n_null_text:
        log_lines.append(f"  Dropped {n_null_text} review(s): null or empty review_text")
    df = df[~mask_null_text].copy()

    # -- Normalise whitespace -----------------------------------------------
    df["review_text"] = df["review_text"].str.strip().str.replace(r"\s+", " ", regex=True)

    dropped = original_len - len(df)
    log_lines.insert(0, f"[clean_reviews] {original_len} rows -> {len(df)} rows ({dropped} dropped)")
    for line in log_lines:
        logger.info(line)
        print(line)

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------

def clean_faults(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the ``faults`` table.

    * Cast ``reported_at`` / ``resolved_at`` to datetime
    * Flag rows where ``resolved=True`` but ``resolved_at`` is null
      (don't guess a date — just add a boolean flag column)
    """
    original_len = len(df)
    log_lines: list[str] = []

    # -- Timestamp casting ---------------------------------------------------
    for col in ("reported_at", "resolved_at", "created_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # -- Flag resolved=True with null resolved_at ---------------------------
    mask_missing_resolve_date = df["resolved"] & df["resolved_at"].isna()
    n_flagged = mask_missing_resolve_date.sum()
    df["resolved_date_missing"] = mask_missing_resolve_date
    if n_flagged:
        log_lines.append(
            f"  Flagged {n_flagged} fault(s): resolved=True but resolved_at is null"
        )

    log_lines.insert(0, f"[clean_faults] {original_len} rows -> {len(df)} rows (0 dropped, {n_flagged} flagged)")
    for line in log_lines:
        logger.info(line)
        print(line)

    return df.reset_index(drop=True)
