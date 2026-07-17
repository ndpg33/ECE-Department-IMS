from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ims.db"
INDEX_PATH = BASE_DIR / "static" / "index.html"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def event(conn: sqlite3.Connection, kind: str, session_id: str | None = None,
          user_id: int | None = None, item_id: int | None = None,
          details: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO events(session_id,user_id,event_type,item_id,timestamp,details) VALUES(?,?,?,?,?,?)",
        (session_id, user_id, kind, item_id, now(), json.dumps(details or {})),
    )


def initialize() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
          university_identifier TEXT UNIQUE, authorized INTEGER NOT NULL,
          role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cards(
          id INTEGER PRIMARY KEY, card_identifier TEXT UNIQUE NOT NULL,
          user_id INTEGER NOT NULL, active INTEGER NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, started_at TEXT NOT NULL,
          ended_at TEXT, ending_reason TEXT,
          FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS items(
          id INTEGER PRIMARY KEY, part_number TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          description TEXT NOT NULL, category TEXT NOT NULL,
          inventory_type TEXT NOT NULL, location TEXT NOT NULL,
          estimated_quantity REAL, low_stock_threshold REAL, unit TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id INTEGER,
          event_type TEXT NOT NULL, item_id INTEGER, timestamp TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '{}');
        """)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO users VALUES(?,?,?,?,?)",
                [(1,"Nicholas (Demo)","DEMO-1001",1,"student"),
                 (2,"ECE Lab Manager","DEMO-ADMIN",1,"administrator"),
                 (3,"Unauthorized Demo User","DEMO-LOCKED",0,"student")],
            )
            conn.executemany(
                "INSERT INTO cards VALUES(?,?,?,?)",
                [(1,"CARD-0001",1,1),(2,"CARD-ADMIN",2,1),(3,"CARD-LOCKED",3,1)],
            )
        if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
            rows = [
                (1,"RES-10K-025W","10 kΩ Resistor, 1/4 W","Through-hole carbon film resistor, 5% tolerance","Resistors","consumable","Shelf B · Cabinet 3 · Bin 12",430,100,"pieces",1),
                (2,"RES-1K-025W","1 kΩ Resistor, 1/4 W","Through-hole carbon film resistor, 5% tolerance","Resistors","consumable","Shelf B · Cabinet 3 · Bin 08",75,100,"pieces",1),
                (3,"CAP-100UF-25V","100 µF Electrolytic Capacitor","Radial electrolytic capacitor rated for 25 V","Capacitors","consumable","Shelf B · Cabinet 5 · Bin 04",160,40,"pieces",1),
                (4,"MCU-ARD-UNO-R4","Arduino Uno R4 Minima","Microcontroller development board","Development Boards","returnable","Equipment Cabinet A · Shelf 2",8,2,"boards",1),
                (5,"METER-FLUKE-87V","Fluke 87V Multimeter","Industrial digital multimeter","Test Equipment","returnable","Equipment Cabinet A · Drawer 1",4,1,"meters",1),
            ]
            conn.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)


def active_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT s.*,u.display_name,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.ended_at IS NULL",
        (session_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "Session is missing or has ended")
    return row


def item_json(row: sqlite3.Row) -> dict[str, Any]:
    quantity = row["estimated_quantity"]
    threshold = row["low_stock_threshold"]
    status = "Unknown" if quantity is None else ("Low" if threshold is not None and quantity <= threshold else "Adequate")
    return {**dict(row), "stock_status": status}


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="ECE Storeroom IMS", version="0.1.0", lifespan=lifespan)


class CardRequest(BaseModel):
    card_identifier: str


class EndRequest(BaseModel):
    ending_reason: str = "USER_SIGN_OUT"


@app.get("/")
def home() -> FileResponse:
    return FileResponse(INDEX_PATH)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "database": "connected"}


@app.post("/api/auth/card")
def scan_card(payload: CardRequest) -> dict[str, Any]:
    card_id = payload.card_identifier.strip().upper()
    with db() as conn:
        card = conn.execute("""
          SELECT c.card_identifier,c.active,u.id user_id,u.display_name,u.authorized,u.role
          FROM cards c JOIN users u ON u.id=c.user_id WHERE c.card_identifier=?
        """, (card_id,)).fetchone()
        if card is None:
            event(conn, "CARD_REJECTED", details={"card_identifier": card_id, "reason": "UNKNOWN_CARD"})
            conn.commit()
            raise HTTPException(404, "Card not recognized")
        if not card["active"] or not card["authorized"]:
            event(conn, "CARD_REJECTED", user_id=card["user_id"], details={"reason": "NOT_AUTHORIZED"})
            conn.commit()
            raise HTTPException(403, "This user is not authorized")
        for old in conn.execute("SELECT id FROM sessions WHERE user_id=? AND ended_at IS NULL", (card["user_id"],)).fetchall():
            conn.execute("UPDATE sessions SET ended_at=?,ending_reason=? WHERE id=?", (now(),"REPLACED_BY_NEW_SCAN",old["id"]))
            event(conn,"SESSION_ENDED",old["id"],card["user_id"],details={"ending_reason":"REPLACED_BY_NEW_SCAN"})
        session_id = str(uuid.uuid4())
        started = now()
        conn.execute("INSERT INTO sessions(id,user_id,started_at) VALUES(?,?,?)", (session_id,card["user_id"],started))
        event(conn,"CARD_SCANNED",session_id,card["user_id"],details={"card_identifier":card_id})
        event(conn,"SESSION_STARTED",session_id,card["user_id"])
        conn.commit()
        return {"session_id":session_id,"started_at":started,"user":{"id":card["user_id"],"display_name":card["display_name"],"role":card["role"]}}


@app.get("/api/items")
def search_items(q: str = Query(default="", max_length=100), x_session_id: str = Header(alias="X-Session-ID")) -> dict[str, Any]:
    term = q.strip()
    like = f"%{term}%"
    with db() as conn:
        session = active_session(conn, x_session_id)
        rows = conn.execute("""
          SELECT * FROM items WHERE active=1 AND (?='' OR name LIKE ? OR part_number LIKE ? OR description LIKE ? OR category LIKE ? OR location LIKE ?)
          ORDER BY inventory_type,name LIMIT 50
        """, (term,like,like,like,like,like)).fetchall()
        if term:
            event(conn,"ITEM_SEARCHED",x_session_id,session["user_id"],details={"query":term,"result_count":len(rows)})
            conn.commit()
        return {"query":term,"items":[item_json(r) for r in rows]}


@app.post("/api/items/{item_id}/view")
def view_item(item_id: int, x_session_id: str = Header(alias="X-Session-ID")) -> dict[str, Any]:
    with db() as conn:
        session = active_session(conn, x_session_id)
        row = conn.execute("SELECT * FROM items WHERE id=? AND active=1", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Item not found")
        event(conn,"ITEM_LOCATION_VIEWED",x_session_id,session["user_id"],item_id,{"location":row["location"]})
        conn.commit()
        return {"item":item_json(row)}


@app.post("/api/items/{item_id}/probable-usage")
def probable_usage(item_id: int, x_session_id: str = Header(alias="X-Session-ID")) -> dict[str, Any]:
    with db() as conn:
        session = active_session(conn, x_session_id)
        row = conn.execute("SELECT * FROM items WHERE id=? AND active=1", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Item not found")
        if row["inventory_type"] != "consumable":
            raise HTTPException(400, "Probable usage is only for consumables")
        event(conn,"CONSUMABLE_USAGE_RECORDED",x_session_id,session["user_id"],item_id,{"tracking_method":"PROBABLE_USAGE"})
        conn.commit()
        return {"message":"Probable usage recorded","item":item_json(row)}


@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: str, payload: EndRequest) -> dict[str, Any]:
    with db() as conn:
        session = active_session(conn, session_id)
        ended = now()
        reason = payload.ending_reason.strip().upper() or "USER_SIGN_OUT"
        conn.execute("UPDATE sessions SET ended_at=?,ending_reason=? WHERE id=?", (ended,reason,session_id))
        event(conn,"SESSION_ENDED",session_id,session["user_id"],details={"ending_reason":reason})
        counts = conn.execute("""
          SELECT SUM(event_type='ITEM_LOCATION_VIEWED') viewed,
                 SUM(event_type='CONSUMABLE_USAGE_RECORDED') usage
          FROM events WHERE session_id=?
        """, (session_id,)).fetchone()
        conn.commit()
        return {"ended_at":ended,"ending_reason":reason,"summary":{"locations_viewed":counts["viewed"] or 0,"probable_usage_records":counts["usage"] or 0}}


@app.get("/api/events/recent")
def recent_events(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("""
          SELECT e.*,u.display_name,i.name item_name,i.part_number
          FROM events e LEFT JOIN users u ON u.id=e.user_id LEFT JOIN items i ON i.id=e.item_id
          ORDER BY e.id DESC LIMIT ?
        """, (limit,)).fetchall()
        output = []
        for row in rows:
            record = dict(row)
            try:
                record["details"] = json.loads(record["details"])
            except json.JSONDecodeError:
                record["details"] = {}
            output.append(record)
        return {"events":output}
