MKK_SUITE_CONFIG ?=
MKK_SUITE_CHECKED_AT ?=
MKK_SUITE_START ?=
MKK_SUITE_END ?=

.PHONY: audit-change-impact-e2e audit-change-impact audit-total-rasyo test test-all test-bank-v47 self-audit-kap-bank-e2e self-audit-kap-bank-batch self-audit-kap-bank-persistence preview-kap-bank-batch postgres-bank-acceptance core migrate fetch-kap-universe sync-kap-universe check-mkk-kap sync-mkk-kap sync-mkk-kap-resume extract-kap-facts optimize-weights fill-sector-group ingest-universe ingest-historical-universe ingest-prices ingest-index ingest-fin validate-core calc-ratios run-daily backtest fetch-yf-prices fetch-yf-index build-universe map-kap-semantic-facts materialize-bank-facts self-audit-semantic-bank-facts run-kap-bank-batch run-kap-bank-db show-bank-ranking self-audit-kap-bank-db self-audit-mkk-runtime self-audit-mkk-onboarding validate-mkk-contract plan-mkk-backfill validate-mkk-suite-example plan-mkk-suite-example self-audit-mkk-suite self-audit-mkk-suite-sync check-mkk-suite-readiness sync-mkk-suite materialize-company-facts calc-company-ratios self-audit-nonbank-core run-nonfin-batch self-audit-nonfin-valuation ingest-holding-nav run-holding-batch self-audit-holding-valuation ingest-gyo-nav run-gyo-batch self-audit-gyo-valuation ingest-insurance-metrics run-insurance-batch self-audit-insurance-valuation ingest-fi-metrics run-fi-batch self-audit-fi-valuation

test:
	pytest -q

test-all: test test-bank-v47

test-bank-v47:
	cd src/analytics/bank_v47 && PYTHONPATH=. pytest -c /dev/null -q ../../../vendor/v47_roe_belirsizlik

core:
	psql -f sql/010_create_core_tables.sql

migrate:
	psql -f sql/000_create_schemas.sql
	psql -f sql/001_create_analytics_tables.sql
	psql -f sql/003_decile_thresholds.sql
	psql -f sql/005_backtest_tables.sql
	psql -f sql/006_trailing_alpha_period_m2_tables.sql
	psql -f sql/007_valuation_and_backtest_ext.sql
	psql -f sql/011_bank_valuation_integration.sql
	psql -f sql/013_bank_batch_m2_integration.sql
	psql -f sql/015_kap_official_ingestion.sql
	psql -f sql/016_semantic_sector_materialization.sql
	psql -f sql/017_kap_bank_batch_persistence.sql
	psql -f sql/018_kap_bank_database_workflow.sql
	psql -f sql/019_mkk_kap_runtime_safety.sql
	psql -f sql/020_mkk_suite_sync.sql
	psql -f sql/021_company_semantic_materialization.sql
	psql -f sql/022_nonfin_relative_valuation.sql
	psql -f sql/023_holding_nav_valuation.sql
	psql -f sql/024_gyo_nav_valuation.sql
	psql -f sql/025_insurance_valuation.sql
	psql -f sql/026_financial_institution_valuation.sql
	psql -f sql/027_total_rasyo_orchestrator.sql
	psql -f sql/028_total_rasyo_run_registry.sql
	psql -f sql/029_total_rasyo_status_taxonomy.sql
	psql -f sql/030_total_rasyo_run_scope.sql
	psql -f sql/031_total_rasyo_restate.sql
	psql -f sql/032_impact_plan.sql
	psql -f sql/033_impact_runtime_roles.sql
	psql -f sql/034_module_production_lineage.sql
	psql -f sql/035_reconciliation_impact_vs_actual.sql
	psql -f sql/036_total_rasyo_module_input.sql
	psql -f sql/037_reconciliation_module_freshness.sql
	psql -f sql/038_total_rasyo_restate_hardening.sql
	psql -f sql/039_restate_pit_reconciliation.sql
	psql -f sql/040_historical_universe_membership.sql

