"""One-time copy of the XAMPP (MySQL) portfolio into Supabase.

Start MySQL in the XAMPP Control Panel, fill in .env, run
setup_supabase.py once, then:

    venv\\Scripts\\python migrate_from_mysql.py

Copies every table (keeping the same ids) and uploads the files in
static/uploads that the data refers to. It refuses to run if Supabase
already has projects or messages, so it cannot copy anything twice.
Plain-text admin passwords are hashed on the way.

Requires: pip install mysql-connector-python (only for this script).
"""
import mimetypes
import os
import sys
from pathlib import Path

import mysql.connector
import requests
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from db import get_db_connection

load_dotenv()

MYSQL = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "portfolio_system"),
}
UPLOADS = Path(__file__).with_name("static") / "uploads"
HASH_PREFIXES = ("scrypt:", "pbkdf2:")

# Supabase table -> columns copied from the MySQL table of the same name.
TABLES = {
    "admins": ["id", "username", "password"],
    "projects": ["id", "title", "description", "technologies", "project_link", "created_at"],
    "project_media": ["id", "project_id", "filename", "position", "created_at"],
    "profile": ["id", "hero_name", "hero_intro", "hero_picture",
                "description", "picture", "skills", "updated_at"],
    "certificates": ["id", "title", "description", "photo", "obtained_at", "created_at"],
    "messages": ["id", "name", "email", "message", "created_at"],
}


def read_mysql():
    connection = mysql.connector.connect(**MYSQL)
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SHOW TABLES")
    present = {list(row.values())[0] for row in cursor.fetchall()}

    data = {}
    for table, columns in TABLES.items():
        if table not in present:
            data[table] = []
            continue
        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        available = {row["Field"] for row in cursor.fetchall()}
        wanted = [column for column in columns if column in available]
        cursor.execute(f"SELECT {', '.join(f'`{c}`' for c in wanted)} FROM `{table}` ORDER BY id")
        data[table] = cursor.fetchall()

    # Very old projects kept one file in projects.media.
    if "projects" in present:
        cursor.execute("SHOW COLUMNS FROM projects LIKE 'media'")
        if cursor.fetchone():
            cursor.execute("SELECT id, media FROM projects WHERE media IS NOT NULL AND media <> ''")
            for row in cursor.fetchall():
                data["project_media"].append(
                    {"project_id": row["id"], "filename": row["media"], "position": 0}
                )

    cursor.close()
    connection.close()
    return data


def clean(table, row):
    row = dict(row)
    if table == "admins" and not str(row["password"]).startswith(HASH_PREFIXES):
        row["password"] = generate_password_hash(row["password"])
    if table in ("projects",):
        row["technologies"] = row.get("technologies") or ""
        row["project_link"] = row.get("project_link") or ""
    if table == "certificates":
        row["description"] = row.get("description") or ""
    return row


def write_supabase(data):
    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT (SELECT COUNT(*) FROM projects) + (SELECT COUNT(*) FROM messages) AS n")
    if cursor.fetchone()["n"]:
        sys.exit("Supabase already has projects or messages. Nothing was copied.")

    # The schema's default profile row is replaced by the real one.
    cursor.execute("DELETE FROM profile")

    for table, rows in data.items():
        for row in rows:
            row = clean(table, row)
            columns = list(row)
            cursor.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})",
                [row[column] for column in columns]
            )
        if table != "profile":
            # New rows continue after the copied ids.
            cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )
        print(f"  {table}: {len(rows)} row(s)")

    connection.commit()
    cursor.close()
    connection.close()


def referenced_files(data):
    names = {row["filename"] for row in data["project_media"]}
    names |= {row["photo"] for row in data["certificates"] if row.get("photo")}
    for row in data["profile"]:
        names |= {row.get("picture"), row.get("hero_picture")}
    return sorted(name for name in names if name)


def upload_files(names):
    project_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = os.getenv("SUPABASE_BUCKET", "portfolio-media")
    headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    missing = []
    for name in names:
        path = UPLOADS / name
        if not path.is_file():
            missing.append(name)
            continue
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = requests.post(
            f"{project_url}/storage/v1/object/{bucket}/{name}",
            headers=dict(headers, **{"Content-Type": content_type, "x-upsert": "true"}),
            data=path.read_bytes(),
            timeout=120
        )
        if response.status_code >= 300:
            sys.exit(f"Upload of {name} failed: {response.status_code} {response.text}")
        print(f"  uploaded {name}")

    if missing:
        print(f"  WARNING: not found in static/uploads, so not uploaded: {', '.join(missing)}")


if __name__ == "__main__":
    print("Reading XAMPP MySQL...")
    mysql_data = read_mysql()
    print("Copying rows to Supabase...")
    write_supabase(mysql_data)
    print("Uploading files to Supabase Storage...")
    upload_files(referenced_files(mysql_data))
    print("Done. Your XAMPP database was only read, never changed.")
