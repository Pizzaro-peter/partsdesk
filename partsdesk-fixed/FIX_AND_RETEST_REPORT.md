# PartsDesk fix and retest report

30 September 2026. Based on the uploaded `partsdesk-Multi tenant.zip` (SHA-256 `1c0355f3146d8a2788776234b87c626aa76711da2df0ec6ec7cba8f392dcbad7`).

**All 16 reported issues were corrected. All 263 automated checks pass: zero failures, errors or skips.** The selected concurrency failure now restores one unit exactly once when two prefetched copies are removed concurrently. The other worker receives a safe already-removed error.

| Test group | Passed | Failed |
| --- | ---: | ---: |
| Original Django suite | 9 | 0 |
| Existing expanded regression suite | 220 | 0 |
| Additional targeted fix regressions | 26 | 0 |
| SQLite concurrency tests | 4 | 0 |
| POS JavaScript logic with DOM stub | 3 | 0 |
| Representative legacy migration preservation | 1 | 0 |
| **Total** | **263** | **0** |

The original 236-check baseline now passes, with 27 additional checks. Tests exercise both complete business journeys and the same 115 role/page combinations. The existing nine app tests were unchanged. Checkout test fixtures now include the newly required request UUID; the replay assertion still requires one invoice, and added tests prove that distinct IDs allow two identical legitimate sales. Invalid numerical request fixtures also include a valid ID so they test the numerical validation itself.

## Fixes

| Issue | Correction | Files |
| --- | --- | --- |
| D01 | Malformed input handling | core/utils.py; sales/views.py; core/views.py; stock/views.py; workshop/views.py |
| D02 | Finite values, precision and size limits | core/utils.py; sales/services.py; stock/services.py; workshop/services.py |
| D03 | Persistent checkout identity, replay and conflict handling | sales/models.py; sales/services.py; sales/views.py; sales/migrations/0004_checkoutrequest.py; static/js/pos.js; templates/sales/pos.html |
| D04 | Reload job-part row after locking job; restore once | workshop/services.py |
| D05 | Reprice cart when customer is cleared | static/js/pos.js |
| D06 | Reject negative prices/imports and invalid settings | catalog/forms.py; core/views.py; catalog/management/commands/import_parts.py; financial services |
| D07 | Decimal invoice subtotal | sales/models.py; templates/sales/sale_detail.html |
| D08 | Branch cost and margin in details/default PO cost | catalog/views.py; templates/catalog/part_detail.html; stock/views.py |
| D09 | Valid CSRF-protected POST form for PO removal | templates/stock/po_detail.html |
| D10 | Counter ownership enforced for payment view and service | sales/views.py; sales/services.py |
| D11 | Escape formula-leading CSV text; preserve numeric values | core/utils.py |
| D12 | Hide workshop queries, cards and counts from storekeeper | core/views.py; templates/core/dashboard.html |
| D13 | Hide cancellation form from unauthorized mechanic | templates/workshop/job_detail.html |
| D14 | Show field and non-field job validation errors | templates/workshop/job_detail.html |
| D15 | Initialize destination branch stock for shared SKU once | catalog/management/commands/import_parts.py |
| D16 | Atomic import rollback on invalid row | catalog/management/commands/import_parts.py |

## Evidence and practical limits

- Python 3.12.14, Django 6.1.1, SQLite, Linux. All server tests use disposable test databases.
- Django system check, migration drift check, Python compilation and POS JavaScript syntax checks pass. Static collection processed 133 files successfully.
- Four bounded, two-worker SQLite concurrency tests pass: sell the last unit once, prevent overpayment, restore a removed job part once, and create one invoice for simultaneous submissions sharing a checkout key. These are not a load test or PostgreSQL proof.
- The added checkout migration applied to a separate copy of the uploaded database. Existing records in 35 tables were preserved; only normal model metadata was added (one content type and four permissions). The new checkout-request table was empty. Database integrity and foreign-key checks pass.
- The representative legacy migration preservation test still passes with the new migration included.
- Updated code is supplied as a separate patch and complete-source archive. No edits were made to the original uploaded archive or your Windows installation. Neither update archive contains user databases, credentials, virtual environments or compiled assets.
- Full browser checks remain outstanding: the previous Chromium download failed, and no real browser execution was performed. POS checks use a minimal DOM stub, and form/CSRF checks use Django's HTTP client. Actual browser interactions, printer/scanner behavior, mobile layout and receipt appearance still need manual UAT. PostgreSQL and production hosting/security configuration were not exercised; the previously reported HSTS deployment warning is outside these functional fixes.

## Apply the update

Use `PartsDesk-Fixes.zip` to replace matching source files in your existing project, then run `manage.py migrate` before restarting. See `PartsDesk-Apply-Fixes.md` for the exact Windows commands and file paths. Copy all related files together, including the new migration and updated POS JavaScript/template.

The updated `PartsDesk-UAT-Results.csv` keeps the original **Result** and failure **Details** as history. Its **Retest result** column is Pass for all 263 checks. The 27 newly added checks have a blank original Result and a comment explaining that they were added after the baseline.

After installing locally, complete browser UAT before considering deployment.
