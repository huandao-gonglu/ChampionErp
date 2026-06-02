# SQLite Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `product.json` as the ERP's primary product store with a local SQLite foundation that supports multi-product listing workflows.

**Architecture:** Add a focused `erp_db.py` persistence module with schema creation, product upsert/query, and legacy JSON migration. Keep `打开新版ERP.cmd` unchanged and wire the existing Python web app to initialize and read/write the SQLite store with JSON fallback only for migration compatibility.

**Tech Stack:** Python standard library (`sqlite3`, `json`, `pathlib`, `unittest`), existing ERP model dictionaries.

---

### Task 1: Database Schema and Migration API

**Files:**
- Create: `erp_db.py`
- Create: `tests/test_erp_db.py`

- [ ] **Step 1: Write failing tests**

Test that `initialize_database()` creates the six required tables, `upsert_product_model()` writes a product with platform drafts and media assets, and `migrate_legacy_json()` imports `product.json` plus `output/products/*.json`.

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_erp_db -v`

Expected: fails because `erp_db` does not exist yet.

- [ ] **Step 3: Implement minimal SQLite module**

Create `erp_db.py` with:
- `DEFAULT_DB_NAME = "erp.sqlite3"`
- `REQUIRED_TABLES`
- `initialize_database(app_dir)`
- `connect(app_dir)`
- `product_identity(product)`
- `upsert_product_model(app_dir, product)`
- `load_product_model(app_dir, product_id)`
- `list_product_records(app_dir)`
- `migrate_legacy_json(app_dir)`

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_erp_db -v`

Expected: all tests pass.

### Task 2: Web App Minimum SQLite Wiring

**Files:**
- Modify: `erp_web_app.py`
- Test: `tests/test_erp_web_db_integration.py`

- [ ] **Step 1: Write failing integration test**

Test that saving a product through `erp_web_app.save_product()` also writes to SQLite and `load_products_index()` returns SQLite records.

- [ ] **Step 2: Run test to verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_erp_web_db_integration -v`

Expected: fails until `erp_web_app` calls `erp_db`.

- [ ] **Step 3: Wire SQLite into app startup and product APIs**

Import `erp_db`, initialize the database, migrate legacy JSON on startup, and update `save_product()`, `load_product_from_index()`, `load_products_index()`, and `upsert_products_index()` to use SQLite as the primary store.

- [ ] **Step 4: Verify**

Run:
- `.venv\Scripts\python.exe -m unittest tests.test_erp_db tests.test_erp_web_db_integration -v`
- `.venv\Scripts\python.exe -m py_compile erp_db.py erp_web_app.py product_model.py`
- `.venv\Scripts\python.exe migration_json_to_sqlite.py --check`

Expected: tests and compile pass; check reports migrated products without rewriting unrelated files.
