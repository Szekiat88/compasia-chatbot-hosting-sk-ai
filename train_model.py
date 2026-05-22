"""
Train a RandomForest intent classifier using data/Samples.xlsx.

Sources used:
  - Main DB sheet  : keyword + action columns → labelled examples
  - User Questions : real user questions → anchor token maps to intent

Run:
    python train_model.py

Outputs (models/):
    model.pkl           RandomForest classifier
    label_encoder.pkl   sklearn LabelEncoder for intent classes
    feature_config.json keyword lists & class names (read by model_engine.py)
"""

import json
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# ---------------------------------------------------------------------------
# Intent labels
# ---------------------------------------------------------------------------
PRODUCT_ENQUIRE = "PRODUCT_ENQUIRE"
ORDER_STATUS    = "ORDER_STATUS"
ESCALATION      = "ESCALATION"
FAQ             = "FAQ"
NO_MATCH        = "NO_MATCH"

# ---------------------------------------------------------------------------
# Keyword → intent mapping for Main DB rows
# (matched by substring in the keyword text, lowercased)
# ---------------------------------------------------------------------------
KEYWORD_INTENT_MAP = {
    # ORDER_STATUS
    "outstanding order":             ORDER_STATUS,
    "shipment overdue":              ORDER_STATUS,
    "tracking number":               ORDER_STATUS,
    "did not receive shipping":      ORDER_STATUS,
    "change shipping address":       ORDER_STATUS,
    "delivery status":               ORDER_STATUS,
    "application status":            ORDER_STATUS,

    # ESCALATION
    "something wrong with device":   ESCALATION,
    "changing/ update personal":     ESCALATION,
    "device has issues":             ESCALATION,
    "early termination":             ESCALATION,
    "renewngo customer service":     ESCALATION,
    "warranty claim process":        ESCALATION,
    "shipment arrived damage":       ESCALATION,
    "received wrong item":           ESCALATION,
    "check remaining device swap":   ESCALATION,
    "changing personal details":     ESCALATION,
    "clearing outstanding payments": ESCALATION,
    "billing/payment details":       ESCALATION,
    "change address":                ESCALATION,
    "selling defective":             ESCALATION,

    # PRODUCT_ENQUIRE
    "product enquiry":               PRODUCT_ENQUIRE,
    "stock availability":            PRODUCT_ENQUIRE,
    "certified second-hand":         PRODUCT_ENQUIRE,
    "differences between refurbished": PRODUCT_ENQUIRE,
    "where the devices are from":    PRODUCT_ENQUIRE,
    "pictures of device before":     PRODUCT_ENQUIRE,
    "imei number":                   PRODUCT_ENQUIRE,
    "devices offered in renewngo":   PRODUCT_ENQUIRE,

    # NO_MATCH
    "no complete inquiry":           NO_MATCH,
}
# Anything not matched above defaults to FAQ
DEFAULT_INTENT = FAQ

# ---------------------------------------------------------------------------
# Anchor token → intent (for User Questions sheet)
# ---------------------------------------------------------------------------
ANCHOR_INTENT_MAP = {
    "renewngo program":                       FAQ,
    "required documents for renewngo":        FAQ,
    "store location":                         FAQ,
    "renewngo upfront payment":               FAQ,
    "where the devices are from":             FAQ,
    "repair services":                        FAQ,
    "no complete inquiry":                    NO_MATCH,
    "product enquiry":                        PRODUCT_ENQUIRE,
    "stock availability":                     PRODUCT_ENQUIRE,
    "delivery status":                        ORDER_STATUS,
    "tracking number":                        ORDER_STATUS,
    "warranty claim process":                 ESCALATION,
    "shipment overdue":                       ORDER_STATUS,
}

