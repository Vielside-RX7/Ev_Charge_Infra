"""
Reliability Model Training and Inference Module
===============================================
Trains and evaluates machine learning models to predict EV charger reliability
(target: success_rate) based on operational, fault, and review features.

Models evaluated via Leave-One-Out Cross-Validation (LOOCV):
- RandomForestRegressor
- XGBRegressor (XGBoost)

Artifacts saved:
- models/artifacts/reliability_model.pkl (Trained model)
- models/artifacts/reliability_model_features.json (Feature column list)
"""

import json
import os
import sys
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import LeaveOneOut
from xgboost import XGBRegressor

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_PATH = os.path.join(BASE_DIR, "data", "processed", "charger_reliability_features.csv")
ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "reliability_model.pkl")
FEATURES_PATH = os.path.join(ARTIFACTS_DIR, "reliability_model_features.json")

# ---------------------------------------------------------------------------
# Feature Definitions
# ---------------------------------------------------------------------------
TARGET_COL = "success_rate"

# Explicitly excluded columns to prevent data leakage (derived directly from target):
# num_sessions, num_success, num_failed, num_interrupted, failure_rate, interrupted_rate
EXCLUDED_COLS = [
    "charger_id",
    "num_sessions",
    "num_success",
    "num_failed",
    "num_interrupted",
    "failure_rate",
    "interrupted_rate",
    "avg_energy_delivered_kwh",
    TARGET_COL,
]

FEATURE_COLS: List[str] = [
    "avg_session_duration_minutes",
    "num_faults",
    "num_resolved_faults",
    "resolved_fault_ratio",
    "days_since_last_fault",
    "has_no_faults",
    "avg_rating",
    "avg_sentiment_score",
    "review_count",
    "num_reviews_low",
    "num_reviews_high",
]

# Global cache for loaded model and features
_CACHED_MODEL = None
_CACHED_FEATURES = None


# ---------------------------------------------------------------------------
# Data Preprocessing
# ---------------------------------------------------------------------------
def preprocess_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Preprocess dataframe to generate exact model feature matrix.
    Handles NaN values in days_since_last_fault and creates has_no_faults flag.
    """
    df_clean = df.copy()

    # Derived flag: 1 if charger has no recorded faults / days_since_last_fault is NaN, else 0
    if "has_no_faults" not in df_clean.columns:
        if "days_since_last_fault" in df_clean.columns:
            df_clean["has_no_faults"] = df_clean["days_since_last_fault"].isna().astype(int)
        else:
            df_clean["has_no_faults"] = (df_clean.get("num_faults", 0) == 0).astype(int)

    # Fill NaN sentinel for days_since_last_fault (-1.0 indicating no fault history)
    if "days_since_last_fault" in df_clean.columns:
        df_clean["days_since_last_fault"] = df_clean["days_since_last_fault"].fillna(-1.0)

    # Fill NaN sentinel for avg_sentiment_score (-1.0 indicating no scored reviews)
    if "avg_sentiment_score" in df_clean.columns:
        df_clean["avg_sentiment_score"] = df_clean["avg_sentiment_score"].fillna(-1.0)

    # Fill any remaining NaNs in numeric features with 0.0
    for col in FEATURE_COLS:
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].fillna(0.0)

    return df_clean


def load_and_prepare_data(csv_path: str = DATA_PATH) -> Tuple[pd.DataFrame, pd.Series]:
    """Load features CSV, clean and split into X and y."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Feature dataset not found at: {csv_path}")

    df = pd.read_csv(csv_path)
    df_prep = preprocess_features(df)

    X = df_prep[FEATURE_COLS]
    y = df_prep[TARGET_COL]

    return X, y


# ---------------------------------------------------------------------------
# Model Evaluation (LOOCV and 10-Fold CV)
# ---------------------------------------------------------------------------
from sklearn.model_selection import KFold, LeaveOneOut


def evaluate_loocv(
    model: Any, X: np.ndarray, y: np.ndarray
) -> Tuple[float, float, float, np.ndarray]:
    """
    Evaluate a regression model using Leave-One-Out Cross-Validation.
    Returns: (R², MAE, Pearson correlation, out-of-fold predictions)
    """
    loo = LeaveOneOut()
    y_preds: List[float] = []
    y_trues: List[float] = []

    for train_idx, test_idx in loo.split(X):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model_clone = sklearn_clone(model)
        model_clone.fit(X_train, y_train)
        pred = float(model_clone.predict(X_test)[0])

        y_preds.append(pred)
        y_trues.append(float(y_test[0]))

    y_pred_arr = np.array(y_preds)
    y_true_arr = np.array(y_trues)

    r2 = float(r2_score(y_true_arr, y_pred_arr))
    mae = float(mean_absolute_error(y_true_arr, y_pred_arr))
    corr, _ = pearsonr(y_true_arr, y_pred_arr)

    return r2, mae, float(corr), y_pred_arr


