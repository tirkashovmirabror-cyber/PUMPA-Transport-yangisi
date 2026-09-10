import os
import sqlite3
import secrets
from contextlib import contextmanager
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("PUMPA_DB_PATH", "pumpa.db")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
# Eslatma: Render'ning bepul tarifida disk vaqtinchalik (ephemeral) — server qayta
# ishga tushganda pumpa.db fayli o'chib ketishi mumkin. Doimiy saqlash kerak bo'lsa,
# Render'da "Persistent Disk" ulash yoki tashqi Postgres bazaga o'tish kerak bo'ladi.

app = FastAPI(title="PUMPA Kids Transport")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Database yordamchilari
# ---------------------------------------------------------------------------
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS transportlar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nomi TEXT NOT NULL,
                davlat_raqami TEXT,
                marshrut TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS haydovchilar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ism TEXT NOT