# ---------------------------------------------------------------------------
# Keywords used as features (derived from real domain vocabulary)
# ---------------------------------------------------------------------------
PRODUCT_KW = [
    "phone","iphone","samsung","galaxy","pixel","oppo","vivo","xiaomi","device",
    "refurbished","grade","condition","colour","color","storage","gb","buy","price",
    "harga","murah","recommend","budget","android","spec","cari","nak","tengok",
    "bawah","available","256","128","stock","availability","second-hand","secondhand",
    "certified","imei","model","variant","specification","new stock","brand",
]
ORDER_KW = [
    "order","delivery","shipment","track","parcel","arrive","received","pending",
    "shipped","purchase","tracking","processing","ca","hantar","sampai","bayar",
    "package","status","update","late","outstanding","fulfil","fulfillment",
    "shipping address","wrong item","damage","overdue","address",
]
ESCALATE_KW = [
    "angry","refund","terrible","unacceptable","fraud","complaint","cancel",
    "manager","supervisor","human","agent","staff","report","aduan","kecewa",
    "disappointed","broke","social","media","rights","consumer","urgent",
    "billing","outstanding payment","device issue","wrong device","early termination",
    "repair","personal details","swap","claim","damaged",
]
FAQ_KW = [
    "warranty","return","payment","policy","credit","card","installment","instalment",
    "pay","exchange","hours","ship","sabah","sarawak","located","store","support",
    "contact","shipping","free","minimum","east","boleh","paylater","spaylater",
    "renewngo","replace plus","terms","conditions","eligibility","fee","subscription",
    "privacy","account","login","password","newsletter","location","promotion",
    "monthly","upfront","sign up","apply","application","program","plan",
]
GREET_KW = [
    "hello","hi","bye","thanks","thank","ok","yes","no","good","morning",
    "alright","noted","great","hmm","test","evening","night","helo","hai",
]


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
FEATURE_NAMES = [
    "word_count","char_count","avg_word_len",
    "product_score","order_score","escalate_score",
    "faq_score","greet_score",
    "has_question_mark","has_digit","has_ca_prefix","long_word_ratio",
]


def featurize(text: str) -> list:
    t = text.lower()
    words = t.split()
    wc = len(words) or 1
    cc = len(t)

    def _hit(kws):
        return sum(1 for k in kws if k in t) / max(len(kws), 1)

    return [
        wc,
        cc,
        cc / wc,
        _hit(PRODUCT_KW),
        _hit(ORDER_KW),
        _hit(ESCALATE_KW),
        _hit(FAQ_KW),
        _hit(GREET_KW),
        int("?" in t),
        int(any(c.isdigit() for c in t)),
        int("ca" in words),
        sum(1 for w in words if len(w) > 7) / wc,
    ]


