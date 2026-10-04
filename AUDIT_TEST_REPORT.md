# Audit logging verification — 1 October 2026

Test environment: Linux, Python 3.12, Django 6.1.1, SQLite. Windows and PostgreSQL execution have not been performed for this build.

| Suite | Passed | Failures/errors/skips |
|---|---:|---:|
| General acceptance (290 existing + 29 audit cases) | 319 | 0 |
| Concurrent transactions | 5 | 0 |
| Legacy upgrade preservation | 1 | 0 |
| POS JavaScript | 3 | 0 |
| Total | 328 | 0 |

System checks, migration drift checks, Python compilation, JavaScript syntax checks and collectstatic passed.

Audit tests verify authentication, known/unknown failed login privacy, password-change redaction, service actor attribution, sales/payments/discount metadata, stock before/after values, business rollback, rollback on audit-write failure, request metadata and credential exclusion, role/tenant denial, append-only ORM paths, assigned-branch and tenant export boundaries, CSV formula escaping, Zambia date boundaries, print access/CSRF, report exports, relationship add/remove/clear (including reverse relationships), roles/permissions/branch switching, staff-password exclusion, purchase/workshop workflows, upload row exclusion, session isolation and description-only edits with redaction.

The new migrations were additionally applied to a disposable copy of the fixed project's supplied database. Original values in all original columns of its 34 non-metadata tables remained unchanged. Existing audit tenant values were backfilled correctly and the new request log table was empty. The source database was opened read-only.

The shared Back-button and Excel-upload regression cases passed. Browser and print-beacon behaviour still require the manual Windows UAT checklist. Initial test runs showed the existing missing-staticfiles warning; collectstatic was then run and the final general suite passed without that warning.