fill-sector-group:
	psql -f sql/004_fill_sector_group.sql

ingest-universe:
	python -m src.app.cli ingest-universe --file data/universe_stocks.csv

ingest-historical-universe:
	PYTHONPATH=. python scripts/ingest_historical_universe.py --file data/universe_membership_history.csv

ingest-prices:
	python -m src.app.cli ingest-prices --file data/prices_daily.csv

ingest-index:
	python -m src.app.cli ingest-index --file data/index_prices_daily.csv

ingest-fin:
	python -m src.app.cli ingest-fin --file data/financials_quarterly.csv

validate-core:
	python -m src.app.cli validate-core

calc-ratios:
	python -m src.app.cli calc-ratios --ratios config/ratios.json

run-daily:
	python -m src.app.cli run-daily --asof 2026-02-20 --ratios config/ratios.json --sectors config/sectors.json --weights config/weights.json

backtest:
	python -m src.app.cli backtest --start 2024-01-01 --end 2026-01-01 --rebalance step5 --hold 20 --ensure-scores

optimize-weights:
	python -m src.app.cli optimize-weights --start 2024-01-01 --end 2026-01-01 --hold 20 --step 0.10 --objective ic

fetch-yf-prices:
	python -m src.app.cli fetch-yf-prices --tickers THYAO,ASELS --start 2020-01-01 --end 2026-02-22 --symbols config/yfinance_symbols.json

fetch-yf-index:
	python -m src.app.cli fetch-yf-index --index XU100 --start 2020-01-01 --end 2026-02-22 --symbols config/yfinance_symbols.json

build-universe:
	python -m src.app.cli build-universe --tickers-file data/tickers.txt --out data/universe_stocks.csv --universe-map config/universe_mapping.json

fetch-kap-universe:
	python -m src.app.cli fetch-kap-universe --out data/universe_kap.csv

sync-kap-universe:
	python -m src.app.cli sync-kap-universe --out data/universe_kap.csv

sync-mkk-kap:
	python -m src.app.cli sync-mkk-kap --api-config config/mkk_kap_endpoints.json --start 2026-08-04T00:00:00+03:00 --end 2026-08-04T23:59:59+03:00

check-mkk-kap:
	python -m src.app.cli check-mkk-kap --api-config config/mkk_kap_endpoints.json --start 2026-08-04T00:00:00+03:00 --end 2026-08-04T00:05:00+03:00

sync-mkk-kap-resume:
	python -m src.app.cli sync-mkk-kap --resume --api-config config/mkk_kap_endpoints.json --end 2026-08-05T00:00:00+03:00 --overlap-seconds 300 --max-window-hours 24

extract-kap-facts:
	python -m src.app.cli extract-kap-facts --mapping-config config/mkk_kap_financial_facts_mapping.json --analysis-at 2026-08-04T19:00:00+03:00

map-kap-semantic-facts:
	python -m src.app.cli map-kap-semantic-facts --semantic-config config/kap_bank_semantic_mapping.json --source-mapping-profile KAP_FINANCIAL_FACTS --source-mapping-version 1 --analysis-at 2026-08-04T20:00:00+03:00

materialize-bank-facts:
	python -m src.app.cli materialize-bank-facts --derivation-config config/bank_fact_derivation.json --analysis-at 2026-08-04T20:00:00+03:00 --anchor 2026-06-30

self-audit-semantic-bank-facts:
	PYTHONPATH=. python scripts/self_audit_semantic_bank_facts.py

self-audit-kap-bank-e2e:
	PYTHONPATH=. python scripts/self_audit_kap_bank_e2e.py

preview-kap-bank-batch:
	@python -m src.app.cli preview-kap-bank-batch --file test_fixtures/kap_bank_batch_e2e/disclosures.jsonl --contexts-config test_fixtures/kap_bank_batch_e2e/contexts.json --mapping-config config/mkk_kap_financial_facts_mapping.example.json --semantic-config config/kap_bank_semantic_mapping.official_v1.json --derivation-config config/bank_fact_derivation.official_v1.json --analysis-at 2026-05-15T20:00:00+03:00 --anchor 2026-03-31

