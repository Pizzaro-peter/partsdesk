# PartsDesk audit and request logging update

This package builds on the Excel-upload project and includes the earlier Back-button improvements. Apply the update ZIP to your existing partsdesk-fixed folder. It contains complete replacement files, with no database or virtual environment.

## Install in local development

1. Stop the running server with Ctrl+C. Close other processes using this database.
2. Download PartsDesk-Audit-Logging-Update.zip to C:\Users\Peter\Downloads.
3. Open PowerShell and go to your project:

```powershell
Set-Location "C:\Users\Peter\Downloads\PartsDesk-Fixed-Project\partsdesk-fixed"
Get-Item .\manage.py
```

4. Back up the entire current project outside that folder:

```powershell
$backupPath = "C:\Users\Peter\Downloads\PartsDesk-Before-Audit-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath "C:\Users\Peter\Downloads\PartsDesk-Fixed-Project\partsdesk-fixed" -Destination $backupPath -Recurse
Get-Item "$backupPath\data\db.sqlite3"
```

5. Apply the update:

```powershell
Expand-Archive -LiteralPath "C:\Users\Peter\Downloads\PartsDesk-Audit-Logging-Update.zip" -DestinationPath . -Force
$env:PARTSDESK_DB = "sqlite"
$env:PARTSDESK_DEBUG = "1"
$env:PARTSDESK_TZ = "Africa/Lusaka"
```

6. Apply the database migration and check the project:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Expect core.0005_auditlog_actor_name_auditlog_changes_and_more... OK. If Back buttons had not yet been applied, this update installs those too. No new Python packages are needed beyond the Excel-upload requirements already installed. Existing audit entries are preserved and assigned their originating tenant.

7. Run the tests, one command at a time:

```powershell
.\.venv\Scripts\python.exe run_uat.py
.\.venv\Scripts\python.exe manage.py test uat_concurrency --settings=uat_concurrency_settings --noinput --verbosity 2
.\.venv\Scripts\python.exe uat_upgrade.py
node .\uat_pos_logic.cjs
```

Expected: 319 general acceptance tests, 5 concurrency tests, 1 legacy-upgrade result and 3 JavaScript results, all passing. The checks use disposable test databases.

8. Start the server:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

9. Visit http://127.0.0.1:8000/ and refresh with Ctrl+F5. Sign out and sign back in to generate real login/logout events. Log in as Owner or Manager and open Audit log.

## What is recorded

| Area | Recorded activity |
|---|---|
| Authentication | Successful/failed sign-in, sign-out and password changes; password values and hashes are excluded |
| Sales | Invoice, line and payment creation/changes, discounts, returns and associated stock movements |
| Stock | Stock balances before/after, opening movements, adjustments and adjustment reasons |
| Purchasing | Order and order-line creation/changes/deletion, status changes and receipts |
| Workshop | Job creation/changes/status, issued/removed parts, labour changes, cancellation and invoicing |
| Catalogue | Parts, categories, suppliers, vehicle applications, cross-references and fitment membership changes |
| Excel uploads | Upload records and row counts, import completion, cancellation and failed requests; workbook rows are not duplicated into audit JSON |
| Administration | Settings, branches, user records, role/active-status changes, branch/group/permission memberships and active-branch switching |
| Reports | Export requests and print requests |
| Access | Denied/rejected requests, including restricted roles, cross-tenant record lookups and CSRF failures |
| Requests | Application page visits and API/auth requests, HTTP result, actor, branch, route, record IDs, IP, duration and request ID |

Success events for business changes commit in the same transaction as the change. If the change rolls back, its success events also roll back. Failed HTTP requests are recorded separately after the business transaction. Repeat POS checkout or catalogue confirmation records each HTTP attempt without creating another sale/import.

Some operations produce multiple linked entries (for example, a sale creates an invoice, invoice lines, payment and stock movements). Use the request ID to trace a single web action. Calls made directly by services/commands have an actor when explicitly supplied; unattributed programmatic changes are labelled System and have no browser request ID.

## Use the screens

- **Audit log** shows business, authentication, security and activity events.
- **Request log** is a separate button on that page; it includes routine browsing and API calls.
- Filter by branch, user, result, action/route, date or record/user/request ID.
- Dates and displayed/exported timestamps use Zambia time, UTC+02:00.
- Branch filtering defaults to the active branch. Owners additionally see business-wide entries such as known-account login failures. All assigned branches remains limited to branches the viewer is authorised to access.
- Managers see assigned branch activity; owners can review all their business's branches. Other tenants' records and tenantless platform events are excluded.
- **Export filtered CSV** exports the same scoped selection. Dangerous spreadsheet formula prefixes are escaped.
- Entries have no edit/delete controls. Accounts referenced by logs are protected against deletion; deactivate an account instead of deleting it.

## Privacy and practical limits

Request bodies, query strings, cookies, session tokens, password values/hashes and raw credential submissions are never serialized. Sensitive/free-text fields such as contact information, descriptions and payment references are omitted; when these fields change, their field names are recorded without their contents. Selected business text, such as part names and adjustment/return reasons, remains visible to authorised reviewers.

Unknown-account login failures are tenantless, record no submitted username/password, and are available only to authorised platform administrators in read-only Django admin. A known-account failure is associated with its business without claiming that the account holder authenticated.

IP addresses come from REMOTE_ADDR. Forwarded headers are not trusted automatically; a reverse proxy can therefore appear as the client until deployment configuration is reviewed.

Print entries mean a print view or browser print preview was requested. They do not prove physical printing. Manual print telemetry uses a browser beacon and is best effort; it may be absent if the browser/network blocks it. The app cannot observe browser-only actions such as closing a tab, editing an unsaved field or every keyboard shortcut.

This update covers application requests and ordinary audited model/service operations. Direct SQL, external tools and bulk ORM writes bypass signal-based business events. The application's zero-stock cache initialization uses bulk writes and is represented by the parent creation event; explicit opening stock and subsequent changes are logged. New business operations must continue to use the audited save/service paths.

Append-only enforcement is at the application/ORM layer. Database administrators can still alter a database outside the application. Log history begins when this update is installed; it cannot reconstruct past unrecorded actions. There is no automatic log purge in this update. Request logs will grow; storage, backups, retention and off-host log copies should be planned before production deployment.

## Manual UAT

- [ ] Sign in, sign out, and sign in again; see auth.login/auth.logout entries.
- [ ] Enter the wrong password for a known account; see auth.login_failed under its business.
- [ ] Make a sale and inspect its sale, line, payment and stock entries using the request ID.
- [ ] Adjust stock; verify the previous/new balances and reason.
- [ ] Add/remove a job part and labour line; verify events and stock movements.
- [ ] Receive a purchase order and check order, line and stock changes.
- [ ] Edit only a description; see a redacted-field change without the description content.
- [ ] Change a price, supplier or fitment; check the appropriate events.
- [ ] Upload, cancel and confirm catalogue files; verify status events and completion summary.
- [ ] Create staff and change accessible account settings; verify no password is present.
- [ ] Export a report and open print preview; inspect export/print-request events.
- [ ] Open Request log; verify a page visit has a route/status and the same request ID as related events.
- [ ] Test action/date/user/result filters and CSV export.
- [ ] Sign in as Counter; Audit log access must be denied and recorded.
- [ ] Sign in to another tenant; the first business's logs must be absent.
- [ ] Verify Back buttons and Excel upload still work.
