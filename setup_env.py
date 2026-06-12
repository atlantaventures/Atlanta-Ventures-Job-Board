"""
Decode GOOGLE_CREDENTIALS_JSON (base64) → config/google_credentials.json.
Run before any script that needs Google Sheets access.
No-ops if the file already exists (i.e. local dev with the real file).
"""
import base64
import os
from pathlib import Path

creds_path = Path(__file__).parent / "config" / "google_credentials.json"
if not creds_path.exists():
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_b64:
        raise RuntimeError(
            "config/google_credentials.json is missing and "
            "GOOGLE_CREDENTIALS_JSON env var is not set"
        )
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(base64.b64decode(creds_b64).decode())
    print(f"Wrote Google credentials to {creds_path}")
