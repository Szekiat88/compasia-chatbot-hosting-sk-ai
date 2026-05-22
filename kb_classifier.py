"""
kb_classifier.py
================
Inference wrapper for the trained Random Forest KB intent classifier.

Exposes two public functions:

    predict(question)          -> (keyword, confidence) or (None, 0.0)
    is_model_available()       -> bool

If the model files are present and the question scores above CONFIDENCE_THRESHOLD
the RF answer is returned instantly (no API call).  Below the threshold the caller
should fall back to the LLM engine.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

CONFIDENCE_THRESHOLD = 0.45

_BASE_DIR   = Path(__file__).resolve().parent
_MODEL_DIR  = _BASE_DIR / "models" / "kb_rf"
_RF_PATH    = _MODEL_DIR / "rf_model.joblib"
_VEC_PATH   = _MODEL_DIR / "tfidf_vectorizer.joblib"
_LE_PATH    = _MODEL_DIR / "label_encoder.joblib"

_vectorizer = None
_le         = None
_clf        = None


def is_model_available() -> bool:
    return _RF_PATH.exists() and _VEC_PATH.exists() and _LE_PATH.exists()


def _load() -> None:
    global _vectorizer, _le, _clf
    if _clf is not None:
        return
    import joblib
    _vectorizer = joblib.load(_VEC_PATH)
    _le         = joblib.load(_LE_PATH)
    _clf        = joblib.load(_RF_PATH)


def predict(question: str) -> Tuple[Optional[str], float]:
    """
    Returns (keyword, confidence) when the RF is confident, else (None, 0.0).

    The caller should fall back to the LLM intent engine whenever None is returned.
    """
    if not is_model_available():
        return None, 0.0

    _load()

    x       = _vectorizer.transform([question.strip()])
    proba   = _clf.predict_proba(x)[0]
    top_idx = int(np.argmax(proba))
    conf    = float(proba[top_idx])

    if conf < CONFIDENCE_THRESHOLD:
        return None, conf

    keyword = str(_le.inverse_transform([top_idx])[0])
    return keyword, conf
