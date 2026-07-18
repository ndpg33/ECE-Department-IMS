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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ims.db"
STATIC_DIR = BASE_DIR / "static"
TERMINAL_ID = 1
ASSET_STATUSES = ("AVAILABLE", "CHECKED_OUT", "MAINTENANCE", "MISSING", "RETIRED")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Add a column when upgrading an existing prototype database."""
    existing_columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def log_event(
    connection: sqlite3.Connection,
    event_type: str,
    *,
    session_id: str | None = None,
    user_id: int | None = None,
    item_id: int | None = None,
    asset_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO events (
            session_id, user_id, event_type, item_id, asset_id, timestamp, details
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            user_id,
            event_type,
            item_id,
            asset_id,
            utc_now(),
            json.dumps(details or {}),
        ),
    )


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                university_identifier TEXT UNIQUE,
                authorized INTEGER NOT NULL DEFAULT 1,
                role TEXT NOT NULL DEFAULT 'student'
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_identifier TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS terminals (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                location TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                terminal_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                ending_reason TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (terminal_id) REFERENCES terminals(id)
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_number TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                inventory_type TEXT NOT NULL CHECK (inventory_type IN ('consumable', 'returnable')),
                location TEXT NOT NULL,
                estimated_quantity REAL,
                low_stock_threshold REAL,
                unit TEXT NOT NULL DEFAULT 'units',
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                asset_tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
                serial_number TEXT,
                status TEXT NOT NULL DEFAULT 'AVAILABLE'
                    CHECK (status IN ('AVAILABLE', 'CHECKED_OUT', 'MAINTENANCE', 'MISSING', 'RETIRED')),
                current_user_id INTEGER,
                checked_out_at TEXT,
                location TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (item_id) REFERENCES items(id),
                FOREIGN KEY (current_user_id) REFERENCES users(id),
                CHECK (
                    (status = 'CHECKED_OUT' AND current_user_id IS NOT NULL AND checked_out_at IS NOT NULL)
                    OR
                    (status <> 'CHECKED_OUT' AND current_user_id IS NULL AND checked_out_at IS NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                item_id INTEGER,
                asset_id INTEGER,
                timestamp TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (item_id) REFERENCES items(id),
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            );
            """
        )

        # Phase 1 databases already have these tables, but not the Phase 1.1
        # columns. SQLite's CREATE TABLE IF NOT EXISTS does not alter an
        # existing table, so apply small, repeatable migrations explicitly.
        ensure_column(
            connection,
            "sessions",
            "terminal_id",
            "INTEGER REFERENCES terminals(id)",
        )
        ensure_column(connection, "events", "asset_id", "INTEGER REFERENCES assets(id)")

        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_asset ON events(asset_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
            CREATE INDEX IF NOT EXISTS idx_assets_item ON assets(item_id);
            CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_tag_nocase
                ON assets(asset_tag COLLATE NOCASE);
            """
        )

        connection.execute(
            "INSERT OR IGNORE INTO terminals (id, name, location) VALUES (?, ?, ?)",
            (TERMINAL_ID, "Development Terminal", "ECE Storeroom Prototype"),
        )
        # Preserve old sessions while assigning them to the development
        # terminal introduced in Phase 1.1. New sessions always provide this
        # value explicitly.
        connection.execute(
            "UPDATE sessions SET terminal_id = ? WHERE terminal_id IS NULL",
            (TERMINAL_ID,),
        )

        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            connection.executemany(
                """
                INSERT INTO users (display_name, university_identifier, authorized, role)
                VALUES (?, ?, ?, ?)
                """,
                [
                    ("Nicholas (Demo)", "DEMO-1001", 1, "student"),
                    ("ECE Lab Manager", "DEMO-ADMIN", 1, "administrator"),
                    ("Unauthorized Demo User", "DEMO-LOCKED", 0, "student"),
                ],
            )
            user_ids = {
                row["university_identifier"]: row["id"]
                for row in connection.execute(
                    "SELECT id, university_identifier FROM users"
                ).fetchall()
            }
            connection.executemany(
                "INSERT INTO cards (card_identifier, user_id, active) VALUES (?, ?, ?)",
                [
                    ("CARD-0001", user_ids["DEMO-1001"], 1),
                    ("CARD-ADMIN", user_ids["DEMO-ADMIN"], 1),
                    ("CARD-LOCKED", user_ids["DEMO-LOCKED"], 1),
                ],
            )

        seed_items = [
            (
                "RES-10K-025W",
                "10 kΩ Resistor, 1/4 W",
                "Through-hole carbon film resistor, 5% tolerance",
                "Resistors",
                "consumable",
                "Shelf B · Cabinet 3 · Bin 12",
                430,
                100,
                "pieces",
            ),
            (
                "RES-1K-025W",
                "1 kΩ Resistor, 1/4 W",
                "Through-hole carbon film resistor, 5% tolerance",
                "Resistors",
                "consumable",
                "Shelf B · Cabinet 3 · Bin 08",
                75,
                100,
                "pieces",
            ),
            (
                "CAP-100UF-25V",
                "100 µF Electrolytic Capacitor",
                "Radial electrolytic capacitor rated for 25 V",
                "Capacitors",
                "consumable",
                "Shelf B · Cabinet 5 · Bin 04",
                160,
                40,
                "pieces",
            ),
            (
                "MCU-ARD-UNO-R4",
                "Arduino UNO R4 Minima",
                "Microcontroller development board",
                "Development Boards",
                "returnable",
                "Shelf A · Secure Drawer 2",
                8,
                2,
                "boards",
            ),
            (
                "METER-FLUKE-117",
                "Fluke 117 Digital Multimeter",
                "True-RMS handheld digital multimeter",
                "Test Equipment",
                "returnable",
                "Equipment Cabinet 1 · Slot 03",
                3,
                1,
                "meters",
            ),
            (
                "WIRE-JUMPER-MM",
                "Male-to-Male Breadboard Jumper Wires",
                "Assorted-color solderless breadboard jumper wires",
                "Wire and Interconnects",
                "consumable",
                "Shelf C · Cabinet 1 · Bin 06",
                25,
                30,
                "wires",
            ),
            (
                "METER-FLUKE-87V",
                "Fluke 87V Digital Multimeter",
                "Industrial True-RMS handheld digital multimeter",
                "Test Equipment",
                "returnable",
                "Equipment Cabinet A",
                2,
                1,
                "meters",
            ),
            (
                "SCOPE-RIGOL-DS1054Z",
                "Rigol DS1054Z Oscilloscope",
                "Four-channel digital storage oscilloscope",
                "Test Equipment",
                "returnable",
                "Equipment Cabinet B",
                1,
                1,
                "oscilloscopes",
            ),
            (
                "SUPPLY-BENCH-DEMO",
                "Bench Power Supply",
                "Adjustable laboratory DC power supply",
                "Test Equipment",
                "returnable",
                "Equipment Cabinet C",
                1,
                1,
                "supplies",
            ),
        ]
        connection.executemany(
            """
            INSERT OR IGNORE INTO items (
                part_number, name, description, category, inventory_type,
                location, estimated_quantity, low_stock_threshold, unit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seed_items,
        )

        item_ids = {
            row["part_number"]: row["id"]
            for row in connection.execute(
                """
                SELECT id, part_number FROM items
                WHERE part_number IN (?, ?, ?)
                """,
                (
                    "METER-FLUKE-87V",
                    "SCOPE-RIGOL-DS1054Z",
                    "SUPPLY-BENCH-DEMO",
                ),
            ).fetchall()
        }
        seed_assets = [
            (
                item_ids["METER-FLUKE-87V"],
                "ECE-METER-001",
                "DEMO-F87V-001",
                "AVAILABLE",
                "Equipment Cabinet A · Slot 01",
                "Phase 1.1 demonstration asset",
            ),
            (
                item_ids["METER-FLUKE-87V"],
                "ECE-METER-002",
                "DEMO-F87V-002",
                "AVAILABLE",
                "Equipment Cabinet A · Slot 02",
                "Phase 1.1 demonstration asset",
            ),
            (
                item_ids["SCOPE-RIGOL-DS1054Z"],
                "ECE-SCOPE-001",
                "DEMO-DS1054Z-001",
                "AVAILABLE",
                "Equipment Cabinet B · Slot 01",
                "Phase 1.1 demonstration asset",
            ),
            (
                item_ids["SUPPLY-BENCH-DEMO"],
                "ECE-SUPPLY-001",
                "DEMO-PSU-001",
                "MAINTENANCE",
                "Maintenance Bench",
                "Unavailable for checkout during Phase 1.1 testing",
            ),
        ]
        connection.executemany(
            """
            INSERT OR IGNORE INTO assets (
                item_id, asset_tag, serial_number, status, location, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            seed_assets,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="ECE Storeroom IMS Terminal",
    version="0.2.0",
    description="Phase 1.1 terminal backend for the ECE Storeroom Inventory Management System.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ScanRequest(BaseModel):
    card_identifier: str = Field(min_length=1, max_length=100)


class EndSessionRequest(BaseModel):
    ending_reason: str = Field(default="USER_SIGN_OUT", max_length=50)


def require_active_session(
    connection: sqlite3.Connection, session_id: str
) -> sqlite3.Row:
    session = connection.execute(
        """
        SELECT s.*, u.display_name, u.role
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.id = ? AND s.ended_at IS NULL
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=401, detail="Session is missing, invalid, or ended")
    return session


def stock_status(row: sqlite3.Row) -> str:
    quantity = row["estimated_quantity"]
    threshold = row["low_stock_threshold"]
    if quantity is None or threshold is None:
        return "unknown"
    if quantity <= 0:
        return "out"
    if quantity <= threshold:
        return "low"
    return "adequate"


def serialize_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "part_number": row["part_number"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "inventory_type": row["inventory_type"],
        "location": row["location"],
        "estimated_quantity": row["estimated_quantity"],
        "low_stock_threshold": row["low_stock_threshold"],
        "unit": row["unit"],
        "stock_status": stock_status(row),
    }


def normalize_asset_tag(asset_tag: str) -> str:
    normalized = asset_tag.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="Asset tag is required")
    if len(normalized) > 100:
        raise HTTPException(status_code=400, detail="Asset tag is too long")
    return normalized


