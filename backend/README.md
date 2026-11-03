# Fake-Job Detector — Backend

FastAPI server that exposes a fraud-detection API backed by an XGBoost model,
rule-based red-flag detection, and JWT-authenticated user accounts.

## Prerequisites

- Python 3.10+
- The project virtual environment lives at `../venv/` (or create a fresh one).

## Install dependencies

```bash
# From the backend/ directory
pip install -r requirements.txt
```

## Environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable         | Required | Description |
|------------------|----------|-------------|
| `JWT_SECRET`     | Recommended | 32-byte hex secret for signing JWTs. If unset, a random one is generated each restart (all tokens invalidated on restart). Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_EMAIL`    | Optional | If set (with `ADMIN_PASSWORD`), an admin account is created on first startup if no admin exists yet. |
| `ADMIN_PASSWORD` | Optional | Password for the bootstrapped admin (min 8 chars, max 72 bytes UTF-8). |

## Run the development server

```bash
# From the backend/ directory
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at **http://localhost:8000**.  
Interactive docs (with Authorize button): **http://localhost:8000/docs**

## Reset the database

The SQLite file is created automatically at `backend/app.db`.  
If you need to reset it after a schema change, run:

```bash
rm backend/app.db
```

Then restart the server — tables will be recreated automatically.

## Endpoints

### Auth (public)

| Method | Path             | Description |
|--------|------------------|-------------|
| POST   | `/auth/register` | Create a new account (`email`, `password`). Returns 409 if email exists. |
| POST   | `/auth/login`    | OAuth2 form login (`username`=email, `password`). Returns `access_token`. |

### Inference (🔒 login required)

| Method | Path       | Description |
|--------|------------|-------------|
| POST   | `/predict` | Analyse a job posting → `{risk_score, verdict, reasons}` |

### Feedback (🔒 login required)

| Method | Path        | Description |
|--------|-------------|-------------|
| POST   | `/feedback` | Save a user label (`genuine` / `scam`) for a posting. Status defaults to `pending`. |

### Postings (public)

| Method | Path        | Description |
|--------|-------------|-------------|
| GET    | `/postings` | Recent high-risk predictions (`risk_score >= 60`), newest first. |

#### `/postings` query parameters

| Param    | Default | Range   | Description |
|----------|---------|---------|-------------|
| `q`      | —       | —       | Keyword search over posting text and reasons (case-insensitive) |
| `limit`  | 50      | 1–500   | Max rows to return |
| `offset` | 0       | 0+      | Pagination offset |

### Utility (public)

| Method | Path      | Description |
|--------|-----------|-------------|
| GET    | `/health` | Liveness probe → `{"status": "ok"}` |

## Authentication flow

1. `POST /auth/register` → create account  
2. `POST /auth/login` (form: `username`, `password`) → receive `access_token`  
3. Add `Authorization: Bearer <token>` header to protected requests  
4. Token is valid for **8 hours**

In Swagger (`/docs`), click the **Authorize** button and enter your credentials.

## CORS

Requests from `http://localhost:3000` (React) and `http://localhost:8501` (Streamlit) are allowed.
