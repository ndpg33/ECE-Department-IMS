import json
import sqlite3
import uuid

import pytest
from fastapi import HTTPException

import app


@pytest.fixture()
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "ims-test.db"
    monkeypatch.setattr(app, "DB_PATH", database_path)
    app.initialize_database()
    return database_path


def create_session(connection: sqlite3.Connection, university_identifier: str) -> tuple[str, int]:
    user = connection.execute(
        "SELECT id FROM users WHERE university_identifier = ?",
        (university_identifier,),
    ).fetchone()
    assert user is not None
    session_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO sessions (id, user_id, terminal_id, started_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, user["id"], app.TERMINAL_ID, app.utc_now()),
    )
    connection.commit()
    return session_id, user["id"]


def test_initialization_is_idempotent_and_seeds_assets(isolated_database):
    app.initialize_database()

    with app.get_db() as connection:
        assets = connection.execute(
            "SELECT asset_tag, status FROM assets ORDER BY asset_tag"
        ).fetchall()

    assert [(row["asset_tag"], row["status"]) for row in assets] == [
        ("ECE-METER-001", "AVAILABLE"),
        ("ECE-METER-002", "AVAILABLE"),
        ("ECE-SCOPE-001", "AVAILABLE"),
        ("ECE-SUPPLY-001", "MAINTENANCE"),
    ]


def test_checkout_duplicate_rejection_and_return(isolated_database):
    with app.get_db() as connection:
        session_id, user_id = create_session(connection, "DEMO-1001")

        connection.execute("BEGIN IMMEDIATE")
        checked_out = app.checkout_asset(
            connection,
            "ece-meter-001",
            user_id=user_id,
            session_id=session_id,
        )
        connection.commit()

        assert checked_out["status"] == "CHECKED_OUT"
        assert checked_out["current_user_id"] == user_id
        assert checked_out["checked_out_at"] is not None

        with pytest.raises(HTTPException) as duplicate_error:
            app.checkout_asset(
                connection,
                "ECE-METER-001",
                user_id=user_id,
                session_id=session_id,
            )
        assert duplicate_error.value.status_code == 409
        assert "already checked out" in duplicate_error.value.detail

        connection.execute("BEGIN IMMEDIATE")
        returned = app.return_asset(
            connection,
            "ECE-METER-001",
            returning_user_id=user_id,
            session_id=session_id,
        )
        connection.commit()

        assert returned["status"] == "AVAILABLE"
        assert returned["current_user_id"] is None
        assert returned["checked_out_at"] is None

        event_types = [
            row["event_type"]
            for row in connection.execute(
                """
                SELECT event_type FROM events
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        ]
        assert event_types == ["ASSET_CHECKED_OUT", "ASSET_RETURNED"]


def test_maintenance_asset_cannot_be_checked_out(isolated_database):
    with app.get_db() as connection:
        session_id, user_id = create_session(connection, "DEMO-1001")

        with pytest.raises(HTTPException) as error:
            app.checkout_asset(
                connection,
                "ECE-SUPPLY-001",
                user_id=user_id,
                session_id=session_id,
            )

        assert error.value.status_code == 409
        assert "MAINTENANCE" in error.value.detail


def test_another_authorized_user_can_return_asset(isolated_database):
    with app.get_db() as connection:
        borrower_session, borrower_id = create_session(connection, "DEMO-1001")
        admin_session, admin_id = create_session(connection, "DEMO-ADMIN")

        connection.execute("BEGIN IMMEDIATE")
        app.checkout_asset(
            connection,
            "ECE-SCOPE-001",
            user_id=borrower_id,
            session_id=borrower_session,
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        returned = app.return_asset(
            connection,
            "ECE-SCOPE-001",
            returning_user_id=admin_id,
            session_id=admin_session,
        )
        connection.commit()

        assert returned["status"] == "AVAILABLE"

        event = connection.execute(
            """
            SELECT user_id, details FROM events
            WHERE event_type = 'ASSET_RETURNED' AND session_id = ?
            """,
            (admin_session,),
        ).fetchone()
        details = json.loads(event["details"])

        assert event["user_id"] == admin_id
        assert details["original_borrower_user_id"] == borrower_id
        assert details["original_borrower_name"] == "Nicholas (Demo)"
        assert details["returned_by_original_borrower"] is False


def test_existing_phase1_events_table_is_migrated(tmp_path, monkeypatch):
    database_path = tmp_path / "phase1.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                item_id INTEGER,
                timestamp TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

    monkeypatch.setattr(app, "DB_PATH", database_path)
    app.initialize_database()

    with app.get_db() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(events)").fetchall()
        }

    assert "asset_id" in columns


def test_asset_api_checkout_and_return_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    database_path = tmp_path / "ims-api-test.db"
    monkeypatch.setattr(app, "DB_PATH", database_path)

    with TestClient(app.app) as client:
        auth_response = client.post(
            "/api/auth/scan", json={"card_identifier": "CARD-0001"}
        )
        assert auth_response.status_code == 200
        session_id = auth_response.json()["session_id"]
        headers = {"X-Session-ID": session_id}

        lookup_response = client.get("/api/assets/ECE-METER-002", headers=headers)
        assert lookup_response.status_code == 200
        assert lookup_response.json()["asset"]["status"] == "AVAILABLE"

        checkout_response = client.post(
            "/api/assets/ECE-METER-002/checkout", headers=headers
        )
        assert checkout_response.status_code == 200
        assert checkout_response.json()["asset"]["status"] == "CHECKED_OUT"

        duplicate_response = client.post(
            "/api/assets/ECE-METER-002/checkout", headers=headers
        )
        assert duplicate_response.status_code == 409

        return_response = client.post(
            "/api/assets/ECE-METER-002/return", headers=headers
        )
        assert return_response.status_code == 200
        assert return_response.json()["asset"]["status"] == "AVAILABLE"

        end_response = client.post(
            f"/api/sessions/{session_id}/end",
            json={"ending_reason": "USER_SIGN_OUT"},
        )
        assert end_response.status_code == 200
        assert end_response.json()["summary"]["assets_checked_out"] == 1
        assert end_response.json()["summary"]["assets_returned"] == 1


def test_terminal_ui_exposes_asset_checkout_and_return_controls(
    isolated_database,
):
    from fastapi.testclient import TestClient

    with TestClient(app.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    page = response.text
    assert 'data-workspace="checkout"' in page
    assert 'data-workspace="return"' in page
    assert 'id="checkoutLookupForm"' in page
    assert 'id="returnLookupForm"' in page
    assert "ECE-METER-001" in page
