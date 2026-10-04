# Apply the PartsDesk fixes locally

This update is for the `partsdesk-Multi tenant.zip` build reviewed on 30 September 2026. All 263 automated checks pass. The original source upload is retained as the baseline.

## Update your existing project (recommended)

1. Stop the test server with Ctrl+C. Work in the project folder containing `manage.py`.
2. Back up the full project and its `data` folder before replacing files or applying the migration. With SQLite the stopped server lets you preserve the database and any WAL sidecar files together. One PowerShell example for the data backup:

```powershell
$backupTarget = "data-backup-" + (Get-Date -Format yyyyMMdd-HHmmss)
Copy-Item .\data $backupTarget -Recurse
```

3. Extract `PartsDesk-Fixes.zip` into a separate folder. Copy its files and folders into your existing project, preserving the relative paths below, and allow replacement of matching files. For example, `templates/workshop/job_detail.html` goes into your existing `templates/workshop` folder; `workshop/services.py` goes into your existing `workshop` folder. The archive supplies complete files, so no manual code merging is needed if your local source is still the tested build. If you made additional local edits after that upload, compare those files against `FIXES.patch` first.
4. The new `sales/migrations/0004_checkoutrequest.py` must be copied along with the updated Python, JavaScript and template files. In PowerShell, from the project folder, run:

```powershell
$env:PARTSDESK_DB = "sqlite"
$env:PARTSDESK_DEBUG = "1"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe run_uat.py
.\.venv\Scripts\python.exe manage.py test uat_concurrency --settings=uat_concurrency_settings --noinput --verbosity 2
.\.venv\Scripts\python.exe uat_upgrade.py
node .\uat_pos_logic.cjs
.\.venv\Scripts\python.exe manage.py runserver
```

Node is only needed for the JavaScript test script. It is not a new application dependency. If your virtual environment has a different name, substitute its Python path. The supplied tests target SQLite development settings; do not point them at a production database.

5. Open `http://127.0.0.1:8000/`, sign in using your existing account, and hard-refresh the browser with Ctrl+F5 so the updated POS script is loaded. Keep your current `data` and `.venv` folders. Neither archive includes or replaces your user database or virtual environment. The migration adds a checkout-request table; it does not change existing account credentials.

If you serve collected static assets rather than runserver assets, run `manage.py collectstatic --noinput` using the same environment after copying the update. Browser clicks, print layout and hardware workflows still require manual UAT.

## Alternative: complete source archive

`PartsDesk-Fixed-Project.zip` contains all updated source plus the test scripts under `partsdesk-fixed/`. It intentionally excludes the database and virtual environment. Use the patch archive for your existing installation, or create a fresh development folder from the complete source and restore your backed-up data there before migrating. Do not run the fresh folder expecting existing users unless your data has been restored.

## Checkout request contract

The POS now submits a UUID `request_id` for each new ticket. Retries must reuse the same ID and identical JSON. A committed request returns its existing invoice; a changed ticket using that ID receives HTTP 409. Opening a fresh POS page supplies a new ID, allowing another legitimate sale with the same items. External API clients must also send a UUID request ID. The old replay test was updated to express this request identity; the new tests separately prove that fresh IDs permit identical legitimate sales.

## Import behavior

An invalid CSV row rolls back the entire import. Financial values must be finite, nonnegative and fit two decimal places. Opening quantity is applied once for each destination branch/part with zero balance and no stock movement history, even when the SKU already exists in another branch. Reimporting existing stock or previously depleted stock will not add another opening balance. Use Stock Adjustment or purchase receipts for subsequent stock changes.

## Exact replacement paths

- `README.md`
- `catalog/forms.py`
- `catalog/management/commands/import_parts.py`
- `catalog/views.py`
- `core/utils.py`
- `core/views.py`
- `sales/migrations/0004_checkoutrequest.py`
- `sales/models.py`
- `sales/services.py`
- `sales/views.py`
- `static/js/pos.js`
- `stock/services.py`
- `stock/views.py`
- `templates/catalog/part_detail.html`
- `templates/core/dashboard.html`
- `templates/sales/pos.html`
- `templates/sales/sale_detail.html`
- `templates/stock/po_detail.html`
- `templates/workshop/job_detail.html`
- `workshop/services.py`
- `workshop/views.py`

## Supplied test files

- `run_uat.py`
- `uat_regression.py`
- `uat_fixes.py`
- `uat_concurrency.py`
- `uat_concurrency_settings.py`
- `uat_upgrade.py`
- `uat_pos_logic.cjs`
