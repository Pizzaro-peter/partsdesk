# Excel catalogue upload verification — 1 October 2026

Tested locally on Linux, Python 3.12, Django 6.1.1, openpyxl 3.1.5 and SQLite. Windows instructions are supplied; this build has not been run on Peter's Windows computer or PostgreSQL.

| Suite | Passed | Failed / errors / skipped |
|---|---:|---:|
| General acceptance runner (255 existing + 28 Excel cases) | 283 | 0 |
| Concurrent transactions (4 existing + same-upload confirmation race) | 5 | 0 |
| Legacy database migration preservation | 1 | 0 |
| POS JavaScript totals | 3 | 0 |
| Total | 292 | 0 |

Django system checks passed. Migration drift check found no missing migrations. Python compilation and collectstatic passed. The Excel template was inspected and visually rendered; headers, empty input rows, Text identifier formats and valid dropdown choices were verified.

The new tests cover tenant/branch/user isolation, role access, CSRF, POST-only actions, preview without catalogue writes, related records and fitment, repeat confirmation, concurrent confirmation, cancellation, expiry, changes between preview and confirmation, full rollback after a second-row failure, opening stock history and preservation of existing average cost, duplicate identifiers, malformed files, formulas/errors, numeric identifiers, decimal limits, invalid choices, maximum row count, unpacked-size limits and unsafe XML.

Separately, catalog.0006 was applied to a disposable copy of the fixed project's database. Every row in its 34 existing non-metadata tables remained unchanged; the new import table was empty. Django's normal content type, permission and migration metadata were added. The source database was opened read-only for the copy.

The first acceptance run reported the existing missing-staticfiles warning; collectstatic was subsequently run successfully. Browser-level visual UAT remains to be performed with the checklist in EXCEL_UPLOAD_GUIDE.md. Automated HTTP integration checks exercised the upload and confirmation forms using Django's test client.
