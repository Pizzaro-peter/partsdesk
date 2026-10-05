# PartsDesk — multi-tenant spare parts and workshop management

Django application for multiple independent businesses (tenants). Each business may have multiple branches. Catalog items, categories, suppliers, fitment, customers, pricing defaults, and shop settings belong to a business. Each branch has independent stock balances, shelf locations, reorder thresholds, moving-average cost, sales, purchase orders, job cards, document numbers, and audit entries.

## Setup

Use Python 3.14 and PostgreSQL for hosted use. SQLite remains available for local development and automated tests. The CI workflow runs against the locked Python dependency set.

```bash
python -m venv .venv
# Activate the environment: source .venv/bin/activate (Windows: .venv\Scripts\activate)
pip install -r requirements-lock.txt
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

Configure `PARTSDESK_DB=postgres` and the PostgreSQL `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, and `PGPORT` variables. Set `PARTSDESK_SECRET_KEY` to a private random value, `PARTSDESK_DEBUG=0`, and `PARTSDESK_ALLOWED_HOSTS` to explicit hostnames. Serve the application behind HTTPS, configure trusted CSRF origins as needed, and run Django's `check --deploy`. TLS proxy settings must reflect the actual hosting setup.

For an empty trial deployment on Render, the repository includes `render.yaml` for a free web service and a free Render PostgreSQL database. Connect the repository through Render's Blueprint flow, choose the `render-trial` branch, and apply the Blueprint; Render provisions and connects both resources without asking you to place database credentials in the repository. Then create the first tenant with the `create_tenant` command in the service shell. The settings use Render's `RENDER_EXTERNAL_HOSTNAME` for the default allowed host and HTTPS CSRF origin. `SECURE_PROXY_SSL_HEADER` is enabled only by the explicit `PARTSDESK_TRUST_PROXY_SSL_HEADER=1` setting in the Render blueprint; use this only when the service is behind a trusted proxy that sets and sanitizes `X-Forwarded-Proto`.

The Render free web service can sleep when idle and has an ephemeral filesystem, while the free PostgreSQL database expires after 30 days. Free tiers are for trials, not live business records; export any trial data before expiry. The blueprint runs database migrations at service startup to initialize the trial; for a production service with multiple instances, use a dedicated pre-deploy migration step instead.

Enable HSTS only after HTTPS works end-to-end for every hostname and subdomain that will be covered. Start with `PARTSDESK_HSTS_SECONDS=31536000`; set `PARTSDESK_HSTS_INCLUDE_SUBDOMAINS=1` only after confirming all subdomains will remain HTTPS-only. Browser preload requires the one-year max age and `includeSubDomains`; set `PARTSDESK_HSTS_PRELOAD=1` only after meeting current preload requirements. The defaults deliberately leave HSTS off to avoid locking browsers onto an unverified deployment.

Before starting a release, take a database backup, apply migrations, and collect static assets:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Run `waitress-serve --listen=127.0.0.1:8000 config.wsgi:application` behind a local TLS-terminating reverse proxy, or use the WSGI server and bind address required by the hosting platform. Configure the proxy’s TLS and trusted-origin settings for that host. Back up PostgreSQL and test restoration regularly. Configure external uptime/error monitoring and alerting, and document the on-call owner and recovery procedure; these depend on the hosting provider and are not provisioned by the application.

Keep local SQLite database files, secrets, virtual environments, collected static files, and generated test reports out of Git. `.gitignore` prevents new untracked artifacts from being added, but does not untrack files already committed. Do not deploy demo users or their passwords.

Sign-in is protected by a five-failure username-and-IP lockout with a one-hour cool-off. If the application is behind a reverse proxy, configure Axes to use the verified client-IP header and trusted proxy count; the edge proxy must remove client-supplied forwarding headers before setting its own. Never trust arbitrary forwarded headers. An operator with access to the production environment can clear a verified user's lockout with `python manage.py axes_reset_username <username>`. Authenticator-app TOTP and one-time backup tokens are optional per-user protections, available from **My profile → Manage two-factor authentication**. Users should store generated backup tokens securely; protect database backups and OTP seeds using the hosting provider's encryption-at-rest controls. Django admin remains a separate platform-administrator surface; restrict it at the network or hosting layer and do not grant staff superuser access.

New or changed passwords must be at least 12 characters, avoid common passwords, and not closely resemble the account's personal attributes. Existing passwords continue to work until changed.

Request logs contain account and IP metadata. Choose a retention period with the business's privacy and legal requirements, preview affected rows, then run `python manage.py purge_request_logs --days 90` (preview only) and add `--delete` to perform the deletion. Schedule the reviewed command with the hosting platform's task scheduler. This command only purges request-metadata logs; append-only business/security audit events are retained and require a separately approved records-retention policy.

## Tenant and branch boundaries

- Every normal page and API requires an active, authorized branch.
- Catalog and customers are tenant-wide. Stock, sales, orders, jobs, reports, and exports are branch-specific.
- SKU and barcode uniqueness is per tenant. Invoice, PO, and job numbers are per branch. Numbers may repeat in different branches; include branch identity in external integrations.
- All stock changes use `stock/services.py`; quantity and ledger rows are updated atomically. Sales and workshop services validate tenant and branch relationships before writing.
- Customer credit balance is tenant-wide. The receivables report is for the currently selected branch.
- A central catalog cost is an initial cost for a newly created branch stock record; receipts update the branch's own moving-average cost. Retail and trade prices are shared business-wide.
- Branch stock transfers, branch-level price overrides, consolidated cross-branch reports, and self-service tenant sign-up are not included in this version. Staff account management is available to authorized owners.

## Verification

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
```

The tests cover cross-tenant catalog and checkout access, separate branch stock, and branch switching permissions. CI runs the full UAT suite, SQLite concurrency tests, the legacy upgrade check, and JavaScript validation using `requirements-lock.txt`.

## UAT fixes — 30 September 2026

Apply `sales/0004_checkoutrequest` with `python manage.py migrate` before using the updated POS. Each POS page supplies a unique `request_id`; retries with that ID return the original invoice. A different ticket must use a fresh ID. External checkout clients must send a UUID in the JSON `request_id` field. Reusing a committed ID with different JSON returns HTTP 409. Opening a fresh POS page creates a new ID, so two legitimate identical sales remain possible.

CSV imports are atomic: any invalid row rolls back the entire file. Nonnegative prices, quantities and reorder values must fit two decimal places. Opening stock is applied once per branch/part, when its balance is zero and it has no movement history. This also covers a shared SKU already present in another branch. Reimporting an established or previously depleted item does not add stock; use Stock Adjustment or purchasing receipts for later changes.

Run the complete server regression suite with `python run_uat.py`, the disk-backed SQLite concurrency checks with `python manage.py test uat_concurrency --settings=uat_concurrency_settings --noinput`, the representative legacy migration check with `python uat_upgrade.py`, and POS logic checks with `node uat_pos_logic.cjs`. Use `PARTSDESK_DB=sqlite` and development settings for these supplied test scripts. See `APPLY_FIXES.md` for Windows commands, the change list and remaining manual UAT steps.