def get_asset_by_tag(
    connection: sqlite3.Connection, asset_tag: str
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            a.*,
            i.name AS item_name,
            i.part_number,
            i.description AS item_description,
            i.category,
            u.display_name AS borrower_name
        FROM assets a
        JOIN items i ON i.id = a.item_id
        LEFT JOIN users u ON u.id = a.current_user_id
        WHERE a.asset_tag = ? COLLATE NOCASE AND a.active = 1
        """,
        (normalize_asset_tag(asset_tag),),
    ).fetchone()


def serialize_asset(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "asset_tag": row["asset_tag"],
        "serial_number": row["serial_number"],
        "status": row["status"],
        "location": row["location"],
        "notes": row["notes"],
        "checked_out_at": row["checked_out_at"],
        "current_user": (
            {
                "id": row["current_user_id"],
                "display_name": row["borrower_name"],
            }
            if row["current_user_id"] is not None
            else None
        ),
        "item": {
            "id": row["item_id"],
            "part_number": row["part_number"],
            "name": row["item_name"],
            "description": row["item_description"],
            "category": row["category"],
        },
    }


def checkout_asset(
    connection: sqlite3.Connection,
    asset_tag: str,
    *,
    user_id: int,
    session_id: str,
) -> sqlite3.Row:
    normalized_tag = normalize_asset_tag(asset_tag)
    asset = get_asset_by_tag(connection, normalized_tag)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset tag not recognized")
    if asset["status"] == "CHECKED_OUT":
        borrower = asset["borrower_name"] or "another user"
        raise HTTPException(
            status_code=409,
            detail=f"Asset is already checked out to {borrower}",
        )
    if asset["status"] != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail=f"Asset cannot be checked out while status is {asset['status']}",
        )

    checked_out_at = utc_now()
    cursor = connection.execute(
        """
        UPDATE assets
        SET status = 'CHECKED_OUT', current_user_id = ?, checked_out_at = ?
        WHERE id = ? AND status = 'AVAILABLE'
        """,
        (user_id, checked_out_at, asset["id"]),
    )
    if cursor.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail="Asset status changed before checkout completed; scan it again",
        )

    log_event(
        connection,
        "ASSET_CHECKED_OUT",
        session_id=session_id,
        user_id=user_id,
        item_id=asset["item_id"],
        asset_id=asset["id"],
        details={
            "asset_tag": normalized_tag,
            "checked_out_at": checked_out_at,
            "location": asset["location"],
        },
    )
    return get_asset_by_tag(connection, normalized_tag)  # type: ignore[return-value]


def return_asset(
    connection: sqlite3.Connection,
    asset_tag: str,
    *,
    returning_user_id: int,
    session_id: str,
) -> sqlite3.Row:
    normalized_tag = normalize_asset_tag(asset_tag)
    asset = get_asset_by_tag(connection, normalized_tag)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset tag not recognized")
    if asset["status"] == "AVAILABLE":
        raise HTTPException(status_code=409, detail="Asset is already available")
    if asset["status"] != "CHECKED_OUT":
        raise HTTPException(
            status_code=409,
            detail=f"Asset cannot be returned while status is {asset['status']}",
        )

    original_borrower_id = asset["current_user_id"]
    original_borrower_name = asset["borrower_name"]
    original_checked_out_at = asset["checked_out_at"]
    returned_at = utc_now()

    cursor = connection.execute(
        """
        UPDATE assets
        SET status = 'AVAILABLE', current_user_id = NULL, checked_out_at = NULL
        WHERE id = ? AND status = 'CHECKED_OUT'
        """,
        (asset["id"],),
    )
    if cursor.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail="Asset status changed before return completed; scan it again",
        )

    log_event(
        connection,
        "ASSET_RETURNED",
        session_id=session_id,
        user_id=returning_user_id,
        item_id=asset["item_id"],
        asset_id=asset["id"],
        details={
            "asset_tag": normalized_tag,
            "returned_at": returned_at,
            "original_borrower_user_id": original_borrower_id,
            "original_borrower_name": original_borrower_name,
            "original_checked_out_at": original_checked_out_at,
            "returned_by_original_borrower": original_borrower_id
            == returning_user_id,
        },
    )
    return get_asset_by_tag(connection, normalized_tag)  # type: ignore[return-value]


@app.get("/", include_in_schema=False)
def terminal_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": str(DB_PATH.name)}


@app.post("/api/auth/scan")
def scan_card(payload: ScanRequest) -> dict[str, Any]:
    card_identifier = payload.card_identifier.strip().upper()
    with get_db() as connection:
        card = connection.execute(
            """
            SELECT c.card_identifier, c.active AS card_active,
                   u.id AS user_id, u.display_name, u.authorized, u.role
            FROM cards c
            JOIN users u ON u.id = c.user_id
            WHERE UPPER(c.card_identifier) = ?
            """,
            (card_identifier,),
        ).fetchone()

        if card is None:
            log_event(
                connection,
                "CARD_REJECTED",
                details={"card_identifier": card_identifier, "reason": "UNKNOWN_CARD"},
            )
            connection.commit()
            raise HTTPException(status_code=404, detail="Card not recognized")

        log_event(
            connection,
            "CARD_SCANNED",
            user_id=card["user_id"],
            details={"card_identifier": card_identifier},
        )

        if not card["card_active"] or not card["authorized"]:
            log_event(
                connection,
                "ACCESS_DENIED",
                user_id=card["user_id"],
                details={"reason": "CARD_OR_USER_INACTIVE"},
            )
            connection.commit()
            raise HTTPException(status_code=403, detail="User is not authorized")

        abandoned_sessions = connection.execute(
            "SELECT id FROM sessions WHERE user_id = ? AND ended_at IS NULL",
            (card["user_id"],),
        ).fetchall()
        for abandoned in abandoned_sessions:
            connection.execute(
                """
                UPDATE sessions
                SET ended_at = ?, ending_reason = 'REPLACED_BY_NEW_SCAN'
                WHERE id = ?
                """,
                (utc_now(), abandoned["id"]),
            )
            log_event(
                connection,
                "SESSION_ENDED",
                session_id=abandoned["id"],
                user_id=card["user_id"],
                details={"ending_reason": "REPLACED_BY_NEW_SCAN"},
            )

        session_id = str(uuid.uuid4())
        started_at = utc_now()
        connection.execute(
            """
            INSERT INTO sessions (id, user_id, terminal_id, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, card["user_id"], TERMINAL_ID, started_at),
        )
        log_event(
            connection,
            "SESSION_STARTED",
            session_id=session_id,
            user_id=card["user_id"],
            details={"terminal_id": TERMINAL_ID},
        )
        connection.commit()

        return {
            "session_id": session_id,
            "started_at": started_at,
            "user": {
                "id": card["user_id"],
                "display_name": card["display_name"],
                "role": card["role"],
            },
        }


