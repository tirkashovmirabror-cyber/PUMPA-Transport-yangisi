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
                ism TEXT NOT NULL,
                telefon TEXT,
                transport_id INTEGER,
                FOREIGN KEY(transport_id) REFERENCES transportlar(id) ON DELETE SET NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ota_onalar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ism TEXT NOT NULL,
                telefon TEXT,
                pairing_code TEXT,
                telegram_chat_id TEXT
            )
        """)
        # Eski bazalarda bu ustunlar bo'lmasligi mumkin — mavjud bo'lmasa qo'shamiz
        existing_cols = [r["name"] for r in db.execute("PRAGMA table_info(ota_onalar)").fetchall()]
        if "pairing_code" not in existing_cols:
            db.execute("ALTER TABLE ota_onalar ADD COLUMN pairing_code TEXT")
        if "telegram_chat_id" not in existing_cols:
            db.execute("ALTER TABLE ota_onalar ADD COLUMN telegram_chat_id TEXT")
        db.execute("""
            CREATE TABLE IF NOT EXISTS sozlamalar (
                kalit TEXT PRIMARY KEY,
                qiymat TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS bolalar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ism TEXT NOT NULL,
                yosh INTEGER,
                ota_ona_id INTEGER,
                transport_id INTEGER,
                avatar TEXT DEFAULT '🧒',
                FOREIGN KEY(ota_ona_id) REFERENCES ota_onalar(id) ON DELETE SET NULL,
                FOREIGN KEY(transport_id) REFERENCES transportlar(id) ON DELETE SET NULL
            )
        """)


init_db()

# ---------------------------------------------------------------------------
# Telegram bot yordamchilari
# ---------------------------------------------------------------------------
import json
import math
import urllib.request
import urllib.error

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def send_telegram_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def broadcast_to_parents(text: str):
    with get_db() as db:
        rows = db.execute(
            "SELECT telegram_chat_id FROM ota_onalar WHERE telegram_chat_id IS NOT NULL AND telegram_chat_id != ''"
        ).fetchall()
    for r in rows:
        send_telegram_message(r["telegram_chat_id"], text)


def haversine_metres(lat1, lon1, lat2, lon2):
    R = 6371000  # Yer radiusi, metrda
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def get_setting(key: str, default=None):
    with get_db() as db:
        row = db.execute("SELECT qiymat FROM sozlamalar WHERE kalit=?", (key,)).fetchone()
        return row["qiymat"] if row else default


def set_setting(key: str, value: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO sozlamalar (kalit, qiymat) VALUES (?, ?) "
            "ON CONFLICT(kalit) DO UPDATE SET qiymat=excluded.qiymat",
            (key, value),
        )

# ---------------------------------------------------------------------------
# Admin autentifikatsiya (oddiy token, xotirada saqlanadi)
# ---------------------------------------------------------------------------
active_tokens = set()


class LoginRequest(BaseModel):
    username: str
    password: str


def require_admin(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token topilmadi")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in active_tokens:
        raise HTTPException(status_code=401, detail="Token noto'g'ri yoki muddati o'tgan")
    return True


@app.post("/admin/login")
def admin_login(body: LoginRequest):
    if body.username != ADMIN_USERNAME or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Login yoki parol xato")
    token = secrets.token_hex(24)
    active_tokens.add(token)
    return {"token": token}


@app.post("/admin/logout")
def admin_logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        active_tokens.discard(authorization.removeprefix("Bearer ").strip())
    return {"success": True}


# ---------------------------------------------------------------------------
# Pydantic modellari
# ---------------------------------------------------------------------------
class Transport(BaseModel):
    nomi: str
    davlat_raqami: Optional[str] = None
    marshrut: Optional[str] = None


class Haydovchi(BaseModel):
    ism: str
    telefon: Optional[str] = None
    transport_id: Optional[int] = None


class OtaOna(BaseModel):
    ism: str
    telefon: Optional[str] = None


class Bola(BaseModel):
    ism: str
    yosh: Optional[int] = None
    ota_ona_id: Optional[int] = None
    transport_id: Optional[int] = None
    avatar: Optional[str] = "🧒"


# ---------------------------------------------------------------------------
# Umumiy CRUD generatori (4 ta jadval uchun bir xil naqsh)
# ---------------------------------------------------------------------------
def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def make_crud(table: str, model, fields: List[str]):

    def list_items():
        with get_db() as db:
            rows = db.execute(f"SELECT * FROM {table} ORDER BY id DESC").fetchall()
            return [row_to_dict(r) for r in rows]

    def create_item(item: model):
        cols = ", ".join(fields)
        placeholders = ", ".join(["?"] * len(fields))
        values = [getattr(item, f) for f in fields]
        try:
            with get_db() as db:
                cur = db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
                new_id = cur.lastrowid
                row = db.execute(f"SELECT * FROM {table} WHERE id=?", (new_id,)).fetchone()
                return row_to_dict(row)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=400,
                detail="Bog'liq ID topilmadi. Avval tegishli Transport/Ota-ona/Haydovchi yozuvini qo'shing, keyin shu ID'ni kiriting (yoki maydonni bo'sh qoldiring)."
            )

    def update_item(item_id: int, item: model):
        set_clause = ", ".join([f"{f}=?" for f in fields])
        values = [getattr(item, f) for f in fields] + [item_id]
        try:
            with get_db() as db:
                existing = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Topilmadi")
                db.execute(f"UPDATE {table} SET {set_clause} WHERE id=?", values)
                row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
                return row_to_dict(row)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=400,
                detail="Bog'liq ID topilmadi. Avval tegishli Transport/Ota-ona/Haydovchi yozuvini qo'shing, keyin shu ID'ni kiriting (yoki maydonni bo'sh qoldiring)."
            )

    def delete_item(item_id: int):
        with get_db() as db:
            existing = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Topilmadi")
            db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
            return {"success": True}

    return list_items, create_item, update_item, delete_item


transport_list, transport_create, transport_update, transport_delete = make_crud(
    "transportlar", Transport, ["nomi", "davlat_raqami", "marshrut"]
)
haydovchi_list, haydovchi_create, haydovchi_update, haydovchi_delete = make_crud(
    "haydovchilar", Haydovchi, ["ism", "telefon", "transport_id"]
)
otaona_list, otaona_create, otaona_update, otaona_delete = make_crud(
    "ota_onalar", OtaOna, ["ism", "telefon"]
)


def otaona_create_with_code(item: OtaOna):
    code = secrets.token_hex(3).upper()  # masalan "A1B2C3"
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO ota_onalar (ism, telefon, pairing_code) VALUES (?, ?, ?)",
            (item.ism, item.telefon, code),
        )
        new_id = cur.lastrowid
        row = db.execute("SELECT * FROM ota_onalar WHERE id=?", (new_id,)).fetchone()
        return row_to_dict(row)


otaona_create = otaona_create_with_code

bola_list, bola_create, bola_update, bola_delete = make_crud(
    "bolalar", Bola, ["ism", "yosh", "ota_ona_id", "transport_id", "avatar"]
)

# --- Transportlar ---
app.get("/admin/transportlar", dependencies=[Depends(require_admin)])(transport_list)
app.post("/admin/transportlar", dependencies=[Depends(require_admin)])(transport_create)
app.put("/admin/transportlar/{item_id}", dependencies=[Depends(require_admin)])(transport_update)
app.delete("/admin/transportlar/{item_id}", dependencies=[Depends(require_admin)])(transport_delete)

# --- Haydovchilar ---
app.get("/admin/haydovchilar", dependencies=[Depends(require_admin)])(haydovchi_list)
app.post("/admin/haydovchilar", dependencies=[Depends(require_admin)])(haydovchi_create)
app.put("/admin/haydovchilar/{item_id}", dependencies=[Depends(require_admin)])(haydovchi_update)
app.delete("/admin/haydovchilar/{item_id}", dependencies=[Depends(require_admin)])(haydovchi_delete)

# --- Ota-onalar ---
app.get("/admin/ota-onalar", dependencies=[Depends(require_admin)])(otaona_list)
app.post("/admin/ota-onalar", dependencies=[Depends(require_admin)])(otaona_create)
app.put("/admin/ota-onalar/{item_id}", dependencies=[Depends(require_admin)])(otaona_update)
app.delete("/admin/ota-onalar/{item_id}", dependencies=[Depends(require_admin)])(otaona_delete)

# --- Bolalar ---
app.get("/admin/bolalar", dependencies=[Depends(require_admin)])(bola_list)
app.post("/admin/bolalar", dependencies=[Depends(require_admin)])(bola_create)
app.put("/admin/bolalar/{item_id}", dependencies=[Depends(require_admin)])(bola_update)
app.delete("/admin/bolalar/{item_id}", dependencies=[Depends(require_admin)])(bola_delete)


# ---------------------------------------------------------------------------
# Sozlamalar (bog'cha koordinatalari) — admin panel orqali o'rnatiladi
# ---------------------------------------------------------------------------
class BogchaSozlama(BaseModel):
    bogcha_lat: Optional[float] = None
    bogcha_lng: Optional[float] = None


@app.get("/admin/sozlamalar", dependencies=[Depends(require_admin)])
def get_sozlamalar():
    return {
        "bogcha_lat": get_setting("bogcha_lat"),
        "bogcha_lng": get_setting("bogcha_lng"),
    }


@app.post("/admin/sozlamalar", dependencies=[Depends(require_admin)])
def save_sozlamalar(body: BogchaSozlama):
    if body.bogcha_lat is not None:
        set_setting("bogcha_lat", str(body.bogcha_lat))
    if body.bogcha_lng is not None:
        set_setting("bogcha_lng", str(body.bogcha_lng))
    return {"success": True}


# ---------------------------------------------------------------------------
# Telegram webhook — ota-onalar botga pairing_code yuborib o'zini ulaydi
# ---------------------------------------------------------------------------
@app.post("/telegram/webhook")
async def telegram_webhook(update: dict):
    message = update.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip().upper()

    if not chat_id or not text:
        return {"ok": True}

    if text == "/START":
        send_telegram_message(
            chat_id,
            "Assalomu alaykum! PUMPA Kids Transport botiga xush kelibsiz.\n"
            "Admin sizga bergan ulash kodini shu yerga yozib yuboring (masalan: A1B2C3)."
        )
        return {"ok": True}

    with get_db() as db:
        row = db.execute("SELECT * FROM ota_onalar WHERE pairing_code=?", (text,)).fetchone()
    if row:
        with get_db() as db:
            db.execute("UPDATE ota_onalar SET telegram_chat_id=? WHERE id=?", (chat_id, row["id"]))
        send_telegram_message(chat_id, f"✅ Muvaffaqiyatli ulandingiz, {row['ism']}! Endi transport xabarlarini shu yerdan olasiz.")
    else:
        send_telegram_message(chat_id, "❌ Kod topilmadi. Admin bergan kodni to'g'ri kiriting.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Eski GPS/holat endpointlari (driver.html / parent.html hozircha shularni
# ishlatadi — kelgusi bosqichda transport_id bo'yicha ko'p-avtobusli qilinadi)
# ---------------------------------------------------------------------------
class Location(BaseModel):
    latitude: float
    longitude: float


class RouteStatus(BaseModel):
    status: str


location = {"latitude": None, "longitude": None}
route = {"status": "waiting"}
trip_state = {"yaqinlashdi_xabar_yuborildi": False}


@app.get("/")
def home():
    return {"app": "PUMPA Kids Transport", "status": "online"}


@app.post("/driver/location")
def set_location(x: Location):
    location.update(x.model_dump())

    bogcha_lat = get_setting("bogcha_lat")
    bogcha_lng = get_setting("bogcha_lng")
    if bogcha_lat and bogcha_lng and route["status"] in ("started", "picked_up"):
        distance = haversine_metres(x.latitude, x.longitude, float(bogcha_lat), float(bogcha_lng))
        if distance <= 500 and not trip_state["yaqinlashdi_xabar_yuborildi"]:
            trip_state["yaqinlashdi_xabar_yuborildi"] = True
            broadcast_to_parents("🏁 PUMPA Kids Transport yaqinlashib qoldi — shoshiling!")

    return {"success": True, "location": location}


@app.get("/driver/location")
def get_location():
    return location


@app.post("/driver/route")
def set_route(x: RouteStatus):
    route["status"] = x.status
    if x.status == "started":
        trip_state["yaqinlashdi_xabar_yuborildi"] = False
        broadcast_to_parents("🚐 PUMPA Kids Transport yo'lga chiqdi — shoshiling!")
    return {"success": True, "status": route["status"]}


@app.get("/driver/route")
def get_route():
    return route
