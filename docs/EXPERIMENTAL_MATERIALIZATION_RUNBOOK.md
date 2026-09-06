# Reconstructed experimental materialization

Profile: `EXPERIMENTAL_RISK_ACCEPTED_5Y`. The production/authoritative source
contracts are unchanged. A reconstructed catalog is a new index over verified
original primary bytes; it is not a recovery of the missing original catalogs.

## Reproduce

Use the repository root and install `requirements.txt` plus
`requirements-experimental.txt`. The native experimental parser pins lxml 6.1.3.
The original production parser remains the reference for the separate sampled
differential audit. Preserve source bytes exactly; `.gitattributes` marks source
captures as binary/non-text.

The 28 archive locations and immutable expected hashes are recorded in
`data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json`.
Download them into `private/reconstructed_kap_archives`. The reconstructed
catalog accepts only archives that match the original manifest. Its receipt
records the two excluded replacement archives and their observed hashes.

```powershell
python -m scripts.reconstruct_experimental_kap_catalog --help
python -m scripts.materialize_experimental_financial_facts --catalog data/backtest_sources/reconstructed_experimental_kap_v1/reports.jsonl.gz --raw-dir private/reconstructed_kap_archives --output-dir data/backtest_sources/experimental_semantic_facts_v1 --workers 4
python -m scripts.materialize_experimental_alias_facts --raw-dir private/reconstructed_kap_archives --output-dir data/backtest_sources/experimental_semantic_facts_v1
python -m scripts.materialize_experimental_entity_facts --raw-dir private/reconstructed_kap_archives --output-dir data/backtest_sources/experimental_semantic_facts_v1 --workers 4
python -m scripts.materialize_experimental_financial_facts --catalog data/backtest_sources/reconstructed_experimental_kap_v1/reports.jsonl.gz --raw-dir private/reconstructed_kap_archives --output-dir private/experimental_semantic_second --workers 4
python -m scripts.materialize_experimental_alias_facts --raw-dir private/reconstructed_kap_archives --output-dir private/experimental_semantic_second
python -m scripts.materialize_experimental_entity_facts --raw-dir private/reconstructed_kap_archives --output-dir private/experimental_semantic_second --workers 4
python -m scripts.audit_experimental_semantic_rebuild --first data/backtest_sources/experimental_semantic_facts_v1 --second private/experimental_semantic_second --output data/backtest_sources/experimental_semantic_facts_v1/rebuild_audit.json
python -m scripts.materialize_experimental_p3_p4 --semantic-dir data/backtest_sources/experimental_semantic_facts_v1 --output-dir data/audit/experimental_materialization_v2
python -m scripts.materialize_experimental_p3_p4 --semantic-dir data/backtest_sources/experimental_semantic_facts_v1 --output-dir private/experimental_materialization_v2_second
python -m scripts.audit_experimental_cell_rebuild --first data/audit/experimental_materialization_v2 --second private/experimental_materialization_v2_second --semantic-audit data/backtest_sources/experimental_semantic_facts_v1/rebuild_audit.json --output data/audit/experimental_materialization_v2/rebuild_audit.json
python -m scripts.audit_experimental_materialization --artifact-dir data/audit/experimental_materialization_v2 --semantic-dir data/backtest_sources/experimental_semantic_facts_v1 --raw-dir private/reconstructed_kap_archives --output data/audit/experimental_materialization_v2/independent_audit.json
python -m scripts.build_experimental_unlock_matrix --artifact-dir data/audit/experimental_materialization_v2 --output data/audit/experimental_materialization_v2/unlock_matrix.json --stage family_m3_core_wiring
python -m scripts.audit_p7_version_pair
```

The v1 materialization and its cash-only P5 diagnostic are retained as immutable
historical comparison artifacts. Do not overwrite or relabel them as v2 strategy
performance. Run P5 only after P4 contains valid scores and the monthly candidate
minimum is met.