def evaluate_kfold(
    model: Any,
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 10,
    random_state: int = 42,
) -> Tuple[float, float, float, np.ndarray, List[Dict[str, float]]]:
    """
    Evaluate a regression model using k-fold cross-validation with shuffling.

    Parameters
    ----------
    model : Any
        Scikit-learn or XGBoost regressor instance.
    X : np.ndarray
        Feature matrix.
    y : np.ndarray
        Target vector.
    n_splits : int, default 10
        Number of cross-validation folds.
    random_state : int, default 42
        Fixed random seed for reproducible fold shuffling.

    Returns
    -------
    tuple
        (overall_r2, overall_mae, overall_pearson_r, y_pred_arr, fold_metrics_list)
    """
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    y_preds = np.zeros_like(y, dtype=float)
    fold_metrics = []

    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(X, y), 1):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model_clone = sklearn_clone(model)
        model_clone.fit(X_train, y_train)
        preds = model_clone.predict(X_test)
        y_preds[test_idx] = preds

        fold_r2 = float(r2_score(y_test, preds))
        fold_mae = float(mean_absolute_error(y_test, preds))
        fold_corr, _ = pearsonr(y_test, preds) if len(np.unique(preds)) > 1 else (0.0, 1.0)

        fold_metrics.append({
            "fold": fold_idx,
            "r2": fold_r2,
            "mae": fold_mae,
            "pearson_r": float(fold_corr),
            "n_test": len(test_idx),
        })

    overall_r2 = float(r2_score(y, y_preds))
    overall_mae = float(mean_absolute_error(y, y_preds))
    overall_corr, _ = pearsonr(y, y_preds)

    return overall_r2, overall_mae, float(overall_corr), y_preds, fold_metrics


def sklearn_clone(model: Any) -> Any:
    """Clone an estimator by re-instantiating with identical parameters."""
    if isinstance(model, RandomForestRegressor):
        return RandomForestRegressor(**model.get_params())
    elif isinstance(model, XGBRegressor):
        return XGBRegressor(**model.get_params())
    from sklearn.base import clone
    return clone(model)


# ---------------------------------------------------------------------------
# Training & Model Artifact Management
# ---------------------------------------------------------------------------
import time


def train_and_save_model() -> Dict[str, Any]:
    """
    Runs 10-fold Cross-Validation and LOOCV comparison across candidate models,
    selects the best model based on 10-fold CV, retrains on the full dataset,
    saves production artifacts, and returns evaluation results.
    """
    print(f"Loading data from: {DATA_PATH}")
    X_df, y_series = load_and_prepare_data(DATA_PATH)
    X = X_df.values
    y = y_series.values

    print(f"Dataset loaded: {len(X)} samples, {X.shape[1]} features.")
    print(f"Target: {TARGET_COL}")
    print(f"Feature columns ({len(FEATURE_COLS)}): {FEATURE_COLS}\n")

    # Define candidate models
    candidate_models = {
        "RandomForestRegressor": RandomForestRegressor(
            n_estimators=100,
            max_depth=3,
            random_state=42,
        ),
        "XGBRegressor": XGBRegressor(
            n_estimators=30,
            learning_rate=0.1,
            max_depth=2,
            random_state=42,
        ),
    }

    kfold_results: Dict[str, Dict[str, Any]] = {}
    loocv_results: Dict[str, Dict[str, Any]] = {}

    print("=" * 80)
    print("                 CROSS-VALIDATION METHODOLOGY COMPARISON (n=382)")
    print("=" * 80)
    print(f"{'Model':<24} | {'Method':<10} | {'R^2':<9} | {'MAE':<9} | {'Pearson r':<10} | {'Time (s)':<8}")
    print("-" * 80)

    best_model_name = None
    best_r2 = -float("inf")

    for name, model in candidate_models.items():
        # 1. 10-Fold CV
        t0 = time.time()
        k_r2, k_mae, k_corr, _, k_folds = evaluate_kfold(model, X, y, n_splits=10, random_state=42)
        k_time = time.time() - t0
        kfold_results[name] = {"r2": k_r2, "mae": k_mae, "pearson_r": k_corr, "time": k_time, "folds": k_folds}
        print(f"{name:<24} | {'10-Fold':<10} | {k_r2:<9.4f} | {k_mae:<9.4f} | {k_corr:<10.4f} | {k_time:<8.2f}")

        # 2. LOOCV
        t0 = time.time()
        l_r2, l_mae, l_corr, _ = evaluate_loocv(model, X, y)
        l_time = time.time() - t0
        loocv_results[name] = {"r2": l_r2, "mae": l_mae, "pearson_r": l_corr, "time": l_time}
        print(f"{name:<24} | {'LOOCV':<10} | {l_r2:<9.4f} | {l_mae:<9.4f} | {l_corr:<10.4f} | {l_time:<8.2f}")
        print("-" * 80)

        if k_r2 > best_r2:
            best_r2 = k_r2
            best_model_name = name

    print("=" * 80)
    print(f"\nBest performing model on 10-Fold CV: {best_model_name} (10-Fold R^2 = {best_r2:.4f})")

    # Per-fold breakdown for best model
    best_folds = kfold_results[best_model_name]["folds"]
    print(f"\n--- Per-Fold Breakdown for {best_model_name} (10-Fold CV) ---")
    print(f"{'Fold':<6} | {'Test N':<8} | {'Fold R^2':<10} | {'Fold MAE':<10} | {'Pearson r':<10}")
    print("-" * 52)
    for f in best_folds:
        print(f"Fold {f['fold']:<2} | {f['n_test']:<8} | {f['r2']:<10.4f} | {f['mae']:<10.4f} | {f['pearson_r']:<10.4f}")
    print("-" * 52)

    # Retrain best model on full dataset
    best_estimator = candidate_models[best_model_name]
    best_estimator.fit(X, y)

    # Create artifacts directory
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    # Save model and feature definitions
    joblib.dump(best_estimator, MODEL_PATH)
    with open(FEATURES_PATH, "w") as f:
        json.dump(FEATURE_COLS, f, indent=2)

    print(f"\nSaved model artifact: {MODEL_PATH}")
    print(f"Saved feature list artifact: {FEATURES_PATH}")

    # Display Feature Importances
    importances = best_estimator.feature_importances_
    fi_pairs = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)

    print("\n--- Feature Importances (Descending) ---")
    for feat_name, imp in fi_pairs:
        print(f"  {feat_name:<30}: {imp:.4f} ({imp * 100:.1f}%)")
    print("-" * 45)

    return {
        "best_model_name": best_model_name,
        "kfold_results": kfold_results,
        "loocv_results": loocv_results,
        "feature_importances": dict(fi_pairs),
    }


