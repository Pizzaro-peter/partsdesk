# PartsDesk Excel catalogue upload

This build also includes expanded audit logging; see AUDIT_LOG_GUIDE.md for the current installation and logging instructions.

This update adds an Excel upload screen to the fixed PartsDesk project used for your earlier UAT. Install it on your local development project first.

## 1. Stop the server and back up your project

In the PowerShell window running Django, press **Ctrl+C**. Close other processes using this project database.

Open PowerShell and go to the folder containing manage.py:

```powershell
Set-Location "C:\Users\Peter\Downloads\PartsDesk-Fixed-Project\partsdesk-fixed"
Get-Item .\manage.py
Get-Item .\.venv\Scripts\python.exe
```

Before applying the update, copy this entire project folder to a separate backup folder using File Explorer. Keep the backup outside the project you are updating. The backup must include **data** (your database) and your existing code. With the server stopped, you can also make a separate data backup:

```powershell
$backupPath = "..\PartsDesk-Data-Backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item .\data $backupPath -Recurse
```

## 2. Apply the update ZIP

Download **PartsDesk-Excel-Catalogue-Update.zip** to your Downloads folder. It contains complete replacement files and the new files. Its root matches your project root; it has no extra wrapping folder.

From the same PowerShell window:

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\PartsDesk-Excel-Catalogue-Update.zip" -DestinationPath . -Force
```

If your browser saved the ZIP with a different name, adjust that name in the command.

This package contains no database or virtual environment. Keep your existing data and .venv folders. The complete source archive, **PartsDesk-With-Excel-Upload.zip**, is also provided for a separate checkout; use the update ZIP for your current installation.

## 3. Install dependencies and migrate

```powershell
$env:PARTSDESK_DB = "sqlite"
$env:PARTSDESK_DEBUG = "1"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Expect **Applying catalog.0006_catalogueimport... OK** the first time. If already applied, Django reports no migrations to apply. The new migration adds an upload record table; your users, catalogue, invoices, jobs and stock remain in your existing database.

## 4. Run the checks locally

```powershell
.\.venv\Scripts\python.exe run_uat.py
.\.venv\Scripts\python.exe manage.py test uat_concurrency --settings=uat_concurrency_settings --noinput --verbosity 2
.\.venv\Scripts\python.exe uat_upgrade.py
node .\uat_pos_logic.cjs
```

Expected: **319** general acceptance tests, **5** concurrency tests, **1** migration preservation test and **3** POS JavaScript tests, all passing. These checks use disposable test databases. The general runner saves results under uat_results.

## 5. Start the server

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Keep this window open and visit **http://127.0.0.1:8000/**. Use your existing login. The upload is available to **Owner, Manager and Storekeeper** roles. Counter and Mechanic roles cannot import.

## 6. Upload your catalogue

1. Select the correct business branch in the application.
2. Open **Parts → Upload catalogue**. Direct local address: http://127.0.0.1:8000/catalog/upload/
3. Check the company name and branch shown on the upload screen.
4. Click **Download Excel template**.
5. Fill the **Catalogue** sheet starting at row 2. Keep the headings in row 1. The Instructions sheet explains each column; its examples are never imported.
6. Every row needs **sku** and **name**. New parts also need **sell_price**, including an explicit zero if an item is free.
7. Save the file as **.xlsx**, select it on the upload screen and choose an import mode.
8. Click **Validate and preview**. Correct any reported Excel row errors, save and upload again.
9. Review the destination branch, part codes, prices, additions/updates and opening stock warnings.
10. Click **Confirm import**. Then open Parts and check the imported records and the selected branch's stock ledger.

## Import rules

- **Add new parts only** is the default. Any existing SKU blocks the file, which protects existing records from accidental updates.
- **Add and update matching SKUs** updates matching part codes in your business and creates missing parts. SKUs are normalized to uppercase. Use one row per SKU per file.
- Optional blank cells preserve existing values during updates. To set a numeric value to zero or a Boolean to false, enter 0 or no explicitly. Blank cells cannot clear an existing supplier, barcode or other optional value.
- Maximum upload: **5 MB and 5,000 catalogue rows**. Up to 64 worksheet columns are permitted. Older .xls files, encrypted files, formulas, Excel error values, macros, external workbook links and merged cells are rejected. Remove unused formatted rows/columns beyond the limits.
- SKU, barcode and OEM identifiers must be **Text** cells. The template formats these columns as Text to preserve leading zeros. If Excel has already removed zeros, re-enter the correct identifier; formatting later cannot reconstruct it.
- Prices, quantities and reorder values must be nonnegative numbers with at most two decimal places. Price fields use your business currency settings; do not put K or other currency symbols into numeric cells.
- Catalogue names, descriptions, selling prices and supplier/category/vehicle relationships are shared by this business's branches. Other tenants cannot access this import.
- **quantity** means initial opening stock in the selected branch. It is applied only if that branch/part has zero balance and no stock movement history. If stock or history already exists, the quantity is skipped and shown in the preview/result. A previously depleted item is not reopened. Use Stock Adjustment or purchase receipts for established stock.
- Shelf location and reorder settings affect the selected branch only. Existing branch average costs are preserved. New opening stock uses the imported catalogue cost.
- Supplier/category names are created within the current business when missing. The importer does not create detailed supplier contact information or OEM cross-reference lists; use their existing screens for those details.
- Optional vehicle fitment needs vehicle_make, vehicle_model, year_from and year_to together. engine is optional. A link is added without removing current links. For a second application of the same part, use a separate update upload or the part's fitment screen.
- Preview creates an upload record but changes no catalogue or stock. It expires after **one hour**. Only the uploading user can confirm it, while using the same branch with appropriate access.
- Confirmation validates current data again and saves all rows in one transaction. A failure leaves no partial catalogue/stock changes. Repeat confirmation of an already successful upload returns its result without importing twice.

## Manual UAT checklist

- [ ] Owner can see the Upload catalogue button; Counter cannot.
- [ ] Company name and branch are correct on both upload and preview screens.
- [ ] Download the template, add two real test part codes with prices, and upload in add-only mode.
- [ ] Before confirmation, those parts are absent from the Parts list.
- [ ] Confirm; both parts appear, prices are correct and only the selected branch receives opening quantities.
- [ ] Upload an existing SKU in add-only mode; verify an error and no changes.
- [ ] Use update mode to change a price; leave optional fields blank and verify they are preserved.
- [ ] Repeat the update with quantity; verify existing branch stock is not increased.
- [ ] Enter a negative price, duplicate SKU or numeric barcode; verify row errors and no import.
- [ ] Cancel a preview; verify its data is not imported.
- [ ] Switch to another branch; verify another branch's upload cannot be confirmed there.
- [ ] Check the Stock ledger and Audit log for the completed import.

## Files changed

- requirements.txt: adds openpyxl and defusedxml.
- catalog/models.py and catalog/migrations/0006_catalogueimport.py: upload records and migration.
- catalog/excel_import.py: parsing, validation, stock rules and atomic confirmation.
- catalog/forms.py, catalog/views.py, catalog/urls.py: upload, template, preview, confirm and cancel routes.
- templates/catalog/part_list.html: upload action.
- templates/catalog/catalogue_upload.html and catalogue_preview.html: new screens.
- catalog/resources/PartsDesk-Catalogue-Template.xlsx: template available inside the app.
- uat_excel.py, uat_concurrency.py and run_uat.py: new tests and runner integration.

All Python/HTML files in the update are complete files, so you do not need to insert individual snippets.
