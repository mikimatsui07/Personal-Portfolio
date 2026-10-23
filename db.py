import os
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

# Local development reads settings from .env; on Vercel they come from
# the project's Environment Variables (load_dotenv never overrides those).
load_dotenv()


# Supabase's copy-paste links sometimes add options meant for other tools
# (e.g. "?pgbouncer=true" for Prisma); PostgreSQL itself rejects them.
_TOOL_OPTIONS = {"pgbouncer", "connection_limit", "pool_timeout", "schema"}


def _with_safe_password(database_url):
    """Encode special characters (@ : / ? #) in the password.

    Supabase passwords may contain "@", which a URL would otherwise read
    as the end of the password. The host always follows the LAST "@".
    """
    scheme, separator, rest = database_url.partition("://")
    if not separator or "@" not in rest:
        return database_url

    userinfo, _, host_and_path = rest.rpartition("@")
    user, colon, password = userinfo.partition(":")
    if colon:
        password = quote(unquote(password), safe="")
    return f"{scheme}://{user}{colon}{password}@{host_and_path}"


def _without_tool_options(database_url):
    parts = urlsplit(_with_safe_password(database_url))
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _TOOL_OPTIONS
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def get_db_connection():
    """Open a connection to the Supabase PostgreSQL database.

    Rows come back as dictionaries. prepare_threshold=None is required
    by Supabase's transaction pooler (port 6543), which serverless hosts
    like Vercel should use.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )

    return psycopg.connect(
        _without_tool_options(database_url),
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=10
    )
