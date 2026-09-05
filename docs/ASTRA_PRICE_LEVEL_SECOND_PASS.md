# Price-level evidence second pass — 2026-09-05

Status: **implementation hardening; #39 remains open**.
Reviewer: Codex/Astra. This is not a Claude or cross-model review.

## Findings corrected

The inherited price-level helper accepted a caller-provided completeness date
without a source inventory or publication boundary. Direct construction of its
public price dataclass also bypassed price basis/positive-value validation.

Market-cap materialization now requires a `PRICE_LEVEL_ACTION_COVERAGE_V1`
manifest, independently approved manifest SHA256, and the original source bytes.
The verifier binds ticker, dated unadjusted source share count, source share
document, exact date interval, enumerated event identities, source hashes and
timezone-aware source publication timestamps to the requested valuation cutoff.
The result carries the verified manifest hash. Public event dataclasses are
rebuilt to detect changed economic fields with a stale action identity.

Cash dividends leave shares unchanged. Split/bonus events multiply shares;
reverse splits require a multiplier below one. Rights issues and paid capital
increases remain explicitly unsupported because their cash economics cannot be
represented by the existing share-only event model. No invented conversion is
used. Price availability follows the existing authorized session-close policy,
including its three historical half-day exceptions.

The separate normalization helper remains arithmetic only. It is not a verified
market-cap materialization API and is documented accordingly.

## Evidence contract and limitations

A manifest must contain `contract`, `ticker`, `source_share_basis`,
`source_shares_out`, `share_source_ref`, `shares_basis_date`, `complete_through`,
`enumeration_complete`, `completeness_source_ref`, `sources`, and `events`.
Each source has `source_ref`, `source_sha256`, and `published_at`; each event
has its canonical `action_id` and explicit `economic_kind`.

Hash equality proves identity, not the truth of the collector's assertions.
Approval of a source manifest requires inspection of its completeness scope,
share-count extraction and event semantics against the original source.
The verifier is not an official KAP event collector. Synthetic manifests are
confined to unit tests. No real corporate-action completeness manifest has been
materialized or approved in this change.

## Second-pass evidence

- 45 targeted tests pass locally.
- `scripts/audit_price_level_mutations.py` runs a passing baseline followed by
  16 production-code mutations in a separate temporary checkout. Every mutant
  must fail assertions without test-collection errors; all 16 were killed.
- Two repeated test materializations produce equal serialized bytes and hashes.
  This is a contract check, not a 60-month real-data replay.
- The five sector adapters, return/momentum/alpha paths and Total Rasyo score
  engine are unchanged in this hardening commit.
- `.gitattributes` preserves LF source/evidence bytes on Windows while retaining
  the pre-existing binary rules for M3 source evidence.
- Captured `data/backtest_sources/**` bytes are exempt from text conversion,
  including original CSV files whose recorded bytes intentionally use CRLF.
- GitHub CI run `33986983642` passed on code commit
  `b928a326e458b97bb1dc8533e03e3dc57ef10ebf`: full PostgreSQL regression
  **1991 passed, 7 skipped**; BANK v4.7 **277 passed, 1 xfailed**;
  production mutation audit **16/16 killed**.
- Versioned receipts are in `data/audit/astra_2026-09-05/`. The preserved-source
  audit returns exit code 2 and `BLOCKED` for five missing source components.
  Two runs produce identical bytes. This source failure is not hidden by CI.

## Remaining acceptance work

1. Collect and independently validate real PIT share/action completeness
   evidence, including explicit unsupported capital-action gaps.
2. Connect all five valuation adapters atomically to that evidence and a new
   explicit configuration/share basis. The old adjusted-basis configuration
   and inputs have not been silently relabelled.
3. Re-evaluate the 12 THB price cells using actual matching share evidence.
4. Recover the missing original KAP catalogs and preserve the old identities.
   The source-capture SHA256SUMS lists five absent gzip components:
   `enumeration_receipts`, `experimental_inventory_6000`, `inventory_6000`,
   `report_catalog`, and `target_report_catalog`.
   The committed archive manifest initially differed from its recorded hash
   because its JSON formatting had changed. Re-serialization with two-space
   indentation and a final LF reconstructed the exact original SHA256
   `066efe98ffa6ebd67a2f529dcaceceadeedbf99df39c94b2d16dd1211907cd26`.
   Those verified bytes were restored without changing the JSON data or the
   preserved checksum claim. The other two present components also match.
5. Complete real P3/P4/P5 artifacts and their independent replays before P6.
6. #24/#36 still require historical superseded-version enumeration. KAP's
   [official site guide](https://kap.org.tr/tr/api/about/content-file/402832a195b3017d0195bd17e2e07e9a)
   describes corrected notifications, but this does not establish exhaustive
   historical version linkage for all 60 cutoffs.

No issue closure, full production readiness or backtest performance result is
claimed by this commit. CI and the remaining data gates are tracked separately.
