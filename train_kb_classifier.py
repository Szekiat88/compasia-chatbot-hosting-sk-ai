"""
train_kb_classifier.py
======================
Trains a Random Forest intent classifier on the knowledge-base test cases.

What it does
------------
  1. Loads all 497 labeled (question → keyword) pairs from the test cases sheet
  2. Builds TF-IDF features with unigrams + bigrams
  3. Trains a Random Forest (300 trees, balanced class weights)
  4. Runs 5-fold cross-validation to get an honest accuracy estimate
  5. Saves the model to  models/kb_rf/

Usage
-----
  python train_kb_classifier.py

Output
------
  models/kb_rf/rf_model.joblib          — trained classifier
  models/kb_rf/tfidf_vectorizer.joblib  — fitted TF-IDF transformer
  models/kb_rf/label_encoder.joblib     — keyword ↔ integer mapping
  models/kb_rf/training_report.txt      — accuracy + per-class report
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder
import joblib

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).resolve().parent
DATA_PATH       = BASE_DIR / "data" / "Test_Cases_MainDB_new.xlsx"
TEST_SHEET      = "Test Cases"
MODEL_DIR       = BASE_DIR / "models" / "kb_rf"

# ---------------------------------------------------------------------------
# Hyperparameters (change here if you want to retrain with different settings)
# ---------------------------------------------------------------------------
N_ESTIMATORS        = 300     # number of trees — more = slower but more stable
MAX_FEATURES        = "sqrt"  # features per split — sqrt(n_features) is standard
MIN_SAMPLES_LEAF    = 1       # allow single-sample leaves (fine for small data)
NGRAM_RANGE         = (1, 2)  # unigrams + bigrams
MAX_TFIDF_FEATURES  = 8000    # vocabulary cap
CV_FOLDS            = 5       # cross-validation folds
RANDOM_STATE        = 42
CONFIDENCE_THRESHOLD = 0.45   # same value used in kb_classifier.py

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _color(code: str, text: str) -> str:
    colors = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "reset": "\033[0m"}
    return f"{colors.get(code, '')}{text}{colors['reset']}"


def _bar(rate: float, width: int = 20) -> str:
    filled = int(rate * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
def load_data() -> pd.DataFrame:
    df = pd.read_excel(DATA_PATH, sheet_name=TEST_SHEET)
    df = df.dropna(subset=["question", "keyword"])
    df["question"] = df["question"].astype(str).str.strip()
    df["keyword"]  = df["keyword"].astype(str).str.strip()
    df = df[(df["question"] != "") & (df["keyword"] != "")]
    return df


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------
def train(df: pd.DataFrame):
    X_text = df["question"].tolist()
    y_raw  = df["keyword"].tolist()

    # TF-IDF vectorisation
    vectorizer = TfidfVectorizer(
        ngram_range=NGRAM_RANGE,
        max_features=MAX_TFIDF_FEATURES,
        sublinear_tf=True,  # log(1 + tf) — dampens high-frequency terms
        strip_accents="unicode",
        analyzer="word",
        min_df=1,
    )
    X = vectorizer.fit_transform(X_text)

    # Encode string labels → integers
    le = LabelEncoder()
    y  = le.fit_transform(y_raw)

    # Random Forest
    clf = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_features=MAX_FEATURES,
        min_samples_leaf=MIN_SAMPLES_LEAF,
        class_weight="balanced",  # handles class imbalance (not all keywords have same # of samples)
        random_state=RANDOM_STATE,
        n_jobs=-1,                # use all CPU cores
    )

    # Cross-validation (stratified — preserves class proportions in each fold)
    print("  Running 5-fold stratified cross-validation...")
    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    cv_scores = cross_val_score(clf, X, y, cv=cv, scoring="accuracy", n_jobs=-1)
    print(f"  CV accuracy per fold: {[f'{s:.1%}' for s in cv_scores]}")
    print(f"  Mean: {cv_scores.mean():.1%}  ±  {cv_scores.std():.1%}")

    # Held-out test split for detailed report
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=RANDOM_STATE
    )
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    # Full training on all data (for production model)
    print("  Training final model on full dataset...")
    clf_final = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_features=MAX_FEATURES,
        min_samples_leaf=MIN_SAMPLES_LEAF,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    clf_final.fit(X, y)

    return vectorizer, le, clf_final, clf, y_test, y_pred, cv_scores


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def save_model(model_dir: Path, vectorizer, le, clf) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(vectorizer, model_dir / "tfidf_vectorizer.joblib")
    joblib.dump(le,         model_dir / "label_encoder.joblib")
    joblib.dump(clf,        model_dir / "rf_model.joblib")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def print_report(
    model_dir: Path,
    le: LabelEncoder,
    cv_scores: np.ndarray,
    clf_test,
    y_test,
    y_pred,
    n_total: int,
    n_classes: int,
) -> None:
    label_names = le.inverse_transform(sorted(set(y_test)))
    report_str  = classification_report(
        y_test, y_pred,
        labels=sorted(set(y_test)),
        target_names=[str(n)[:60] for n in label_names],
        zero_division=0,
    )

    # Feature importances
    feature_names = None
    importances   = None
    try:
        feat_names    = np.array(clf_test.feature_importances_)
        # We can't easily recover vectorizer feature names here, skip for now
    except Exception:
        pass

    lines = [
        "=" * 70,
        "Random Forest Knowledge-Base Classifier — Training Report",
        "=" * 70,
        f"Training samples : {n_total}",
        f"Classes (keywords): {n_classes}",
        f"CV folds          : {CV_FOLDS}",
        f"Trees             : {N_ESTIMATORS}",
        f"",
        f"5-fold CV accuracy: {cv_scores.mean():.1%} ± {cv_scores.std():.1%}",
        f"Held-out accuracy : {(y_pred == y_test).mean():.1%}  (20% split)",
        f"",
        "Per-class report (held-out test set):",
        report_str,
    ]

    report_text = "\n".join(lines)
    print(report_text)
    (model_dir / "training_report.txt").write_text(report_text, encoding="utf-8")

    # Terminal bar chart — per-class precision sorted worst-first
    from sklearn.metrics import precision_score
    classes_in_test = sorted(set(y_test))
    precisions = precision_score(y_test, y_pred, labels=classes_in_test,
                                 average=None, zero_division=0)
    pairs = sorted(zip(precisions, [le.inverse_transform([c])[0] for c in classes_in_test]))

    print("\nPer-keyword precision (worst first):")
    for prec, kw in pairs:
        color = "green" if prec >= 0.8 else ("yellow" if prec >= 0.5 else "red")
        short_kw = str(kw).replace("\n", " ")[:55]
        print(f"  {prec:5.1%}  {_color(color, _bar(prec))}  {short_kw}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("\n=== Training Random Forest KB Classifier ===\n")

    print(f"Loading training data from {DATA_PATH} ...")
    df = load_data()
    print(f"  {len(df)} samples  |  {df['keyword'].nunique()} unique keywords")

    print("\nTraining...")
    vectorizer, le, clf_final, clf_test, y_test, y_pred, cv_scores = train(df)

    print(f"\nSaving model to {MODEL_DIR} ...")
    save_model(MODEL_DIR, vectorizer, le, clf_final)
    print("  Saved: rf_model.joblib, tfidf_vectorizer.joblib, label_encoder.joblib")

    print_report(MODEL_DIR, le, cv_scores, clf_test, y_test, y_pred,
                 n_total=len(df), n_classes=df["keyword"].nunique())

    print(f"\n✓ Training complete. Model ready at: {MODEL_DIR}/")
    print(f"  Confidence threshold for production: {CONFIDENCE_THRESHOLD}")
    print(f"  Queries above this threshold → RF answers instantly (no API call)")
    print(f"  Queries below → LLM fallback (handles novel phrasing)")


if __name__ == "__main__":
    main()
