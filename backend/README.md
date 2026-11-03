# Fake-Job Detector — Backend

FastAPI server that exposes a fraud-detection API backed by an XGBoost model.

## Prerequisites

- Python 3.10+
- The project virtual environment lives at `../venv/` (or create a fresh one).

## Install dependencies

```bash
# From the backend/ directory
pip install -r requirements.txt
```

## Run the development server

```bash
# From the backend/ directory
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at **http://localhost:8000**.  
Interactive docs: **http://localhost:8000/docs**

## Endpoints

| Method | Path        | Description                                              |
|--------|-------------|----------------------------------------------------------|
| GET    | `/health`   | Liveness probe → `{"status": "ok"}`                     |
| POST   | `/predict`  | Analyse a job posting → `{risk_score, verdict, reasons}` |
| POST   | `/feedback` | Save a user label (`genuine` / `scam`) for a posting     |
| GET    | `/postings` | Recent high-risk predictions (`risk_score >= 60`)        |

### `/postings` query parameters

| Param   | Default | Max | Description              |
|---------|---------|-----|--------------------------|
| `limit` | 50      | 500 | Number of rows to return |

## Database

SQLite file is created automatically at `backend/app.db` on first startup.

## CORS

Requests from `http://localhost:3000` (React) and `http://localhost:8501` (Streamlit) are allowed.
