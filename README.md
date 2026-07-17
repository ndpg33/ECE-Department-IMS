# ECE Storeroom IMS — Phase 1.1 Backend

This project is the first end-to-end terminal prototype for the ECE Storeroom Inventory Management System. Phase 1.1 adds the backend foundation for individually tracked returnable assets while preserving the working Phase 1 terminal interface.

## What currently works

### Phase 1 terminal

- Mock ID-card scanning
- Authorized and denied users
- Storeroom session creation
- Inventory search by name, part number, description, category, or location
- Large item-location display
- Probable-usage logging for consumables
- Automatic logout after 90 seconds of inactivity
- Manual sign-out and session summary
- SQLite audit/event log
- FastAPI interactive API documentation

### Phase 1.1 asset backend

- Individually tagged returnable assets
- Asset lookup by tag
- Transactional checkout and return operations
- Exact borrower and checkout-time tracking
- Duplicate-checkout prevention
- Maintenance, missing, and retired status protection
- Cross-user returns with the original borrower preserved in the audit record
- Asset references in the event log
- Session summaries containing checkout and return totals
- Automatic upgrade of an existing Phase 1 SQLite database
- Automated backend and API tests

The terminal checkout and return screens are intentionally not enabled yet. The backend is being proven first so database, API, and interface problems can be debugged separately.

## Windows setup

1. Install **Python 3.10 or newer** and select **Add Python to PATH** during installation.
2. Extract or clone this project to a normal folder.
3. Double-click `run.bat`.
4. The script creates a virtual environment, installs FastAPI, starts the server, and opens the terminal in your browser.
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

The current suite verifies:

- Idempotent asset seeding
- Checkout and return state changes
- Duplicate-checkout rejection
- Maintenance lockout
- Cross-user returns
- Migration of an existing Phase 1 event table
- Full API checkout and return flow

## Asset API

All asset endpoints require an active session ID in the `X-Session-ID` header.

```text
GET  /api/assets/{asset_tag}
POST /api/assets/{asset_tag}/checkout
POST /api/assets/{asset_tag}/return
```

The easiest way to test these before the UI is added is through the interactive documentation at `/docs`:

1. Call `POST /api/auth/scan` with `CARD-0001`.
2. Copy the returned `session_id`.
3. Open an asset endpoint.
4. Enter that value into the `X-Session-ID` field.
5. Use `ECE-METER-001` as the asset tag.

## Database upgrade behavior

The SQLite database is created automatically at `data/ims.db`. When a Phase 1 database already exists, startup adds the new `assets` table and the `events.asset_id` column without deleting existing users, sessions, items, or events.

Double-click `reset_database.bat` only when you intentionally want to delete the local prototype database and restore all demo data.

## Asset states

An asset can have one of these statuses:

- `AVAILABLE`
- `CHECKED_OUT`
- `MAINTENANCE`
- `MISSING`
- `RETIRED`

Only an `AVAILABLE` asset can be checked out. Only a `CHECKED_OUT` asset can be returned.

## Important prototype limitations

- Mock card identifiers are not secure authentication.
- The development event-log endpoint is visible without administrator authentication.
- The app is designed for one local development terminal.
- Stock quantities are seeded demo values and are not yet connected to load cells.
- The checkout and return terminal screens are not yet implemented.
- Administrative asset editing and status management are not yet implemented.
