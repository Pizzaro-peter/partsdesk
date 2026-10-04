# PartsDesk — multi-tenant spare parts and workshop management

Django application for multiple independent businesses (tenants). Each business may have multiple branches. Catalog items, categories, suppliers, fitment, customers, pricing defaults, and shop settings belong to a business. Each branch has independent stock balances, shelf locations, reorder thresholds, moving-average cost, sales, purchase orders, job cards, document numbers, and audit entries.

## Setup

Use Python 3.11+ and PostgreSQL for hosted use. SQLite remains available for local development.

```bash
python -m venv .venv
# Activate the environment: source .venv/bin/activate (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py create_tenant --name "Example Auto Spares" --slug example --owner example_owner
python manage.py runserver
```

`create_tenant` prompts securely for the owner password, creates the first `MAIN` branch, and configures shop settings. Sign in as that owner and use **Branches** to add branches, **Staff** to create employees and assign their allowed branches. Owners can switch to any active branch in their business. Staff see only their assigned active branches. Branch selection is stored in the session and rechecked on every request.

To import parts, specify both the business and destination branch:

```bash
python manage.py import_parts parts.csv --tenant example --branch MAIN
```

An optional demo dataset can be seeded into an existing business:

```bash
python manage.py seed_demo --tenant example --branch MAIN
```

Demo passwords are predictable and must never be used for a live tenant. Do not run the demo command against a production database.

## Upgrading an existing single-shop installation

Back up the database before running migrations. The migration creates an `original-business` tenant and `MAIN` branch, assigns existing staff and records to them, copies each part's previous stock balance and reorder settings into branch stock, and removes shop-owner platform superuser privileges. The old shop owner can use the PartsDesk owner interface, while a separate platform administrator can be created with `python manage.py createsuperuser` if Django admin is required. Verify the migrated stock ledger and invoice totals on a working copy before upgrading a live deployment.

## Hosted deployment

Configure `PARTSDESK_DB=postgres` and the PostgreSQL `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, and `PGPORT` variables. Set `PARTSDESK_SECRET_KEY` to a private random value, `PARTSDESK_DEBUG=0`, and `PARTSDESK_ALLOWED_HOSTS` to explicit hostnames. Serve the application behind HTTPS, configure trusted CSRF origins as needed, and run Django's `check --deploy`. TLS proxy settings need to reflect your actual hosting setup. Use a production WSGI server and serve collected static files. Back up PostgreSQL and test restoration regularly.

The delivered ZIP contains the migrated local SQLite database so the attached shop data remains available. Keep that file out of Git and do not deploy demo users or their passwords. The ZIP excludes the virtual environment, `.idea`, and generated static files.

## Tenant and branch boundaries

- Every normal page and API requires an active, authorized branch.
- Catalog and customers are tenant-wide. Stock, sales, orders, jobs, reports, and exports are branch-specific.
- SKU and barcode uniqueness is per tenant. Invoice, PO, and job numbers are per branch. Numbers may repeat in different branches; include branch identity in external integrations.
- All stock changes use `stock/services.py`; quantity and ledger rows are updated atomically. Sales and workshop services validate tenant and branch relationships before writing.
- Customer credit balance is tenant-wide. The receivables report is for the currently selected branch.
- A central catalog cost is an initial cost for a newly created branch stock record; receipts update the branch's own moving-average cost. Retail and trade prices are shared business-wide.
- Branch stock transfers, branch-level price overrides, consolidated cross-branch reports, staff account editing, and self-service tenant sign-up are not included in this version.

## Verification

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
```

The tests cover cross-tenant catalog and checkout access, separate branch stock, and branch switching permissions.

## UAT fixes — 30 September 2026

Apply `sales/0004_checkoutrequest` with `python manage.py migrate` before using the updated POS. Each POS page supplies a unique `request_id`; retries with that ID return the original invoice. A different ticket must use a fresh ID. External checkout clients must send a UUID in the JSON `request_id` field. Reusing a committed ID with different JSON returns HTTP 409. Opening a fresh POS page creates a new ID, so two legitimate identical sales remain possible.

CSV imports are atomic: any invalid row rolls back the entire file. Nonnegative prices, quantities and reorder values must fit two decimal places. Opening stock is applied once per branch/part, when its balance is zero and it has no movement history. This also covers a shared SKU already present in another branch. Reimporting an established or previously depleted item does not add stock; use Stock Adjustment or purchasing receipts for later changes.

Run the complete server regression suite with `python run_uat.py`, the disk-backed SQLite concurrency checks with `python manage.py test uat_concurrency --settings=uat_concurrency_settings --noinput`, the representative legacy migration check with `python uat_upgrade.py`, and POS logic checks with `node uat_pos_logic.cjs`. Use `PARTSDESK_DB=sqlite` and development settings for these supplied test scripts. See `APPLY_FIXES.md` for Windows commands, the change list and remaining manual UAT steps.
