"""Retrain the scam classifier with admin-approved feedback and register the new version."""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR))
from ml_core import build_matrix, feature_names  # noqa: E402

ROOT = ML_DIR.parent
CSV_PATH = ML_DIR / "data" / "fake_job_postings.csv"
DB_PATH = ROOT / "backend" / "app.db"
MODELS_DIR = ML_DIR / "models"
VERSIONS_DIR = MODELS_DIR / "versions"
ACTIVE_PATH = MODELS_DIR / "model_bundle.joblib"
REGISTRY_PATH = MODELS_DIR / "registry.json"
TEXT_COLS = ["title", "company_profile", "description", "requirements", "benefits"]
FEEDBACK_WEIGHT = 5.0
TOLERANCE = 0.005


def load_feedback():
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("SELECT posting_text, user_label FROM feedback WHERE status = 'approved' ORDER BY id").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    latest = {}
    for text, label in rows:
        latest[text.strip()] = 1 if label == "scam" else 0
    return list(latest.items())


def evaluate(bundle, texts, y_true):
    X = build_matrix(texts, bundle["vectorizer"])
    prob = bundle["model"].predict_proba(X)[:, 1]
    pred = (prob >= 0.5).astype(int)
    return {
        "precision": round(float(precision_score(y_true, pred)), 3),
        "recall": round(float(recall_score(y_true, pred)), 3),
        "f1": round(float(f1_score(y_true, pred)), 3),
        "pr_auc": round(float(average_precision_score(y_true, prob)), 3),
    }


def load_registry():
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {"active_version": None, "versions": []}


def save_registry(reg):
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-feedback", type=int, default=5, help="minimum approved feedback rows needed")
    args = parser.parse_args()

    feedback = load_feedback()
    print(f"Approved feedback rows: {len(feedback)}")
    if len(feedback) < args.min_feedback:
        print(f"Need at least {args.min_feedback} approved feedback rows. Approve more in the admin dashboard, or lower --min-feedback.")
        sys.exit(1)

    df = pd.read_csv(CSV_PATH)
    df["text"] = df[TEXT_COLS].fillna("").agg(" ".join, axis=1)
    y = df["fraudulent"].values
    X_train, X_test, y_train, y_test = train_test_split(df["text"], y, test_size=0.2, stratify=y, random_state=42)

    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    active = joblib.load(ACTIVE_PATH)
    print("Evaluating the current model on the hold-out set...")
    current_metrics = evaluate(active, X_test, y_test)

    if not registry["versions"]:
        shutil.copy(ACTIVE_PATH, VERSIONS_DIR / "model_v1.joblib")
        registry["versions"].append({
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trained_on_feedback": 0,
            "train_rows": int(len(X_train)),
            "metrics": current_metrics,
            "promoted": True,
            "note": "Initial model trained in Colab",
        })
        registry["active_version"] = 1
        save_registry(registry)

    fb_texts = [t for t, _ in feedback]
    fb_y = np.array([lab for _, lab in feedback])
    train_texts = list(X_train) + fb_texts
    train_y = np.concatenate([y_train, fb_y])
    weights = np.concatenate([np.ones(len(y_train)), np.full(len(fb_y), FEEDBACK_WEIGHT)])

    print(f"Training a new model on {len(train_texts)} rows ({len(fb_texts)} from feedback). This takes a few minutes...")
    vec = TfidfVectorizer(max_features=5000, ngram_range=(1, 2), stop_words="english", min_df=3)
    vec.fit(train_texts)
    X_tr = build_matrix(train_texts, vec)
    spw = float((train_y == 0).sum() / max((train_y == 1).sum(), 1))
    model = XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1, scale_pos_weight=spw, eval_metric="aucpr", tree_method="hist", random_state=42)
    model.fit(X_tr, train_y, sample_weight=weights)
    new_bundle = {"vectorizer": vec, "model": model, "names": feature_names(vec)}
    new_metrics = evaluate(new_bundle, X_test, y_test)

    version = max(v["version"] for v in registry["versions"]) + 1
    path = VERSIONS_DIR / f"model_v{version}.joblib"
    joblib.dump(new_bundle, path)

    promote = new_metrics["pr_auc"] >= current_metrics["pr_auc"] - TOLERANCE
    registry["versions"].append({
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trained_on_feedback": len(fb_texts),
        "train_rows": len(train_texts),
        "metrics": new_metrics,
        "promoted": bool(promote),
        "note": "Promoted: PR-AUC not worse than the active model" if promote else "Not promoted: PR-AUC dropped below the active model",
    })
    if promote:
        shutil.copy(path, ACTIVE_PATH)
        registry["active_version"] = version
    save_registry(registry)

    print("")
    print("Hold-out results (same split as the original notebook)")
    print(f"  current model : {current_metrics}")
    print(f"  new model v{version}: {new_metrics}")
    if promote:
        print(f"PROMOTED: v{version} is now the active model. Restart the backend to load it.")
    else:
        print(f"NOT PROMOTED: v{version} was saved but the active model is unchanged.")


if __name__ == "__main__":
    main()
