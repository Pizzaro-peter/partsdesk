# PartsDesk Back buttons update

This build also includes expanded audit logging; see AUDIT_LOG_GUIDE.md for the current installation and logging instructions.

Apply this update to the PartsDesk project that already has the Excel catalogue upload feature.

## Install on Windows

1. Stop the development server with Ctrl+C. Back up your current partsdesk-fixed folder outside the project, including data.
2. Save PartsDesk-Back-Buttons-Update.zip in C:\Users\Peter\Downloads.
3. Open PowerShell and run:

```powershell
Set-Location "C:\Users\Peter\Downloads\PartsDesk-Fixed-Project\partsdesk-fixed"
Expand-Archive -LiteralPath "C:\Users\Peter\Downloads\PartsDesk-Back-Buttons-Update.zip" -DestinationPath . -Force
$env:PARTSDESK_DB = "sqlite"
$env:PARTSDESK_DEBUG = "1"
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe run_uat.py
.\.venv\Scripts\python.exe manage.py runserver
```

Expected: no system-check issues and 319 acceptance tests passing. This update needs no new dependencies or database migration. It contains complete files and excludes your database and virtual environment.

## Behaviour

The Back link appears at the top of authenticated application pages, before the existing actions. It is an ordinary link, works without JavaScript or browser history, and is hidden on printed receipts, job cards, orders and reports. The dashboard is the starting page and has no Back button.

| Page | Back destination |
|---|---|
| Part details or new part | Parts list |
| Edit part | That part's details |
| Customer details or new customer | Customers list |
| Edit customer or add customer vehicle | That customer's details |
| Invoice | Invoices list |
| Return items | That invoice |
| Job card or new job card | Job cards list |
| Purchase order or new purchase order | Purchase orders list |
| Stock adjustment | Stock ledger |
| Individual report | Reports menu |
| Categories, vehicle applications or suppliers | Parts list |
| Supplier create/edit | Suppliers list |
| Catalogue vehicle create/edit | Vehicle applications list |
| Catalogue upload | Parts list |
| Catalogue preview | Catalogue upload |
| Main sections, settings, staff, branches and password change | Dashboard |

Back returns to the parent page shown on the label. It does not restore a previous list's filters, scroll position or unsaved form entries. Use Save before leaving if you want to keep edits.

## Manual check

- Open a part and click Back to parts.
- Edit a part and click Back to part without submitting.
- Open a job card, invoice, purchase order and customer; check each Back link.
- Open a report and return to Reports.
- Open catalogue upload and its preview; check both destinations.
- Open a detail page directly in a new tab; its Back link must still work.
- Open Change password; check the shared layout and Back to dashboard.
- Use print preview on an invoice or job card; Back must not appear in the printed output.

## Code locations

- core/navigation.py: explicit parent routes and labels.
- core/templatetags/ui.py: shared template tag.
- templates/base.html: shared header button and reusable page-title blocks.
- templates/catalog/catalogue_upload.html: removes its duplicate Back link.
- templates/registration/password_change_form.html and password_change_done.html: account pages in the shared layout.
- uat_navigation.py and run_uat.py: focused navigation checks and runner integration.

## Verification

Tested on Linux using Python 3.12, Django 6.1.1 and SQLite: 319 acceptance tests passed, with no failures/errors/skips (290 pre-audit checks + 29 audit checks). Django checks and migration drift check passed. Tests cover parent destinations, direct page visits, header rendering, external referrer/query independence, invoice/return/upload links and password-change flow. Windows browser and print-preview checks remain for local UAT. An initial missing-staticfiles warning is the existing development warning; collectstatic can populate that directory if needed.
