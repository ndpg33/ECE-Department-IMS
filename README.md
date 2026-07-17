# ECE Storeroom IMS — Phase 1 Terminal

This is the first end-to-end prototype for the ECE Storeroom Inventory Management System.

## What currently works

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

Returnable asset checkout is intentionally left as the next feature. The current button is disabled so the consumable workflow can be tested first.

## Windows setup

1. Install **Python 3.10 or newer** and select **Add Python to PATH** during installation.
2. Extract this project to a normal folder.
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

## First test

1. Scan `CARD-0001`.
2. Search for `10k`.
3. Select **Show location**.
4. Select **Record probable usage**.
5. Select **End session**.
6. Watch the event log update after each action.

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

## Database

The SQLite database is created automatically at `data/ims.db`. Double-click `reset_database.bat` to delete it and restore the original demo data the next time the app starts.

## Important prototype limitations

- Mock card identifiers are not secure authentication.
- The development event-log endpoint is visible without administrator authentication.
- The app is designed for one local development terminal.
- Stock quantities are seeded demo values and are not yet connected to load cells.
- Asset checkout/return and the administrative inventory editor are not yet implemented.
