"""
Synthetic data generator for the EV Charging Intelligence platform.

Creates realistic fake users, charging sessions, reviews, and fault
reports seeded by real charger rows from Phase 2.  Each charger is
assigned a hidden ``true_reliability`` value (NOT stored in the DB)
that biases the generated data so downstream ML models have a
learnable signal for validation.

Usage:
    python pipeline/generate_synthetic_data.py            # generate
    python pipeline/generate_synthetic_data.py --reset    # wipe & regenerate
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Project paths & env
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[ERROR] DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(1)

from models import (  # noqa: E402
    Base,
    Charger,
    ChargingSession,
    Fault,
    FaultSource,
    Review,
    SessionSource,
    SessionStatus,
    User,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
NUM_USERS = 60
SESSIONS_PER_CHARGER = (30, 150)
REVIEWS_PER_CHARGER = (5, 20)
HISTORY_DAYS = 180  # 6 months

EV_MODELS = [
    {"model": "Tata Nexon EV Max",    "capacity": 40.5, "connector": "CCS2"},
    {"model": "Tata Nexon EV",        "capacity": 30.2, "connector": "CCS2"},
    {"model": "MG ZS EV",            "capacity": 50.3, "connector": "CCS2"},
    {"model": "Hyundai Kona Electric","capacity": 39.2, "connector": "CCS2"},
    {"model": "Tata Tiago EV",        "capacity": 24.0, "connector": "Type 2"},
    {"model": "BYD Atto 3",          "capacity": 60.5, "connector": "CCS2"},
    {"model": "Mahindra XUV400",     "capacity": 39.4, "connector": "CCS2"},
    {"model": "Citroen eC3",         "capacity": 29.2, "connector": "Type 2"},
    {"model": "Tata Punch EV",       "capacity": 35.0, "connector": "CCS2"},
    {"model": "MG Comet EV",         "capacity": 17.3, "connector": "Type 2"},
]

FIRST_NAMES = [
    "Aarav", "Aditi", "Amit", "Ananya", "Arjun", "Deepa", "Divya", "Gaurav",
    "Isha", "Karthik", "Kavya", "Lakshmi", "Manish", "Meera", "Neha", "Nikhil",
    "Pooja", "Priya", "Rahul", "Rajesh", "Ravi", "Rekha", "Rohan", "Sakshi",
    "Sandeep", "Shreya", "Siddharth", "Sneha", "Suresh", "Tanvi", "Varun",
    "Vikram", "Vinay", "Anjali", "Bhavna", "Chetan", "Darshan", "Esha",
    "Farhan", "Geeta", "Harish", "Indira", "Jayesh", "Keerthi", "Lavanya",
    "Mohan", "Nandini", "Omkar", "Pallavi", "Ramesh",
]

LAST_NAMES = [
    "Sharma", "Verma", "Patel", "Kumar", "Singh", "Reddy", "Rao", "Nair",
    "Iyer", "Gupta", "Joshi", "Desai", "Mehta", "Chopra", "Bhat", "Hegde",
    "Kulkarni", "Patil", "Menon", "Pillai", "Das", "Ghosh", "Mukherjee",
    "Srinivasan", "Agarwal",
]

FAULT_TYPES = [
    "Connector fault",
    "Payment failure",
    "Power outage",
    "Display malfunction",
    "Communication error",
    "Ground fault",
    "Overheating",
    "Cable damage",
]

FAULT_DESCRIPTIONS = {
    "Connector fault":      "Connector not locking properly / unable to initiate session.",
    "Payment failure":      "Card reader unresponsive or payment gateway timeout.",
    "Power outage":         "Charger offline due to local power supply interruption.",
    "Display malfunction":  "Touchscreen frozen or showing incorrect information.",
    "Communication error":  "Network / OCPP back-end communication lost.",
    "Ground fault":         "Ground fault detected; charger tripped safety relay.",
    "Overheating":          "Thermal shutdown triggered during high-power session.",
    "Cable damage":         "Visible cable wear or insulation damage reported by user.",
}

# Review templates — indexed by sentiment bucket
POSITIVE_REVIEWS = [
    "Great charging experience! Fast and reliable.",
    "Charger worked perfectly, fully charged in under an hour.",
    "Very convenient location and the charger was available immediately.",
    "Smooth session, no issues at all. Will come back.",
    "One of the better chargers in the area. Consistent performance.",
    "Quick top-up on my way to work. No complaints.",
    "Reliable charger, have used it multiple times without any problem.",
    "Good speed and the payment went through smoothly.",
    "Excellent uptime. Always available when I need it.",
    "Clean station, well-maintained. Charged my Nexon in 45 minutes.",
]

NEUTRAL_REVIEWS = [
    "Charger works but the speed is a bit slow for DC.",
    "Decent experience. Had to wait about 10 minutes for availability.",
    "It works, but the display is hard to read in sunlight.",
    "Average charging speed. Location is okay.",
    "Not bad, but I've seen faster chargers in the city.",
    "Functional but could use better signage to find it.",
    "Charging was fine but parking area is tight.",
    "Okay experience. Took longer than expected.",
]

NEGATIVE_REVIEWS = [
    "Charger was not working when I arrived. Wasted my trip.",
    "Session failed midway. Had to find another charger.",
    "Very unreliable. Third time it has failed on me.",
    "Waited 30 minutes and the charger kept showing errors.",
    "Payment failed twice before it finally started. Frustrating.",
    "Extremely slow charging speed, much lower than advertised.",
    "Charger was offline. No maintenance notice posted.",
    "Connector was damaged and wouldn't lock into my car.",
    "Terrible experience. The charger stopped after 5 minutes.",
    "Avoid this charger. It rarely works properly.",
]

# Time-of-day weights (index = hour 0..23)
WEEKDAY_HOUR_WEIGHTS = [
    1, 1, 1, 1, 1, 1,           # 00-05: overnight (very low)
    3, 8, 12, 12, 8,            # 06-10: morning commute peak (7-10am)
    3, 3, 3, 3, 3,              # 11-15: midday baseline
    5, 7, 14, 14, 12, 9,        # 16-21: evening commute peak (6-9pm)
    3, 2,                       # 22-23: late night
]

WEEKEND_HOUR_WEIGHTS = [
    1, 1, 1, 1, 1, 1,           # 00-05: overnight (very low)
    1, 2, 3, 5, 7,              # 06-10: slow morning
    11, 12, 12, 11, 11, 10, 9, 8,# 11-18: midday/afternoon peak (11am-6pm)
    7, 6, 5,                    # 19-21: smaller evening bump
    2, 1,                       # 22-23: late night
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assign_true_reliability(chargers: list[Charger]) -> dict[int, float]:
    """
    Assign each charger a hidden true_reliability in [0.55, 0.98].
    Uses a Beta(5, 2) distribution — skewed toward the higher end so
    most chargers are reasonably reliable, with a meaningful minority poor.
    """
    mapping = {}
    for c in chargers:
        raw = random.betavariate(5, 2)            # mostly 0.6-1.0
        reliability = 0.55 + raw * (0.98 - 0.55)  # scale to [0.55, 0.98]
        mapping[c.id] = round(reliability, 4)
    return mapping


def random_timestamp_in_window() -> datetime:
    """Return a random timestamp in the last HISTORY_DAYS, weighted
    toward weekday evenings and weekends."""
    days_ago = random.randint(0, HISTORY_DAYS - 1)
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)

    dow = day.weekday()  # 0=Mon .. 6=Sun
    weights = WEEKEND_HOUR_WEIGHTS if dow >= 5 else WEEKDAY_HOUR_WEIGHTS
    hour = random.choices(range(24), weights=weights, k=1)[0]
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return day.replace(hour=hour, minute=minute, second=second)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_users(session: Session) -> list[User]:
    """Create NUM_USERS fake users and return them (already flushed)."""
    users = []
    used_emails: set[str] = set()

    for _ in range(NUM_USERS):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        name = f"{first} {last}"

        # Unique email
        base_email = f"{first.lower()}.{last.lower()}"
        email = f"{base_email}@example.com"
        suffix = 1
        while email in used_emails:
            email = f"{base_email}{suffix}@example.com"
            suffix += 1
        used_emails.add(email)

        ev = random.choice(EV_MODELS)

        user = User(
            name=name,
            email=email,
            vehicle_model=ev["model"],
            battery_capacity_kwh=ev["capacity"],
            preferred_connector_type=ev["connector"],
        )
        session.add(user)
        users.append(user)

    session.flush()  # assign IDs
    return users


def generate_sessions(
    session: Session,
    charger: Charger,
    users: list[User],
    true_reliability: float,
) -> list[ChargingSession]:
    """Generate charging sessions for one charger."""
    num = random.randint(*SESSIONS_PER_CHARGER)
    records = []

    for _ in range(num):
        user = random.choice(users)
        battery_cap = user.battery_capacity_kwh or 30.0

        # Determine status based on true_reliability
        roll = random.random()
        if roll < true_reliability:
            status = SessionStatus.success
        elif roll < true_reliability + (1 - true_reliability) * 0.6:
            status = SessionStatus.failed
        else:
            status = SessionStatus.interrupted

        # SOC values
        soc_start = round(random.uniform(8, 50), 1)

        if status == SessionStatus.success:
            soc_end = round(random.uniform(max(soc_start + 20, 70), 95), 1)
        elif status == SessionStatus.failed:
            soc_end = soc_start  # no energy delivered
        else:  # interrupted
            soc_end = round(random.uniform(soc_start + 2, min(soc_start + 25, 90)), 1)

        soc_delta = soc_end - soc_start
        energy = round(battery_cap * soc_delta / 100.0, 2)
        if energy < 0:
            energy = 0.0

        # Charging power for duration calculation
        charger_power = charger.charging_power_kw or 30.0
        if energy > 0 and charger_power > 0:
            hours = energy / charger_power
            # Add 10-20% overhead for tapering/idle
            hours *= random.uniform(1.1, 1.25)
            duration_minutes = max(5, int(hours * 60))
        else:
            duration_minutes = random.randint(1, 10)  # failed sessions are short

        start_time = random_timestamp_in_window()
        end_time = start_time + timedelta(minutes=duration_minutes)

        # Cost: 12-22 INR/kWh in India
        rate = round(random.uniform(12, 22), 1)
        cost = round(energy * rate, 2) if energy > 0 else 0.0

        rec = ChargingSession(
            charger_id=charger.id,
            user_id=user.id,
            start_time=start_time,
            end_time=end_time,
            energy_delivered_kwh=energy,
            status=status,
            soc_start=soc_start,
            soc_end=soc_end,
            cost=cost,
            source=SessionSource.simulated,
        )
        session.add(rec)
        records.append(rec)

    return records


def generate_reviews(
    session: Session,
    charger: Charger,
    users: list[User],
    true_reliability: float,
) -> list[Review]:
    """Generate reviews for one charger, correlated with reliability.

    Rating distribution is tightly coupled to ``true_reliability``:
    - The probability of a positive review (4-5) is reliability ** 1.5
      (amplifies separation between good and bad chargers).
    - Within the positive bucket, higher reliability skews harder to 5.
    - Within the negative bucket, lower reliability skews harder to 1.
    - Review count is 12-25 to reduce per-charger noise.
    """
    num = random.randint(12, 25)
    records = []

    # Amplified positive-review probability: reliability^1.5
    # rel=0.55 -> p_pos~0.41, rel=0.80 -> p_pos~0.72, rel=0.95 -> p_pos~0.93
    p_positive = true_reliability ** 1.5

    for _ in range(num):
        user = random.choice(users)

        if random.random() < p_positive:
            # Positive bucket: higher reliability -> more 5s vs 4s
            w5 = int(true_reliability * 10)   # rel=0.95 -> 9, rel=0.60 -> 6
            w4 = 10 - w5
            rating = random.choices([4, 5], weights=[w4, w5], k=1)[0]
        else:
            # Negative bucket: lower reliability -> more 1s
            unreliability = 1 - true_reliability
            w1 = int(unreliability * 10) + 1  # rel=0.55 -> 5, rel=0.95 -> 1
            w2 = 3
            w3 = 2
            rating = random.choices([1, 2, 3], weights=[w1, w2, w3], k=1)[0]

        # Review text correlated with rating
        if rating >= 4:
            text = random.choice(POSITIVE_REVIEWS)
        elif rating == 3:
            text = random.choice(NEUTRAL_REVIEWS)
        else:
            text = random.choice(NEGATIVE_REVIEWS)

        # Timestamp in the last 6 months
        days_ago = random.randint(0, HISTORY_DAYS - 1)
        created = datetime.now() - timedelta(
            days=days_ago,
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )

        rec = Review(
            charger_id=charger.id,
            user_id=user.id,
            rating=rating,
            review_text=text,
            sentiment_score=None,  # filled by NLP model later
            created_at=created,
        )
        session.add(rec)
        records.append(rec)

    return records


def generate_faults(
    session: Session,
    charger: Charger,
    true_reliability: float,
) -> list[Fault]:
    """Generate fault reports inversely correlated with reliability.

    Fault count is tightly coupled to unreliability:
    - Expected count = (1 - reliability) * 40  (linear scaling)
    - Drawn from a binomial distribution so spread is moderate.
    - rel=0.55 -> ~18 faults, rel=0.80 -> ~8, rel=0.95 -> ~2
    """
    unreliability = 1 - true_reliability
    expected = unreliability * 40
    # Binomial(n=round(expected*2), p=0.5) -> mean=expected, sd=sqrt(expected/2)
    n_trials = max(1, round(expected * 2))
    num = sum(1 for _ in range(n_trials) if random.random() < 0.5)
    records = []

    for _ in range(num):
        fault_type = random.choice(FAULT_TYPES)
        days_ago = random.randint(0, HISTORY_DAYS - 1)
        reported = datetime.now() - timedelta(
            days=days_ago,
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )

        # Most faults eventually resolved (weighted 75% true)
        resolved = random.random() < 0.75
        resolved_at = None
        if resolved:
            resolve_days = random.randint(1, 14)
            resolved_at = reported + timedelta(days=resolve_days)

        rec = Fault(
            charger_id=charger.id,
            reported_at=reported,
            fault_type=fault_type,
            description=FAULT_DESCRIPTIONS.get(fault_type, ""),
            resolved=resolved,
            resolved_at=resolved_at,
            source=FaultSource.user_report,
        )
        session.add(rec)
        records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_synthetic_data(session: Session) -> None:
    """Delete all synthetic / script-generated rows without touching real
    charger data from Phase 2."""
    # Order matters for FK constraints
    # 1) Sessions with source='simulated'
    n_sess = session.query(ChargingSession).filter(
        ChargingSession.source == SessionSource.simulated
    ).delete()
    # 2) All reviews (no real reviews exist yet; all are script-generated)
    n_rev = session.query(Review).delete()
    # 3) Faults with source='user_report' (all script-generated)
    n_flt = session.query(Fault).filter(
        Fault.source == FaultSource.user_report
    ).delete()
    # 4) All users (all are script-generated)
    n_usr = session.query(User).delete()

    session.commit()
    print(
        f"[RESET] Deleted: {n_sess} sessions, {n_rev} reviews, "
        f"{n_flt} faults, {n_usr} users\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic charging data for the EV platform."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing synthetic data before regenerating.",
    )
    args = parser.parse_args()

    random.seed(RANDOM_SEED)

    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        # ── Reset if requested ──────────────────────────────────────
        if args.reset:
            reset_synthetic_data(db)

        # ── Load real chargers ──────────────────────────────────────
        chargers = db.query(Charger).all()
        if not chargers:
            print("[ERROR] No chargers found. Run the ingestion script first.",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Loaded {len(chargers)} chargers from the database.\n")

        # ── Assign hidden reliability ───────────────────────────────
        reliability_map = assign_true_reliability(chargers)
        rel_values = list(reliability_map.values())
        print(
            f"Assigned true_reliability: "
            f"min={min(rel_values):.3f}  max={max(rel_values):.3f}  "
            f"mean={sum(rel_values)/len(rel_values):.3f}\n"
        )

        # ── Generate users ──────────────────────────────────────────
        users = generate_users(db)
        print(f"Created {len(users)} users.")

        # ── Generate sessions, reviews, faults per charger ──────────
        all_sessions: list[ChargingSession] = []
        all_reviews: list[Review] = []
        all_faults: list[Fault] = []

        for charger in chargers:
            rel = reliability_map[charger.id]
            all_sessions.extend(generate_sessions(db, charger, users, rel))
            all_reviews.extend(generate_reviews(db, charger, users, rel))
            all_faults.extend(generate_faults(db, charger, rel))

        db.commit()

        # ── Collect stats while session is still open ───────────────
        num_users = len(users)
        num_sessions = len(all_sessions)
        status_counts = {s: 0 for s in SessionStatus}
        for s in all_sessions:
            status_counts[s.status] += 1

        num_reviews = len(all_reviews)
        ratings = [r.rating for r in all_reviews]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0

        num_faults = len(all_faults)
        resolved_count = sum(1 for f in all_faults if f.resolved)
        unresolved_count = num_faults - resolved_count

        # ── Per-charger aggregates for correlation check ────────────
        charger_ids = [c.id for c in chargers]
        per_charger_avg_rating: dict[int, float] = {}
        per_charger_fault_count: dict[int, int] = {cid: 0 for cid in charger_ids}

        # Aggregate ratings per charger
        from collections import defaultdict
        charger_ratings: dict[int, list[int]] = defaultdict(list)
        for r in all_reviews:
            charger_ratings[r.charger_id].append(r.rating)
        for cid in charger_ids:
            rr = charger_ratings.get(cid, [])
            per_charger_avg_rating[cid] = sum(rr) / len(rr) if rr else 0.0

        # Count faults per charger
        for f in all_faults:
            per_charger_fault_count[f.charger_id] += 1

    engine.dispose()

    # ── Summary ─────────────────────────────────────────────────────
    print("\n========== Synthetic Data Summary ==========")
    print(f"  Users created          : {num_users}")
    print(f"  Sessions created       : {num_sessions}")
    for st, cnt in status_counts.items():
        print(f"    - {st.value:<13s}    : {cnt}")
    print(f"  Reviews created        : {num_reviews}")
    print(f"    - Average rating     : {avg_rating:.2f}")
    print(f"  Faults created         : {num_faults}")
    print(f"    - Resolved           : {resolved_count}")
    print(f"    - Unresolved         : {unresolved_count}")

    # ── Correlation check ───────────────────────────────────────────
    # Pearson correlation: cov(x,y) / (std(x) * std(y))
    def pearson(xs: list[float], ys: list[float]) -> float:
        n = len(xs)
        if n < 3:
            return float("nan")
        mx = sum(xs) / n
        my = sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
        sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
        sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
        if sx == 0 or sy == 0:
            return float("nan")
        return cov / (sx * sy)

    rel_list = [reliability_map[cid] for cid in charger_ids]
    avg_rat_list = [per_charger_avg_rating[cid] for cid in charger_ids]
    flt_cnt_list = [float(per_charger_fault_count[cid]) for cid in charger_ids]

    corr_rating = pearson(rel_list, avg_rat_list)
    corr_faults = pearson(rel_list, flt_cnt_list)

    print(f"\n  Correlation check (Pearson, n={len(charger_ids)} chargers):")
    print(f"    true_reliability vs avg_rating    : {corr_rating:+.4f}")
    print(f"    true_reliability vs num_faults    : {corr_faults:+.4f}")
    print("=============================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
