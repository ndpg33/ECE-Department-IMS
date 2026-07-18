# ECE Storeroom IMS — Phase 1.1 Terminal

This project is an end-to-end terminal prototype for the ECE Storeroom Inventory Management System. Phase 1.1 supports both approximate consumable usage and exact checkout/return tracking for individually tagged equipment.

## What currently works

- Mock ID-card scanning with authorized and denied users
- Storeroom session creation and 90-second inactivity logout
- Inventory search by name, part number, description, category, or location
- Large item-location display
- Probable-usage logging for consumables
- Individually tagged returnable assets
- Asset lookup, checkout, and return terminal screens
- Exact borrower and checkout-time tracking
- Duplicate-checkout and invalid-status protection
- Cross-user returns with the original borrower preserved in the audit record
- Session summaries with consumable, checkout, and return activity
- SQLite audit/event logging
- Migration of an existing Phase 1 database without deleting its records
- Automated backend, migration, API, and terminal-interface tests

## Windows setup

1. Install **Python 3.10 or newer** and select **Add Python to PATH** during installation.
2. Clone or extract the project to a normal folder.
3. Double-click `run.bat`.
4. The script creates a virtual environment, installs the required packages, starts the server, and opens the terminal in your browser.
5. Stop the server with **Ctrl+C** in the command window.

The first installation needs internet access to download Python packages.

## Demo cards

- `CARD-0001` — authorized student
- `CARD-ADMIN` — authorized administrator
- `CARD-LOCKED` — recognized but unauthorized
- Any other value — unknown card

The on-screen demo buttons enter these values automatically.

## Demo assets

| Asset tag | Item | Initial status |
|---|---|---|
| `ECE-METER-001` | Fluke 87V Digital Multimeter | Available |
| `ECE-METER-002` | Fluke 87V Digital Multimeter | Available |
| `ECE-SCOPE-001` | Rigol DS1054Z Oscilloscope | Available |
| `ECE-SUPPLY-001` | Bench Power Supply | Maintenance |

Asset tags are normalized to uppercase and matched case-insensitively.

## Phase 1.1 terminal workflow

The terminal includes three session workspaces:

1. **Search inventory** for consumables and general item locations.
2. **Check out equipment** by scanning an individual asset tag and confirming an available asset.
3. **Return equipment** by scanning an asset tag and confirming a currently checked-out asset.

For an end-to-end demo, sign in with `CARD-0001`, check out `ECE-METER-001`, return the same asset, and end the session. The session summary and development event log will show both actions.

## Developer commands

Instead of `run.bat`, use PowerShell or Command Prompt:

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Open:

- Terminal UI: `http://127.0.0.1:8000`
- Interactive API documentation: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/api/health`

## Running the tests

After running `run.bat` at least once, double-click `run_tests.bat`.

Or run:

```powershell
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The suite verifies:

- Idempotent asset seeding
- Checkout and return state changes
- Duplicate-checkout rejection
- Maintenance lockout
- Cross-user returns
- Upgrade of a complete Phase 1 database
- Preservation of existing Phase 1 users, sessions, items, and events
- Session creation after a Phase 1 database upgrade
- Full API checkout and return flow
- The original consumable search, location, and probable-usage workflow
- Presence of checkout and return controls in the terminal interface

GitHub Actions also runs the test suite automatically for pushes and pull requests involving `main`.

## Asset API

All asset endpoints require an active session ID in the `X-Session-ID` header.

```text
GET  /api/assets/{asset_tag}
POST /api/assets/{asset_tag}/checkout
POST /api/assets/{asset_tag}/return
```

The interactive documentation at `/docs` can be used to exercise the endpoints directly.

## Database upgrade behavior

The SQLite database is created automatically at `data/ims.db`.

When a Phase 1 database already exists, startup performs repeatable migrations that:

- Add the `assets` table
- Add `events.asset_id`
- Add `sessions.terminal_id`
- Assign existing sessions to the development terminal
- Preserve existing users, cards, sessions, items, and events

Double-click `reset_database.bat` only when you intentionally want to delete the local prototype database and restore the demo data.

## Asset states

An asset can have one of these statuses:

- `AVAILABLE`
- `CHECKED_OUT`
- `MAINTENANCE`
- `MISSING`
- `RETIRED`

Only an `AVAILABLE` asset can be checked out. Only a `CHECKED_OUT` asset can be returned.

## Important prototype limitations

- Mock card identifiers are not secure production authentication.
- The development event-log endpoint is visible without administrator authentication.
- The application is currently designed for one local development terminal.
- Stock quantities are seeded demo values and are not connected to load cells yet.
- Administrative inventory editing, asset status management, and purchasing tools are not implemented yet.
- A production multi-terminal deployment should use a central server and production database rather than one local SQLite file.