self-audit-kap-bank-batch:
	PYTHONPATH=. python scripts/self_audit_kap_bank_batch.py

postgres-bank-acceptance:
	PYTHONPATH=. python scripts/run_postgres_bank_acceptance.py

run-kap-bank-batch:
	python -m src.app.cli run-kap-bank-batch --file test_fixtures/kap_bank_batch_e2e/disclosures.jsonl --contexts-config test_fixtures/kap_bank_batch_e2e/contexts.json --mapping-config config/mkk_kap_financial_facts_mapping.example.json --semantic-config config/kap_bank_semantic_mapping.official_v1.json --derivation-config config/bank_fact_derivation.official_v1.json --analysis-at 2026-05-15T20:00:00+03:00 --anchor 2026-03-31

run-kap-bank-db:
	python -m src.app.cli run-kap-bank-db --mapping-config config/mkk_kap_financial_facts_mapping.json --semantic-config config/kap_bank_semantic_mapping.official_v1.json --derivation-config config/bank_fact_derivation.official_v1.json --weights config/weights.json --analysis-at 2026-08-04T20:00:00+03:00 --horizon-days 63 --max-context-age-days 7 --pipeline-version KAP_BANK_DB_BATCH_V8 --batch-source RAW_KAP_DATABASE

show-bank-ranking:
	python -m src.app.cli show-bank-ranking --asof 2026-05-15 --horizon-days 63 --limit 20

self-audit-kap-bank-persistence:
	PYTHONPATH=. python scripts/self_audit_kap_bank_persistence.py

self-audit-kap-bank-db:
	PYTHONPATH=. python scripts/self_audit_kap_bank_db_workflow.py

self-audit-mkk-runtime:
	python scripts/self_audit_mkk_runtime.py

validate-mkk-contract:
	python -m src.app.cli validate-mkk-contract --api-config config/mkk_kap_endpoints.example.json --sample test_fixtures/mkk_contract/sample_response.example.json --checked-at 2026-08-05T00:00:00+03:00 --contract-lock-out data/mkk_contract.lock.json

plan-mkk-backfill:
	python -m src.app.cli plan-mkk-backfill --start 2026-01-01T00:00:00+03:00 --end 2026-08-05T00:00:00+03:00 --max-window-hours 24 --overlap-seconds 300 --plan-out data/mkk_backfill_plan.json

self-audit-mkk-onboarding:
	python scripts/self_audit_mkk_onboarding.py

validate-mkk-suite-example:
	python -m src.app.cli validate-mkk-suite --suite-config test_fixtures/mkk_suite/suite.json --checked-at 2026-08-05T02:00:00+03:00

plan-mkk-suite-example:
	python -m src.app.cli plan-mkk-suite-backfill --suite-config test_fixtures/mkk_suite/suite.json --checked-at 2026-08-05T02:00:00+03:00 --start 2026-01-01T00:00:00+03:00 --end 2026-08-05T00:00:00+03:00 --max-window-hours 24 --overlap-seconds 300 --plan-out data/mkk_suite_backfill_plan.json --force

self-audit-mkk-suite:
	python scripts/self_audit_mkk_suite.py

self-audit-mkk-suite-sync:
	python scripts/self_audit_mkk_suite_sync.py

check-mkk-suite-readiness:
	@test -n "$(MKK_SUITE_CONFIG)" -a -n "$(MKK_SUITE_CHECKED_AT)" -a -n "$(MKK_SUITE_START)" -a -n "$(MKK_SUITE_END)" || (echo "MKK_SUITE_CONFIG, MKK_SUITE_CHECKED_AT, MKK_SUITE_START ve MKK_SUITE_END zorunlu"; exit 2)
	python -m src.app.cli check-mkk-suite-readiness --suite-config "$(MKK_SUITE_CONFIG)" --checked-at "$(MKK_SUITE_CHECKED_AT)" --start "$(MKK_SUITE_START)" --end "$(MKK_SUITE_END)"