# ---------------------------------------------------------------------------
# Build labelled corpus from Samples.xlsx
# ---------------------------------------------------------------------------
def load_corpus(excel_path: str) -> list[tuple[str, str]]:
    xl = pd.ExcelFile(excel_path)
    corpus: list[tuple[str, str]] = []

    # ── Main DB: keyword text as training example ─────────────────────────
    df_main = xl.parse("Main DB")
    for _, row in df_main.iterrows():
        raw_kw = str(row.get("keyword", "")).strip()
        if not raw_kw or raw_kw == "nan":
            continue

        # Clean multi-line keyword cells — split on / and newlines
        parts = re.split(r"[/\n]", raw_kw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]

        # Determine intent
        kw_lower = raw_kw.lower()
        intent = DEFAULT_INTENT
        for pattern, mapped in KEYWORD_INTENT_MAP.items():
            if pattern in kw_lower:
                intent = mapped
                break

        for p in parts:
            corpus.append((intent, p))

    # ── User Questions: real user queries ────────────────────────────────
    df_uq = xl.parse("User Questions")
    for _, row in df_uq.iterrows():
        question = str(row.get("User Question", "")).strip()
        anchor   = str(row.get("Anchor Token", "")).strip().lower()
        if not question or question == "nan":
            continue

        intent = FAQ  # default
        for pattern, mapped in ANCHOR_INTENT_MAP.items():
            if pattern in anchor:
                intent = mapped
                break

        corpus.append((intent, question))

    # ── Extra hand-crafted examples to balance sparse classes ────────────
    extras = [
        (PRODUCT_ENQUIRE, "what iphone models do you have"),
        (PRODUCT_ENQUIRE, "do you sell samsung galaxy"),
        (PRODUCT_ENQUIRE, "how much is iphone 13 pro"),
        (PRODUCT_ENQUIRE, "any iphone 14 under 2000"),
        (PRODUCT_ENQUIRE, "nak tengok phone bawah 1500"),
        (PRODUCT_ENQUIRE, "ada tak samsung galaxy s23"),
        (PRODUCT_ENQUIRE, "show me phones in grade a condition"),
        (PRODUCT_ENQUIRE, "looking for refurbished android budget phone"),
        (ORDER_STATUS, "where is my order"),
        (ORDER_STATUS, "when will my phone arrive"),
        (ORDER_STATUS, "my order CA123456 not received yet"),
        (ORDER_STATUS, "sudah bayar tapi belum hantar"),
        (ORDER_STATUS, "bila order sampai east malaysia"),
        (ORDER_STATUS, "parcel not delivered after 7 days"),
        (ESCALATION, "i want to speak to a human now"),
        (ESCALATION, "this is unacceptable i want refund"),
        (ESCALATION, "saya nak buat aduan sekarang"),
        (ESCALATION, "my device broke after 2 days very disappointed"),
        (NO_MATCH, "ok"),
        (NO_MATCH, "thank you"),
        (NO_MATCH, "hello"),
        (NO_MATCH, "hi there"),
        (NO_MATCH, "bye"),
        (NO_MATCH, "yes noted"),
    ]
    corpus.extend(extras)

    return corpus


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------
def main():
    excel_path = Path("data/Samples.xlsx")
    out_dir    = Path("models")
    out_dir.mkdir(exist_ok=True)

    print(f"Loading corpus from {excel_path} …")
    corpus = load_corpus(str(excel_path))

    # Print class distribution
    from collections import Counter
    dist = Counter(label for label, _ in corpus)
    print(f"Total examples: {len(corpus)}")
    for cls, cnt in sorted(dist.items()):
        print(f"  {cls:20s} {cnt}")

    labels, texts = zip(*corpus)

    le = LabelEncoder()
    y  = le.fit_transform(labels)
    X  = np.array([featurize(t) for t in texts])

    # Stratify where possible (need ≥ n_classes samples per class)
    min_class_size = min(dist.values())
    stratify = y if min_class_size >= 2 else None

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=stratify
    )

    clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=1,
        class_weight="balanced",
        random_state=42,
    )
    clf.fit(X_train, y_train)

    preds = clf.predict(X_test)
    print("\nClassification report:")
    print(classification_report(y_test, preds, target_names=le.classes_))

    # Save artifacts
    with open(out_dir / "model.pkl", "wb") as f:
        pickle.dump(clf, f)
    with open(out_dir / "label_encoder.pkl", "wb") as f:
        pickle.dump(le, f)

    config = {
        "features":    FEATURE_NAMES,
        "classes":     list(le.classes_),
        "product_kw":  PRODUCT_KW,
        "order_kw":    ORDER_KW,
        "escalate_kw": ESCALATE_KW,
        "faq_kw":      FAQ_KW,
        "greet_kw":    GREET_KW,
    }
    with open(out_dir / "feature_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nSaved → {out_dir}/model.pkl  label_encoder.pkl  feature_config.json")

    # Quick sanity check on a few phrases
    print("\nSanity checks:")
    tests = [
        "do you have iphone 14 pro",
        "where is my order CA12345",
        "i want to make a complaint",
        "what is the warranty period",
        "what is renewngo program",
        "hello ok thanks",
    ]
    for txt in tests:
        x    = np.array([featurize(txt)])
        prob = clf.predict_proba(x)[0]
        idx  = int(np.argmax(prob))
        lbl  = le.inverse_transform([idx])[0]
        print(f"  {prob[idx]:.2f}  {lbl:20s}  {txt}")


if __name__ == "__main__":
    main()
