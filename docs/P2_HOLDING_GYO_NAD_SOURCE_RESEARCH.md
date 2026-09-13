# P2 — HOLDING/GYO historical NAD source research

**Status:** `IN_PROGRESS`  
**Profile:** `EXPERIMENTAL_RISK_ACCEPTED_5Y`  
**Base branch:** `v24-real-data-work`  
**Base SHA:** `dd2028953bd24f4cd11d0beb2fda93b86ad94930`

This note records only source facts that are currently independently supportable. It does **not** authorize a canonical historical NAD input by itself.

## 1. Production contract that must not be weakened

The existing production contracts remain authoritative:

- `docs/GYO_VALUATION_V16.md` requires sourced, point-in-time NAD. Accounting equity or portfolio value alone must not be silently treated as NAD.
- `docs/HOLDING_VALUATION_V15.md` likewise forbids treating accounting equity as canonical holding NAD.
- Source publication must be no later than the analysis cutoff.
- Silent current-value, current-report or future-data fallback is forbidden.

The experimental book-equity routes remain separately labelled only:

- `EXPERIMENTAL_HOLDING_BOOK_EQUITY_TWO_AXIS_V1`
- `EXPERIMENTAL_GYO_BOOK_EQUITY_TWO_AXIS_V1`

They must never be renamed or represented as real/canonical NAD.

## 2. Official/industry-standard GYO NAD source identified

Türkiye Sermaye Piyasaları Birliği (TSPB) General Circular No. 46 dated **2022-08-01** introduced the `TSPB GYO Net Aktif Değer ve Varlık Değeri Tabloları Yönergesi`.

Official source:

- https://tspb.org.tr/genelgeler/tspb-gyo-net-aktif-deger-ve-varlik-degeri-tablolari-yonergesi-hk/
- PDF: https://tspb.org.tr/wp-content/uploads/2023/01/Genelge-46-eki_GYO-Net-Aktif-Deger-Yonergesi.pdf

The directive is semantically compatible with the V16 concept of NAD because its `Borç ve Sermaye Tablosu` explicitly defines:

- `(A) Sahiplik Oranına Göre Toplam Portföy Değeri`
- `(B1) Finansal Borçlar`
- `(B2) Finansal Olmayan Borçlar`
- `(B) Borçlar Toplamı`
- `(A-B) Net Aktif Değer Toplamı`
- `(C) Ödenmiş Sermaye`
- `(A-B) / C Pay Başına Net Aktif Değer`
- `Düzenlenme Tarihi`

Therefore a published TSPB table can be a **DIRECT NAD** source candidate for GYO V16, provided its actual publication timestamp, version/source identity, currency and share-basis lineage are proven for the historical cutoff.

## 3. Publication cadence and the point-in-time boundary

The same directive states that GYOs enter data twice per year, based on their latest valuation reports and independently audited 6-month and 12-month financial statements, no later than **31 March** and **31 August**.

For the first implementation year, 2022, the directive specifically states that entries based on **2021 12-month** and **2022 6-month** financial statements were to be completed no later than **2022-08-31**.

This has a critical PIT consequence:

- a table describing a 2021-12 period is **not** automatically usable at a 2021-12 or early-2022 signal;
- the historical replay may use it only after its actual public publication time is known to be `<= cutoff`;
- the 31 March / 31 August dates are deadlines, not proof of the exact publication timestamp of a specific company row;
- period date must never be substituted for publication date.

So the TSPB route is a strong real-NAD candidate for later history, but it does **not** by itself solve the 2021-08 → pre-publication part of the five-year replay.

## 4. Older SPK portfolio data is useful evidence, but not automatically NAD

SPK exposes a historical GYO portfolio-information interface:

- https://spk.gov.tr/portfoy-degerleri-GYO

It provides company/date-based historical portfolio information including `Toplam Değer (TL)` and share count.

That field must **not** be mapped directly to V16 `nav_total` without a source-level reconciliation showing the required debt/liability deductions or a source-provided direct NAD identity. `Toplam Değer` and `Net Aktif Değer` are not treated as synonyms.

This page is therefore a potential component/source-discovery route, not an authorized NAD adapter yet.

## 5. Early-period GYO research still required

For cutoffs before a usable TSPB table was publicly available, the next source search is:

1. KAP valuation reports and annual/semiannual reports published before each cutoff;
2. issuer investor-relations reports that explicitly disclose direct NAD / pay başına NAD;
3. if no direct NAD exists, all V16 `DERIVED` components with a publication timestamp and immutable source identity.

A later TSPB row must never be borrowed backwards to fill an earlier cutoff.

## 6. HOLDING result so far

No comparable standardized historical HOLDING NAD publication source has yet been proven for the full 2021-08 → 2026-07 replay.

Until such a source is found:

- canonical HOLDING NAD remains unresolved;
- the accepted experimental five-year route may use the explicitly named book-equity proxy only under `EXPERIMENTAL_RISK_ACCEPTED_5Y`;
- proxy output must carry an explicit non-NAD label and source lineage;
- authoritative/production claims remain forbidden.

## 7. Current P2 decision

### GYO

`REAL_NAD_SOURCE_IDENTIFIED_BUT_HISTORICAL_PIT_COVERAGE_NOT_YET_PROVEN`

### HOLDING

`REAL_HISTORICAL_NAD_SOURCE_NOT_YET_PROVEN`

### P2 gate

`OPEN`

The next concrete source task is to enumerate actual historical TSPB/KAP publication rows/timestamps and determine cutoff coverage, while separately reconstructing the 12 pre-cutoff valuation-price rejections. No gate is closed by this research note.