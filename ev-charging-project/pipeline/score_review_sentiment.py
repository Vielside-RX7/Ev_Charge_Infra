"""
Review Sentiment Scoring Pipeline
=================================
Scores user reviews in the PostgreSQL database using a pretrained HuggingFace
transformer model (distilbert-base-uncased-finetuned-sst-2-english).

Outputs sentiment scores in the range [0.0, 1.0]:
- 1.0: Extremely positive
- 0.0: Extremely negative
- 0.5: Neutral

Usage:
    python pipeline/score_review_sentiment.py          # Score unscored reviews
    python pipeline/score_review_sentiment.py --rescore # Rescore all reviews
"""

import argparse
import os
import sys
from typing import List

from dotenv import load_dotenv
from scipy.stats import pearsonr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from transformers import pipeline

# ---------------------------------------------------------------------------
# Project Paths & Environment
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[ERROR] DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(1)

from models import Base, Review  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL_NAME = "distilbert-base-uncased-finetuned-sst-2-english"
BATCH_SIZE = 32


# ---------------------------------------------------------------------------
# Sentiment Scoring Function
# ---------------------------------------------------------------------------
def score_reviews(rescore: bool = False) -> None:
    """
    Load reviews with missing sentiment (or all if rescore=True),
    batch inference with HuggingFace pipeline, and update the database.
    """
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        query = session.query(Review).filter(Review.review_text.isnot(None))
        if not rescore:
            query = query.filter(Review.sentiment_score.is_(None))

        reviews_to_score: List[Review] = query.order_by(Review.id).all()
        total_to_score = len(reviews_to_score)

        if total_to_score == 0:
            print("[INFO] No reviews need scoring. (Use --rescore to re-evaluate all reviews)")
        else:
            print(f"Loading pretrained sentiment model: {MODEL_NAME} ...")
            classifier = pipeline(
                "sentiment-analysis",
                model=MODEL_NAME,
                truncation=True,
                max_length=512,
            )

            print(f"Scoring {total_to_score} review(s) in batches of {BATCH_SIZE} ...")

            for i in range(0, total_to_score, BATCH_SIZE):
                batch = reviews_to_score[i : i + BATCH_SIZE]
                texts = [r.review_text.strip() for r in batch]
                results = classifier(texts)

                for rev, res in zip(batch, results):
                    label = res["label"].upper()
                    raw_score = float(res["score"])

                    # SST-2 outputs POSITIVE / NEGATIVE with confidence score in [0.5, 1.0]
                    # Map to [0.0, 1.0] continuous scale:
                    # POSITIVE with 0.95 -> 0.95
                    # NEGATIVE with 0.95 -> 1 - 0.95 = 0.05
                    if label == "POSITIVE":
                        normalized_score = raw_score
                    elif label == "NEGATIVE":
                        normalized_score = 1.0 - raw_score
                    else:
                        normalized_score = 0.5

                    rev.sentiment_score = round(normalized_score, 4)

                session.commit()
                processed = min(i + BATCH_SIZE, total_to_score)
                print(f"  Processed {processed}/{total_to_score} reviews...")

        # ── Compute Summary & Correlation ───────────────────────────────
        all_scored: List[Review] = (
            session.query(Review)
            .filter(Review.sentiment_score.isnot(None))
            .order_by(Review.id)
            .all()
        )

        if not all_scored:
            print("[WARNING] No scored reviews found in database.")
            return

        ratings = [r.rating for r in all_scored]
        sentiments = [r.sentiment_score for r in all_scored]

        avg_sentiment = sum(sentiments) / len(sentiments)
        avg_rating = sum(ratings) / len(ratings)
        corr, p_value = pearsonr(ratings, sentiments)

        print("\n" + "=" * 55)
        print("========== Review Sentiment Scoring Summary ==========")
        print(f"  Total scored reviews in DB : {len(all_scored)}")
        print(f"  Average star rating        : {avg_rating:.2f} / 5.0")
        print(f"  Average sentiment score    : {avg_sentiment:.4f} (0=neg, 1=pos)")
        print(f"  Pearson correlation (r)    : {corr:+.4f} (p={p_value:.2e})")
        print("=" * 55)

        # ── Sample Scored Reviews ───────────────────────────────────────
        print("\n--- Sample Scored Reviews (Rating vs Sentiment) ---")
        samples = (
            session.query(Review)
            .filter(Review.sentiment_score.isnot(None))
            .order_by(Review.id)
            .limit(5)
            .all()
        )
        for s in samples:
            stars = "*" * s.rating
            print(f"  [{stars:<5}] Score: {s.sentiment_score:.4f} | \"{s.review_text}\"")
        print("-" * 55)

    engine.dispose()


# ---------------------------------------------------------------------------
# Main Block
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Score EV charger user review sentiments.")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-score all reviews even if sentiment_score is already populated.",
    )
    args = parser.parse_args()

    score_reviews(rescore=args.rescore)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] Sentiment scoring failed: {exc}", file=sys.stderr)
        sys.exit(1)