# ---------------------------------------------------------------------------
# Inference Interface
# ---------------------------------------------------------------------------
def predict_reliability(feature_dict: Dict[str, Any]) -> float:
    """
    Inference helper function for the API layer.
    Loads saved model and feature definitions, builds feature vector in correct order,
    and returns predicted reliability score clipped to [0.0, 1.0].

    Parameters
    ----------
    feature_dict : dict
        Dictionary containing charger features.

    Returns
    -------
    float
        Predicted reliability score between 0.0 and 1.0.
    """
    global _CACHED_MODEL, _CACHED_FEATURES

    if _CACHED_MODEL is None or _CACHED_FEATURES is None:
        if not os.path.exists(MODEL_PATH) or not os.path.exists(FEATURES_PATH):
            raise FileNotFoundError(
                f"Model artifacts not found. Please train the model first by running "
                f"python models/reliability_model.py"
            )
        _CACHED_MODEL = joblib.load(MODEL_PATH)
        with open(FEATURES_PATH, "r") as f:
            _CACHED_FEATURES = json.load(f)

    # Handle feature transformations
    input_data = dict(feature_dict)

    # Process days_since_last_fault and has_no_faults
    days_val = input_data.get("days_since_last_fault")
    if days_val is None or (isinstance(days_val, float) and np.isnan(days_val)):
        input_data["has_no_faults"] = 1.0
        input_data["days_since_last_fault"] = -1.0
    else:
        input_data["has_no_faults"] = float(input_data.get("has_no_faults", 0.0))
        input_data["days_since_last_fault"] = float(days_val)

    # Build feature vector in exact required order
    vector: List[float] = []
    for feat in _CACHED_FEATURES:
        val = input_data.get(feat, 0.0)
        vector.append(float(val) if val is not None else 0.0)

    X_sample = np.array([vector])
    raw_pred = float(_CACHED_MODEL.predict(X_sample)[0])

    # Clip to valid probability / reliability score range [0.0, 1.0]
    return float(np.clip(raw_pred, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Main Execution Block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        eval_summary = train_and_save_model()

        # Sanity test predict_reliability
        sample_input = {
            "avg_energy_delivered_kwh": 16.5,
            "avg_session_duration_minutes": 35.0,
            "num_faults": 2,
            "num_resolved_faults": 2,
            "resolved_fault_ratio": 1.0,
            "days_since_last_fault": 45.0,
            "avg_rating": 4.5,
            "avg_sentiment_score": 0.85,
            "review_count": 20,
            "num_reviews_low": 1,
            "num_reviews_high": 18,
        }
        score = predict_reliability(sample_input)
        print(f"\nSanity check inference prediction for sample charger: {score:.4f}")

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