Compare the first and second materialization receipts and every listed gzip
output by SHA256. Do not compare incomplete running gzip files. Neither repeat
builds nor passing tests establish historical source completeness.

## What the cells mean

- Membership is the actual 60-month historical cohort, sorted by signal date
  and ticker, with exactly 100 unique tickers per month.
- Financial selection compares the report's own financial period, publication
  instant and numeric disclosure ID. Comparative values in a later report do
  not become earlier-period observations.
- An observed newer replacement-archive dependency has a specific missing
  primary-bytes rejection. Its replacement facts are never mapped as accepted
  primary facts. Missing original catalog bytes are a recorded experimental
  risk, not a global financial rejection.
- Dated legal names and specialist reporting schemas can support an economic
  family. A general reporting schema alone does not prove NONFIN routing.
  Current sector snapshots cannot fill historical family or market-sector gaps.
- Official ticker changes are followed only toward predecessor reports after
  the change takes effect. The original source ticker, facts and hashes remain
  unchanged. CORE calculations may use an explicitly audited temporary ticker
  copy; changes in economic family still reject continuity.
- Archived composite member codes such as GARAN-TGB bind only their exact
  declared tokens. A separately recorded first-token technical mapping identity
  satisfies the existing semantic API while preserving raw source dimensions.
  Financial entity binding is not permission to transfer prices or nominal
  shares between share classes; composite capital/share inputs are excluded
  from the temporary CORE calculation where that proof is missing.
- Generic historical Yahoo closes are observations, not a proof of raw nominal
  valuation basis. The 12 verified THB closes have a separate actual source
  path; their remaining action-completeness rejection is recorded by calling
  the production source gate.
- Paid capital is not silently converted into nominal shares. The recovered
  dated share evidence and demonstrated capital continuity do not prove an
  exhaustive history of share-changing actions.
- M3, Ek4 and Ek9 use the existing production math on bounded data. CORE-only
  diagnostics expose their actual ratios and partial module results separately;
  they do not impersonate a complete CORE+VAL context.
- A missing verified M2 basis produces a source-contract rejection before the
  sector engine. A newly verified basis causes an explicit implementation stop
  until the real sector-engine execution is connected; it cannot silently
  receive a hard-coded rejection or invented Total score.

`p3_cells.jsonl.gz` is a financial materialization and dependency artifact.
`p4_cells.jsonl.gz` and the 60 monthly files contain actual combiner outcomes,
including explicit rejections. A month with no valid score has no numerical
ranking. `core_diagnostics.jsonl.gz` contains partial calculations and their
lineage; `p2_candidates.jsonl.gz` is the newly observed dated HOLDING/GYO cohort,
not a claim that the missing original 993 candidates were reproduced.

## Portfolio and independent audit limits

The portfolio diagnostic refuses any scored cohort until verified execution
and action inputs are connected. For a wholly rejected actual cohort it calls
the existing simulator with no buy signals, the real historical 2× net wage
contributions, and the captured XU100 OPEN/CLOSE benchmark. It independently
reconciles cash and fractional benchmark units. Zero transactions and 100%
cash are diagnostics; they are not a completed Total Rasyo investment backtest.
The final benchmark mark is the July signal day's close, not July month-end.

The independent P6 helper reconstructs membership, timing, report precedence,
source hashes, raw report identity, alias edges, fact lineage, prices and
P3-to-P4 links. It verifies monthly slices and rejects hidden scores/ranks.
It binds semantic numerical values to the versioned artifact; it does not
independently remap every raw numeric cell. Its PASS is explicitly a partial
source-to-rejection audit, not full P6 or authoritative approval.

The KORTS correction-pair research recovered two real published versions and
demonstrated an intervening-cutoff selection. That positive result does not
establish exhaustive superseded/deleted historical version enumeration.

PR #40 remains draft. Issue closures require their actual acceptance artifacts,
independently of code CI and diagnostic completion.