@app.get("/api/items")
def search_items(
    q: str = Query(default="", max_length=100),
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    query = q.strip()
    with get_db() as connection:
        session = require_active_session(connection, x_session_id)
        like = f"%{query}%"
        rows = connection.execute(
            """
            SELECT * FROM items
            WHERE active = 1
              AND (
                  ? = ''
                  OR name LIKE ? COLLATE NOCASE
                  OR part_number LIKE ? COLLATE NOCASE
                  OR description LIKE ? COLLATE NOCASE
                  OR category LIKE ? COLLATE NOCASE
                  OR location LIKE ? COLLATE NOCASE
              )
            ORDER BY
                CASE inventory_type WHEN 'consumable' THEN 0 ELSE 1 END,
                name
            LIMIT 50
            """,
            (query, like, like, like, like, like),
        ).fetchall()

        if query:
            log_event(
                connection,
                "ITEM_SEARCHED",
                session_id=x_session_id,
                user_id=session["user_id"],
                details={"query": query, "result_count": len(rows)},
            )
            connection.commit()

        return {"query": query, "items": [serialize_item(row) for row in rows]}


@app.post("/api/items/{item_id}/view")
def view_item(
    item_id: int,
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    with get_db() as connection:
        session = require_active_session(connection, x_session_id)
        item = connection.execute(
            "SELECT * FROM items WHERE id = ? AND active = 1", (item_id,)
        ).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")

        log_event(
            connection,
            "ITEM_LOCATION_VIEWED",
            session_id=x_session_id,
            user_id=session["user_id"],
            item_id=item_id,
            details={"location": item["location"]},
        )
        connection.commit()
        return {"item": serialize_item(item)}


@app.post("/api/items/{item_id}/probable-usage")
def record_probable_usage(
    item_id: int,
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    with get_db() as connection:
        session = require_active_session(connection, x_session_id)
        item = connection.execute(
            "SELECT * FROM items WHERE id = ? AND active = 1", (item_id,)
        ).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")
        if item["inventory_type"] != "consumable":
            raise HTTPException(
                status_code=400,
                detail="Probable usage is only used for consumable inventory",
            )

        log_event(
            connection,
            "CONSUMABLE_USAGE_RECORDED",
            session_id=x_session_id,
            user_id=session["user_id"],
            item_id=item_id,
            details={
                "tracking_method": "PROBABLE_USAGE",
                "quantity_attributed": None,
            },
        )
        connection.commit()
        return {
            "message": "Probable usage recorded",
            "item": serialize_item(item),
        }


@app.get("/api/assets/{asset_tag}")
def read_asset(
    asset_tag: str,
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    with get_db() as connection:
        require_active_session(connection, x_session_id)
        asset = get_asset_by_tag(connection, asset_tag)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset tag not recognized")
        return {"asset": serialize_asset(asset)}


@app.post("/api/assets/{asset_tag}/checkout")
def checkout_asset_endpoint(
    asset_tag: str,
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    with get_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = require_active_session(connection, x_session_id)
        asset = checkout_asset(
            connection,
            asset_tag,
            user_id=session["user_id"],
            session_id=x_session_id,
        )
        connection.commit()
        return {"message": "Asset checked out", "asset": serialize_asset(asset)}


@app.post("/api/assets/{asset_tag}/return")
def return_asset_endpoint(
    asset_tag: str,
    x_session_id: str = Header(alias="X-Session-ID"),
) -> dict[str, Any]:
    with get_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = require_active_session(connection, x_session_id)
        asset = return_asset(
            connection,
            asset_tag,
            returning_user_id=session["user_id"],
            session_id=x_session_id,
        )
        connection.commit()
        return {"message": "Asset returned", "asset": serialize_asset(asset)}


@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: str, payload: EndSessionRequest) -> dict[str, Any]:
    ending_reason = payload.ending_reason.strip().upper() or "USER_SIGN_OUT"
    with get_db() as connection:
        session = require_active_session(connection, session_id)
        ended_at = utc_now()
        connection.execute(
            "UPDATE sessions SET ended_at = ?, ending_reason = ? WHERE id = ?",
            (ended_at, ending_reason, session_id),
        )
        log_event(
            connection,
            "SESSION_ENDED",
            session_id=session_id,
            user_id=session["user_id"],
            details={"ending_reason": ending_reason},
        )
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN event_type = 'ITEM_LOCATION_VIEWED' THEN 1 ELSE 0 END) AS viewed,
                SUM(CASE WHEN event_type = 'CONSUMABLE_USAGE_RECORDED' THEN 1 ELSE 0 END) AS usage,
                SUM(CASE WHEN event_type = 'ASSET_CHECKED_OUT' THEN 1 ELSE 0 END) AS checkouts,
                SUM(CASE WHEN event_type = 'ASSET_RETURNED' THEN 1 ELSE 0 END) AS returns
            FROM events
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        connection.commit()
        return {
            "ended_at": ended_at,
            "ending_reason": ending_reason,
            "summary": {
                "locations_viewed": counts["viewed"] or 0,
                "probable_usage_records": counts["usage"] or 0,
                "assets_checked_out": counts["checkouts"] or 0,
                "assets_returned": counts["returns"] or 0,
            },
        }


@app.get("/api/events/recent")
def recent_events(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    """Development-only audit view. Protect this endpoint before production use."""
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT e.id, e.timestamp, e.event_type, e.details,
                   u.display_name, i.name AS item_name, i.part_number,
                   a.asset_tag
            FROM events e
            LEFT JOIN users u ON u.id = e.user_id
            LEFT JOIN items i ON i.id = e.item_id
            LEFT JOIN assets a ON a.id = e.asset_id
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        events = []
        for row in rows:
            try:
                details = json.loads(row["details"])
            except json.JSONDecodeError:
                details = {}
            events.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "display_name": row["display_name"],
                    "item_name": row["item_name"],
                    "part_number": row["part_number"],
                    "asset_tag": row["asset_tag"],
                    "details": details,
                }
            )
        return {"events": events}
