"""
SQL Server connection helper for the MLB loaders.

Two different logins are in play, on purpose:

  * These loader scripts WRITE, so by default they connect with Windows
    authentication (your own account, which is a sysadmin on your machine).
    No password lives in any file.

  * The MCP server READS, and connects as `mcp_reader`, which only has
    db_datareader. That is what keeps a chatty AI from ever writing to your
    database.

Override the defaults with environment variables if you need to:

    MSSQL_SERVER     default: localhost
    MSSQL_PORT       default: 1433
    MSSQL_DATABASE   default: MLB
    MSSQL_USER       optional - set this (with MSSQL_PASSWORD) to use a SQL
    MSSQL_PASSWORD   login instead of Windows authentication
    MSSQL_DRIVER     optional - exact ODBC driver name, otherwise auto-detected
"""

from __future__ import annotations

import os
import re
import urllib.parse

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

# pyodbc is imported lazily inside find_driver(). Keeping it out of the module
# import means --dry-run still works on a machine with no ODBC driver
# installed, which is exactly when you most want to check the fetch path.

DEFAULT_SERVER = os.environ.get("MSSQL_SERVER", "localhost")
DEFAULT_PORT = os.environ.get("MSSQL_PORT", "1433")
DEFAULT_DATABASE = os.environ.get("MSSQL_DATABASE", "MLB")

# Preference order - newest driver first.
_DRIVER_PREFERENCE = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "ODBC Driver 11 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]


def find_driver() -> str:
    """Pick the best installed ODBC driver, or explain how to get one."""
    explicit = os.environ.get("MSSQL_DRIVER")
    if explicit:
        return explicit

    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            f"pyodbc could not be imported ({exc}).\n"
            "On Windows: pip install pyodbc, then install Microsoft's\n"
            "'ODBC Driver 18 for SQL Server' if you haven't already."
        ) from exc

    installed = pyodbc.drivers()
    for candidate in _DRIVER_PREFERENCE:
        if candidate in installed:
            return candidate

    raise RuntimeError(
        "No SQL Server ODBC driver found.\n"
        f"pyodbc reports these drivers installed: {installed or '(none)'}\n\n"
        "Install 'ODBC Driver 18 for SQL Server' from Microsoft:\n"
        "  https://learn.microsoft.com/sql/connect/odbc/"
        "download-odbc-driver-for-sql-server\n"
        "It is a small standalone installer and needs no restart."
    )


def connection_url(database: str | None = None) -> str:
    """Build the SQLAlchemy URL."""
    database = database or DEFAULT_DATABASE
    driver = find_driver()

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={DEFAULT_SERVER},{DEFAULT_PORT}",
        f"DATABASE={database}",
        "TrustServerCertificate=yes",
    ]

    user = os.environ.get("MSSQL_USER")
    password = os.environ.get("MSSQL_PASSWORD")
    if user:
        parts += [f"UID={user}", f"PWD={password or ''}"]
    else:
        parts.append("Trusted_Connection=yes")

    odbc = ";".join(parts)
    return "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)


def get_engine(database: str | None = None, create: bool = False) -> Engine:
    """
    Return an engine for `database`.

    With create=True, the database is created first if it does not exist
    (connecting via master to do so).
    """
    database = database or DEFAULT_DATABASE
    if create:
        ensure_database(database)
    engine = sa.create_engine(connection_url(database), fast_executemany=True)
    # Fail fast with a readable error rather than deep inside a load.
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
    return engine


def ensure_database(database: str) -> None:
    """CREATE DATABASE if it isn't there yet."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise ValueError(f"refusing unusual database name: {database!r}")
    master = sa.create_engine(connection_url("master"), isolation_level="AUTOCOMMIT")
    with master.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM sys.databases WHERE name = :n"), {"n": database}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f"CREATE DATABASE [{database}]"))
            print(f"  created database [{database}]")
    master.dispose()


def sql_types(df: pd.DataFrame, max_varchar: int = 4000) -> dict:
    """
    Map a DataFrame's text columns to sized NVARCHAR.

    Without this, pandas hands SQL Server NVARCHAR(MAX) for every string
    column, which cannot be indexed and bloats the tables badly.
    """
    types: dict = {}
    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) in ("string", "str"):
            lengths = df[col].astype("string").str.len()
            longest = int(lengths.max()) if lengths.notna().any() else 1
            size = max(8, min(max_varchar, int(longest * 1.3) + 8))
            types[col] = sa.types.NVARCHAR(length=size)
    return types


def write_table(df: pd.DataFrame, table: str, engine: Engine,
                if_exists: str = "replace", chunksize: int = 5_000,
                schema: str = "dbo") -> int:
    """Write a DataFrame to SQL Server. Returns the row count written."""
    if df is None or len(df) == 0:
        print(f"  {table}: nothing to write, skipped")
        return 0

    # SQL Server rejects duplicate/empty column names and dots in identifiers.
    df = df.rename(columns=lambda c: re.sub(r"[^\w]", "_", str(c)).strip("_") or "col")
    df = df.loc[:, ~df.columns.duplicated()]

    df.to_sql(
        table,
        engine,
        schema=schema,
        if_exists=if_exists,
        index=False,
        chunksize=chunksize,
        dtype=sql_types(df),
        method=None,
    )
    where = f"[{schema}].[{table}]" if schema else f"[{table}]"
    print(f"  {table}: {len(df):,} rows -> {where}")
    return len(df)


def describe_target(database: str | None = None) -> None:
    """Print where we are about to write, so mistakes are obvious up front."""
    database = database or DEFAULT_DATABASE
    auth = f"SQL login {os.environ['MSSQL_USER']}" if os.environ.get("MSSQL_USER") \
        else "Windows authentication"
    print(f"Target: {DEFAULT_SERVER},{DEFAULT_PORT} database [{database}] via {auth}")
    print(f"Driver: {find_driver()}")
