# Public KAP PIT Collector Contract

Status: **PROTOTYPE CONTRACT — NOT A REAL 60-CUTOFF SOURCE PACKAGE**

Issue: #24

## Purpose

This layer defines the narrow source contract required to collect historical KAP notifications without introducing current-state or restatement hindsight into the 60-cutoff Total Rasyo replay.

It does **not** claim that the 2021-08..2026-07 source package has been collected. It does **not** authorize V24-G `READY`, portfolio simulation, or any historical performance result.

## Authority model

The authoritative unit is an individual public KAP notification snapshot, not a current summary page and not the bulk `.rar` archive.

Every accepted raw notification snapshot must retain:

- stable numeric KAP `notification_id`;
- official HTTPS KAP notification URL whose path contains the same ID;
- exact raw response bytes;
- capture timestamp;
- SHA256 of the exact raw bytes;
- exact KAP `Gönderim Tarihi` parsed as `Europe/Istanbul`;
- expected historical ticker supplied by the caller and verified to occur in the snapshot;
- notification type;
- report year / period when the notification is a financial report;
- correction flag and previous-notification date when KAP supplies them.

No current-state ticker lookup may silently replace the caller-provided historical ticker identity.

## Enumeration

`extract_notification_ids_from_query_html()` is deliberately an offline parser. It extracts stable numeric `/Bildirim/<id>` links from a captured KAP query-result page, preserves source order, and removes duplicate links.

This is only the parsing half of deterministic enumeration. Before Issue #24 can close, an at-scale collector must also prove a deterministic query/pagination strategy for the full target window rather than relying on search-engine discovery.

## Raw snapshot and hashing

`capture_public_kap_snapshot()`:

- accepts only byte content;
- accepts only official `https://kap.org.tr` / `https://www.kap.org.tr` notification URLs;
- requires URL notification ID to match the supplied ID;
- rejects empty, invalid UTF-8, or over-limit payloads;
- hashes the exact bytes with SHA256 without normalization.

`snapshot_manifest_row()` emits the machine-readable `PUBLIC_KAP_PIT_RAW_SNAPSHOT_V1` identity row.

A future real source package must commit or otherwise immutably preserve the exact raw snapshot bytes and a manifest. Re-rendered text, browser extraction, screenshots, search snippets, or hand-written HTML are not admissible source evidence.

## Metadata and correction chronology

`parse_public_kap_notification_metadata()` extracts only source metadata needed for PIT chronology. If KAP marks a notification as a correction, the prototype fails closed when the previous-notification date is missing or malformed.

Financial-report parsing is a stricter wrapper: `parse_public_kap_financial_report()` requires notification type `FR` plus a real year and period.

Correction/amendment notices may be retained as metadata records even when they are not themselves financial-report snapshots.

## Cutoff selection

`select_visible_financial_report_versions()`:

- requires a timezone-aware cutoff;
- rejects duplicate notification IDs;
- validates official URL/hash/ticker identity for every financial-report record;
- removes every record with `published_at > cutoff_at`;
- for a `(ticker, report_year, report_period)` key, selects the latest **visible** financial-report version only;
- therefore never substitutes a later correction that was not yet public at the historical cutoff.

This is an information-availability rule, not a statement that the latest eventually published version is economically or accounting-wise superior.

## Test fixture warning

`tests/test_public_kap_pit.py` uses minimal structural HTML strings that reproduce verified KAP field labels and known example timestamps. Those strings are **test fixtures only**. They are not captured KAP source files and must never be counted toward real historical coverage.

## Explicit non-goals of this PR

This prototype does not yet provide:

- live KAP HTTP fetching;
- deterministic multi-page query construction for the entire 2021-08..2026-07 window;
- committed real raw KAP snapshots;
- statement-row/taxonomy extraction at production coverage;
- bank/non-bank/holding/GYO/insurance/financial semantic mapping coverage;
- historical ticker-code lineage binding for every company;
- complete gap/rejection inventory;
- construction of `HistoricalPitTotalRasyoCutoffInput` objects;
- V24-G `READY`.

## Closure path for Issue #24

Before the real 60-cutoff score set is authorized, a later source-package PR must prove at minimum:

1. deterministic KAP enumeration for all relevant historical companies and reporting periods;
2. exact raw snapshot capture + SHA256 manifest;
3. reproducible parser output from those committed raw snapshots;
4. correction/version chronology with mutation-protective tests;
5. ticker/company identity across code changes;
6. schema-family coverage and explicit parser gaps;
7. exact PIT selection using the already-authorized `TOTAL_RASYO_MONTHLY_OPEN_V1` cutoff schedule;
8. every required company/month ending in either usable real inputs or an explicit rejection.

Until those conditions are audited, `PUBLIC_KAP_PIT_ROUTE = FEASIBLE_FOR_COLLECTOR_PROTOTYPE` remains a feasibility status only.
