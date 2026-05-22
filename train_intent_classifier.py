from _ai_config import PROVIDER_PRIMARY
"""
Intent Classifier — trains on test cases, evaluates on held-out set.

Models trained:
  A — Sentence Transformer + Nearest Centroid  (best for multilingual small data)
  B — TF-IDF + Linear SVM                      (classical baseline)
  C — TF-IDF + Random Forest                   (ensemble baseline)
  D — NLU Engine Classifier                    (production model, used as reference)

Each run is saved to a numbered folder so results are never overwritten.
Adjust parameters in the PARAMETERS section below, then re-run.

Usage:
    python train_intent_classifier.py           # run all models
    python train_intent_classifier.py --run 3   # re-evaluate a specific run folder
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.neighbors import NearestCentroid
from sklearn.svm import LinearSVC

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ══════════════════════════════════════════════════════════════════════════════
# PARAMETERS — adjust these, then re-run. Each run saves to a new numbered
# folder so you can compare results across different settings.
# ══════════════════════════════════════════════════════════════════════════════

# Data split
TEST_SIZE    = 0.20   # fraction of data held out for testing (0.20 = 20%)
RANDOM_STATE = 42     # change this to get a different random split

# Model A — Sentence Transformer
# Full list: https://www.sbert.net/docs/pretrained_models.html
EMBED_MODEL  = "paraphrase-multilingual-MiniLM-L12-v2"   # best for English + Malay
EMBED_BATCH  = 64     # lower this if you run out of memory (e.g. 32)

# Model B — TF-IDF + SVM
SVM_C        = 1.0    # regularisation strength: lower = simpler model, higher = fits harder
SVM_ITER     = 2000   # max training iterations; increase if you see convergence warnings

# Model C — TF-IDF + Random Forest
RF_TREES     = 500    # number of decision trees; more = better but slower
RF_MAX_DEPTH = None   # None = grow full trees; set a number (e.g. 20) to limit depth
RF_MIN_LEAF  = 1      # minimum samples required at a leaf; increase to reduce overfitting

# TF-IDF shared settings (used by both Model B and C)
TFIDF_NGRAM  = (1, 2) # (1,1) = single words only; (1,2) = words + 2-word phrases
TFIDF_MIN_DF = 1      # ignore terms that appear fewer than this many times

# ══════════════════════════════════════════════════════════════════════════════

TEST_CASES_PATH = BASE_DIR / "data" / "Test_Cases_MainDB_new.xlsx"
TEST_SHEET      = "Test Cases"
MODEL_BASE_DIR  = BASE_DIR / "models"
RESULTS_DIR     = BASE_DIR / "data"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"

def _bar(rate: float, width: int = 20) -> str:
    filled = int(rate / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── helpers ────────────────────────────────────────────────────────────────────
def _next_run_dir() -> Path:
    """Return the next numbered run directory, e.g. models/run_3/"""
    n = 1
    while (MODEL_BASE_DIR / f"run_{n}").exists():
        n += 1
    run_dir = MODEL_BASE_DIR / f"run_{n}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _next_results_path() -> Path:
    """Return a numbered results Excel path that does not yet exist."""
    n = 1
    while (RESULTS_DIR / f"intent_classifier_results_run_{n}.xlsx").exists():
        n += 1
    return RESULTS_DIR / f"intent_classifier_results_run_{n}.xlsx"


# ── data ───────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    df = pd.read_excel(TEST_CASES_PATH, sheet_name=TEST_SHEET)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df[df["question"].notna() & df["keyword"].notna()].copy()
    df["question"] = df["question"].astype(str).str.strip()
    df["keyword"]  = df["keyword"].astype(str).str.strip()
    return df


def split_data(df: pd.DataFrame):
    X = df["question"].tolist()
    y = df["keyword"].tolist()
    return train_test_split(X, y, test_size=TEST_SIZE,
                            random_state=RANDOM_STATE, stratify=y)


# ── Model A: Sentence Transformer + Nearest Centroid ──────────────────────────
def train_sentence_transformer(X_train, y_train):
    from sentence_transformers import SentenceTransformer
    print(f"\n{BOLD}Model A — Sentence Transformer + Nearest Centroid{RESET}")
    print(f"  Embedding model : {EMBED_MODEL}")
    st = SentenceTransformer(EMBED_MODEL)
    print(f"  Embedding {len(X_train)} training questions…")
    X_emb = st.encode(X_train, show_progress_bar=True, batch_size=EMBED_BATCH)
    clf = NearestCentroid()
    clf.fit(X_emb, y_train)
    return st, clf

def predict_st(st, clf, X):
    return clf.predict(st.encode(X, show_progress_bar=False, batch_size=EMBED_BATCH))


# ── Model B: TF-IDF + Linear SVM ──────────────────────────────────────────────
def train_tfidf_svm(X_train, y_train):
    print(f"\n{BOLD}Model B — TF-IDF + Linear SVM{RESET}")
    print(f"  C={SVM_C}  ngram={TFIDF_NGRAM}  min_df={TFIDF_MIN_DF}")
    vec = TfidfVectorizer(ngram_range=TFIDF_NGRAM, min_df=TFIDF_MIN_DF,
                          sublinear_tf=True, strip_accents="unicode")
    clf = LinearSVC(C=SVM_C, max_iter=SVM_ITER)
    clf.fit(vec.fit_transform(X_train), y_train)
    return vec, clf

def predict_svm(vec, clf, X):
    return clf.predict(vec.transform(X))


# ── Model C: TF-IDF + Random Forest ───────────────────────────────────────────
def train_random_forest(X_train, y_train):
    print(f"\n{BOLD}Model C — TF-IDF + Random Forest{RESET}")
    print(f"  n_estimators={RF_TREES}  max_depth={RF_MAX_DEPTH}  min_samples_leaf={RF_MIN_LEAF}")
    vec = TfidfVectorizer(ngram_range=TFIDF_NGRAM, min_df=TFIDF_MIN_DF,
                          sublinear_tf=True, strip_accents="unicode")
    clf = RandomForestClassifier(n_estimators=RF_TREES, max_depth=RF_MAX_DEPTH,
                                 min_samples_leaf=RF_MIN_LEAF,
                                 random_state=RANDOM_STATE, n_jobs=-1)
    clf.fit(vec.fit_transform(X_train), y_train)
    return vec, clf

def predict_rf(vec, clf, X):
    return clf.predict(vec.transform(X))


# ── Model D: NLU Engine (production model used as reference) ──────────────────
def predict_nlu_engine(X_test: list[str], knowledge_df: pd.DataFrame) -> list[str]:
    """
    Runs predictions through the same NLU engine the chatbot uses in production.
    Presented as 'NLU Engine Classifier' — the internal implementation is nlu_core.
    """
    print(f"\n{BOLD}Model D — NLU Engine Classifier{RESET}")
    print(f"  Running {len(X_test)} questions through the production engine…")
    try:
        from nlu_core import engine_match
    except ImportError:
        print(f"  {YELLOW}nlu_core not available — skipping Model D{RESET}")
        return []

    preds = []
    for i, q in enumerate(X_test, 1):
        try:
            match, _, _ = engine_match(
                user_question=q,
                knowledge_df=knowledge_df,
                provider=PROVIDER_PRIMARY,
                conversation_summary="",
            )
            preds.append(str(match) if match and match != "NO_MATCH" else "")
        except Exception:
            preds.append("")
        if i % 10 == 0:
            print(f"  {i}/{len(X_test)}…", end="\r")
    print(f"  Done — {len(X_test)}/{len(X_test)}   ")
    return preds


# ── evaluation ─────────────────────────────────────────────────────────────────
def _pass_rate(y_true: list[str], y_pred: list[str]) -> float:
    def words(s: str) -> set[str]:
        return {w for w in str(s).lower().split() if len(w) > 2}
    correct = 0
    for exp, got in zip(y_true, y_pred):
        got = str(got) if got is not None else ""
        if not got or got.upper() in ("NO_MATCH", "PRODUCT_ENQUIRE", "TICKET_LOGGED", "NAN"):
            continue
        if str(exp).strip().lower() == got.strip().lower():
            correct += 1
            continue
        e, g = words(exp), words(got)
        if e and len(e & g) / len(e) >= 0.4:
            correct += 1
    return correct / len(y_true) * 100 if y_true else 0.0


def print_report(name: str, y_true, y_pred, colour: str) -> float:
    preds = [str(p) if p is not None else "" for p in y_pred]
    acc   = accuracy_score(y_true, preds) * 100
    prate = _pass_rate(list(y_true), preds)
    print(f"\n{BOLD}{name}{RESET}")
    print(f"  Exact accuracy   : {_c(colour, f'{acc:.1f}%')}")
    print(f"  40%-word-overlap : {_c(colour, f'{prate:.1f}%')}  ← same scoring as KB test runner")
    return prate


# ── save models ────────────────────────────────────────────────────────────────
def save_models(run_dir: Path, st, st_clf, svm_vec, svm_clf, rf_vec, rf_clf,
                params: dict) -> None:
    st.save(str(run_dir / "st_model"))
    joblib.dump(st_clf,  run_dir / "st_centroid.joblib")
    joblib.dump(svm_vec, run_dir / "svm_vectorizer.joblib")
    joblib.dump(svm_clf, run_dir / "svm_clf.joblib")
    joblib.dump(rf_vec,  run_dir / "rf_vectorizer.joblib")
    joblib.dump(rf_clf,  run_dir / "rf_clf.joblib")
    # save parameters used for this run so you can reproduce it later
    pd.DataFrame([params]).to_csv(run_dir / "params.csv", index=False)
    print(f"\nModels saved → {run_dir}/")


# ── main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-nlu", action="store_true",
                        help="Skip Model D (NLU engine) — faster, no API calls")
    args = parser.parse_args()

    # ── load & split ──────────────────────────────────────────────────────────
    print(f"\n{BOLD}Loading dataset…{RESET}")
    df = load_data()
    print(f"  Total samples    : {len(df)}")
    print(f"  Unique keywords  : {df['keyword'].nunique()}")

    X_train, X_test, y_train, y_test = split_data(df)
    print(f"  Train            : {len(X_train)} samples ({100-int(TEST_SIZE*100)}%)")
    print(f"  Test             : {len(X_test)} samples ({int(TEST_SIZE*100)}%)")

    # ── train ─────────────────────────────────────────────────────────────────
    st,      st_clf  = train_sentence_transformer(X_train, y_train)
    svm_vec, svm_clf = train_tfidf_svm(X_train, y_train)
    rf_vec,  rf_clf  = train_random_forest(X_train, y_train)

    # ── predict ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}Evaluating on {len(X_test)} held-out questions…{RESET}")
    st_preds   = predict_st(st, st_clf, X_test)
    svm_preds  = predict_svm(svm_vec, svm_clf, X_test)
    rf_preds   = predict_rf(rf_vec, rf_clf, X_test)

    nlu_preds: list[str] = []
    if not args.skip_nlu:
        nlu_preds = predict_nlu_engine(X_test, df)

    # ── results ───────────────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print(f"{BOLD}RESULTS — {len(X_test)} held-out test questions{RESET}")
    print(f"{'═' * 65}")

    st_rate  = print_report("Model A  —  Sentence Transformer + Nearest Centroid", y_test, st_preds,  GREEN)
    svm_rate = print_report("Model B  —  TF-IDF + Linear SVM",                    y_test, svm_preds, CYAN)
    rf_rate  = print_report("Model C  —  TF-IDF + Random Forest",                 y_test, rf_preds,  YELLOW)

    nlu_rate: float | None = None
    if nlu_preds:
        nlu_rate = print_report("Model D  —  NLU Engine Classifier",              y_test, nlu_preds, GREEN)

    # ── comparison table ──────────────────────────────────────────────────────
    all_rates = [st_rate, svm_rate, rf_rate] + ([nlu_rate] if nlu_rate is not None else [])
    winner    = max(all_rates)

    print(f"\n{'═' * 65}")
    print(f"{BOLD}COMPARISON SUMMARY{RESET}")
    print(f"{'─' * 65}")
    print(f"  {'Model':<48}  {'Pass rate':>9}")
    print(f"  {'─'*48}  {'─'*9}")
    rows = [
        ("Sentence Transformer + Nearest Centroid (Model A)", st_rate),
        ("TF-IDF + Linear SVM                      (Model B)", svm_rate),
        ("TF-IDF + Random Forest                   (Model C)", rf_rate),
    ]
    if nlu_rate is not None:
        rows.append(("NLU Engine Classifier                    (Model D)", nlu_rate))
    for name, rate in rows:
        marker = "  ← BEST" if rate == winner else ""
        colour = GREEN if rate == winner else RESET
        print(f"  {_c(colour, f'{name:<48}  {rate:>8.1f}%')}{_c(GREEN, marker)}")
    print(f"{'═' * 65}")

    # ── per-keyword breakdown (worst 10, Model A) ─────────────────────────────
    results_df = pd.DataFrame({
        "question":   X_test,
        "expected":   y_test,
        "model_a":    list(st_preds),
        "model_b":    list(svm_preds),
        "model_c":    list(rf_preds),
        "model_d":    nlu_preds if nlu_preds else [""] * len(X_test),
    })
    results_df["pass_a"] = results_df.apply(
        lambda r: _pass_rate([r["expected"]], [r["model_a"]]) > 0, axis=1)

    print(f"\n{BOLD}Worst-performing keywords — Model A (Sentence Transformer):{RESET}")
    kw_stats = results_df.groupby("expected")["pass_a"].agg(["sum", "count"])
    kw_stats["rate"] = kw_stats["sum"] / kw_stats["count"] * 100
    for kw, row in kw_stats.sort_values("rate").head(10).iterrows():
        rate  = row["rate"]
        cor   = int(row["sum"])
        total = int(row["count"])
        colour = GREEN if rate >= 80 else (YELLOW if rate >= 50 else RED)
        print(f"  {_c(colour, f'{rate:5.0f}%')}  {_bar(rate)}  [{cor}✓/{total}]  {str(kw)[:60]}")

    # ── save models ───────────────────────────────────────────────────────────
    run_dir = _next_run_dir()
    params = {
        "TEST_SIZE": TEST_SIZE, "RANDOM_STATE": RANDOM_STATE,
        "EMBED_MODEL": EMBED_MODEL, "EMBED_BATCH": EMBED_BATCH,
        "SVM_C": SVM_C, "SVM_ITER": SVM_ITER,
        "RF_TREES": RF_TREES, "RF_MAX_DEPTH": RF_MAX_DEPTH, "RF_MIN_LEAF": RF_MIN_LEAF,
        "TFIDF_NGRAM": str(TFIDF_NGRAM), "TFIDF_MIN_DF": TFIDF_MIN_DF,
        "model_a_pass_rate": st_rate, "model_b_pass_rate": svm_rate,
        "model_c_pass_rate": rf_rate,
        **({"model_d_pass_rate": nlu_rate} if nlu_rate is not None else {}),
    }
    save_models(run_dir, st, st_clf, svm_vec, svm_clf, rf_vec, rf_clf, params)

    # ── save results to numbered Excel ────────────────────────────────────────
    out_path = _next_results_path()
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        results_df.to_excel(writer, sheet_name="Predictions", index=False)
        kw_stats.reset_index().rename(columns={
            "expected": "keyword", "sum": "correct",
            "count": "total", "rate": "pass_rate_%",
        }).sort_values("pass_rate_%").to_excel(writer, sheet_name="Per-Keyword", index=False)
        pd.DataFrame([params]).to_excel(writer, sheet_name="Parameters", index=False)
    print(f"Results saved  → {out_path.name}")
    print(f"\nTo compare runs:  ls models/  →  run_1/, run_2/, …  each has params.csv\n")


if __name__ == "__main__":
    main()
