"""One-time Supabase setup: create the tables and the file bucket.

Run from the my_portfolio folder after filling in .env:

    venv\\Scripts\\python setup_supabase.py

Safe to run again: it only creates what is missing.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from db import get_db_connection

load_dotenv()

# Matches the largest upload the app allows (project media: 16 MB).
BUCKET_SETTINGS = {
    "public": True,
    "file_size_limit": 16 * 1024 * 1024,
    "allowed_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "video/mp4", "video/webm", "video/quicktime",
        "application/pdf"
    ]
}


def create_tables():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    connection = get_db_connection()
    try:
        connection.execute(schema)
        connection.commit()
    finally:
        connection.close()
    print("Tables ready: admins, projects, project_media, profile, certificates, messages")


def create_bucket():
    project_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = os.getenv("SUPABASE_BUCKET", "portfolio-media")
    if not project_url or not service_key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")

    headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
    base = f"{project_url}/storage/v1/bucket"

    existing = requests.get(f"{base}/{bucket}", headers=headers, timeout=30)
    if existing.status_code == 200:
        response = requests.put(
            f"{base}/{bucket}", headers=headers, timeout=30,
            json=dict(BUCKET_SETTINGS, id=bucket, name=bucket)
        )
        action = "updated"
    else:
        response = requests.post(
            base, headers=headers, timeout=30,
            json=dict(BUCKET_SETTINGS, id=bucket, name=bucket)
        )
        action = "created"

    if response.status_code >= 300:
        sys.exit(f"Could not set up bucket '{bucket}': {response.status_code} {response.text}")
    print(f"Storage bucket '{bucket}' {action} (public, max 16 MB, images/videos/PDFs)")


if __name__ == "__main__":
    create_tables()
    create_bucket()
    print("Supabase is ready.")