sync-mkk-suite:
	@test -n "$(MKK_SUITE_CONFIG)" -a -n "$(MKK_SUITE_CHECKED_AT)" -a -n "$(MKK_SUITE_END)" || (echo "MKK_SUITE_CONFIG, MKK_SUITE_CHECKED_AT ve MKK_SUITE_END zorunlu"; exit 2)
	python -m src.app.cli sync-mkk-suite --suite-config "$(MKK_SUITE_CONFIG)" --checked-at "$(MKK_SUITE_CHECKED_AT)" $(if $(MKK_SUITE_START),--start "$(MKK_SUITE_START)",--resume) --end "$(MKK_SUITE_END)" --quarantine-invalid-items

materialize-company-facts:
	python -m src.app.cli materialize-company-facts --derivation-config config/nonbank_fact_derivation.example_v1.json --analysis-at 2026-08-05T20:00:00+03:00 --anchor 2026-06-30 --no-persist

calc-company-ratios:
	python -m src.app.cli calc-company-ratios --analysis-at 2026-08-05T20:00:00+03:00 --ratios config/ratios.json --no-persist

self-audit-nonbank-core:
	python scripts/self_audit_nonbank_core.py

run-nonfin-batch:
	python -m src.app.cli run-nonfin-batch --analysis-at 2026-08-05T20:00:00+03:00 --valuation-config config/nonfin_valuation.relative_v1.json --routing-config config/sector_routing.v1.json --no-persist

self-audit-nonfin-valuation:
	python scripts/self_audit_nonfin_valuation.py


ingest-holding-nav:
	python -m src.app.cli ingest-holding-nav --file data/holding_nav.example.jsonl --no-persist

run-holding-batch:
	python -m src.app.cli run-holding-batch --analysis-at 2026-08-05T20:00:00+03:00 --valuation-config config/holding_valuation.nav_discount_v1.json --routing-config config/sector_routing.v1.json --no-persist

self-audit-holding-valuation:
	python scripts/self_audit_holding_valuation.py


ingest-gyo-nav:
	python -m src.app.cli ingest-gyo-nav --file data/gyo_nav.example.jsonl --no-persist

run-gyo-batch:
	python -m src.app.cli run-gyo-batch --analysis-at 2026-08-05T20:00:00+03:00 --valuation-config config/gyo_valuation.pd_nav_v1.json --routing-config config/sector_routing.v1.json --no-persist

self-audit-gyo-valuation:
	python scripts/self_audit_gyo_valuation.py


ingest-insurance-metrics:
	python -m src.app.cli ingest-insurance-metrics --file data/insurance_metrics.example.jsonl --no-persist

run-insurance-batch:
	python -m src.app.cli run-insurance-batch --analysis-at 2026-08-05T20:00:00+03:00 --valuation-config config/insurance_valuation.pb_pe_v1.json --routing-config config/sector_routing.v1.json --no-persist

self-audit-insurance-valuation:
	python scripts/self_audit_insurance_valuation.py

ingest-fi-metrics:
	python -m src.app.cli ingest-fi-metrics --file data/financial_institution_metrics.example.jsonl --no-persist

run-fi-batch:
	python -m src.app.cli run-fi-batch --analysis-at 2026-08-05T20:00:00+03:00 --valuation-config config/financial_institution_valuation.pb_pe_v1.json --routing-config config/sector_routing.v1.json --no-persist

self-audit-fi-valuation:
	python scripts/self_audit_financial_institution_valuation.py

audit-total-rasyo:
	python3 -m src.analytics.total_rasyo_self_audit --json SELF_AUDIT_TOTAL_RASYO.json

audit-change-impact:
	python3 -m src.analytics.change_impact_self_audit --json SELF_AUDIT_CHANGE_IMPACT_V20.json

audit-change-impact-e2e:
	python3 -m src.analytics.change_impact_e2e_audit --json E2E_AUDIT_CHANGE_IMPACT_V20.json
