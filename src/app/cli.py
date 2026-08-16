from __future__ import annotations
import argparse
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass



def get_conn():
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("PostgreSQL baglantisi icin psycopg2 kurulu olmali") from exc
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "bistlab"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres")
    )


def _load_json(path: str | None):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_aware_datetime(value: str | None, name: str):
    from datetime import datetime
    if not value:
        raise SystemExit(f"--{name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"--{name} must include a timezone offset")
    return parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=[
        "build-universe", "fetch-kap-universe", "sync-kap-universe", "check-mkk-kap", "sync-mkk-kap",
        "validate-mkk-contract", "capture-mkk-sample", "plan-mkk-backfill",
        "validate-mkk-suite", "plan-mkk-suite-backfill",
        "check-mkk-suite-readiness", "sync-mkk-suite",
        "extract-kap-facts", "map-kap-semantic-facts", "materialize-bank-facts", "materialize-company-facts",
        "ingest-universe", "ingest-prices", "ingest-index", "ingest-fin",
        "ingest-bank-metrics", "ingest-bank-assumptions", "ingest-holding-nav", "ingest-gyo-nav", "ingest-insurance-metrics", "ingest-fi-metrics", "validate-core",
        "fetch-yf-prices", "fetch-yf-index",
        "calc-ratios", "calc-company-ratios", "run-daily", "run-bank-batch", "run-nonfin-batch", "run-holding-batch", "run-gyo-batch", "run-insurance-batch", "run-fi-batch", "bank-shadow-report",
        "preview-kap-bank-batch", "run-kap-bank-batch", "run-kap-bank-db",
        "show-bank-ranking",
        "backtest", "optimize-weights"
    ])
    ap.add_argument("--tickers-file", default=None)
    ap.add_argument("--out", default="data/universe_stocks.csv")
    ap.add_argument("--universe-map", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--map", default=None)
    ap.add_argument("--chunksize", type=int, default=None)
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--index", default=None)
    ap.add_argument("--yahoo", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--analysis-at", default=None)
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--thresholds", default="0.80,0.90,1.00")
    ap.add_argument("--report-out", default=None)
    ap.add_argument("--ratios", default="config/ratios.json")
    ap.add_argument("--sectors", default="config/sectors.json")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--since-period-end", default=None)
    ap.add_argument("--rebalance", default="step5", choices=["daily", "step5", "step10"])
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--ensure-scores", action="store_true")
    ap.add_argument("--step", type=float, default=0.10)
    ap.add_argument("--objective", default="ic", choices=["ic", "topq"])
    ap.add_argument("--min-m2", type=float, default=0.15)
    ap.add_argument("--api-config", default=None)
    ap.add_argument("--api-key-env", default="MKK_API_KEY")
    ap.add_argument("--stream-name", default="disclosures")
    ap.add_argument("--cursor", default=None)
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--minimum-rows", type=int, default=100)
    ap.add_argument("--mapping-config", default=None)
    ap.add_argument("--semantic-config", default=None)
    ap.add_argument("--derivation-config", default=None)
    ap.add_argument("--valuation-config", default=None)
    ap.add_argument("--routing-config", default=None)
    ap.add_argument("--source-mapping-profile", default=None)
    ap.add_argument("--source-mapping-version", type=int, default=None)
    ap.add_argument("--source", default="MKK_KAP_API")
    ap.add_argument("--notification-type", default="FINANCIAL_STATEMENT")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--retry-rejections", action="store_true")
    ap.add_argument("--contexts-config", default=None)
    ap.add_argument("--horizon-days", type=int, default=63)
    ap.add_argument("--max-context-age-days", type=int, default=7)
    ap.add_argument("--pipeline-version", default=None)
    ap.add_argument("--batch-source", default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--overlap-seconds", type=int, default=300)
    ap.add_argument("--max-window-hours", type=float, default=24.0)
    ap.add_argument("--quarantine-invalid-items", action="store_true")
    ap.add_argument("--validate-items-limit", type=int, default=5)
    ap.add_argument("--sample", default=None)
    ap.add_argument("--checked-at", default=None)
    ap.add_argument("--contract-lock-out", default=None)
    ap.add_argument("--contract-lock", default=None)
    ap.add_argument("--suite-config", default=None)
    ap.add_argument("--metadata-out", default=None)
    ap.add_argument("--plan-out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--require-api-keys", action="store_true")
    ap.add_argument("--continue-on-error", action="store_true")
    ap.add_argument("--max-windows-per-product", type=int, default=1)
    ap.add_argument("--max-product-attempts", type=int, default=1)
    args = ap.parse_args()

    col_map = _load_json(args.map)
    symbols = _load_json(args.symbols)

    if args.cmd == "build-universe":
        from src.ingest.build_universe import build_universe_csv
        if not args.tickers_file:
            raise SystemExit("--tickers-file is required for build-universe")
        n = build_universe_csv(args.tickers_file, args.out, args.universe_map)
        print(f"Wrote {n} rows to {args.out}")
        return

    if args.cmd == "validate-mkk-contract":
        if not args.api_config or not args.sample or not args.checked_at:
            raise SystemExit("--api-config, --sample and --checked-at are required for validate-mkk-contract")
        from src.ingest.api.mkk_contract import (
            load_contract_sample, validate_mkk_contract_sample, write_mkk_contract_lock,
        )
        from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError, MkkKapApiConfig
        checked_at = _parse_aware_datetime(args.checked_at, "checked-at")
        try:
            config = MkkKapApiConfig.from_json_file(args.api_config)
            sample = load_contract_sample(args.sample)
            report = validate_mkk_contract_sample(
                config, sample, checked_at=checked_at,
                validate_items_limit=args.validate_items_limit,
            )
            lock_path = None
            if args.contract_lock_out:
                lock_path = str(write_mkk_contract_lock(args.contract_lock_out, config, report))
        except (KapApiConfigError, KapApiProtocolError, ValueError, OSError) as exc:
            raise SystemExit(f"MKK contract dogrulama basarisiz: {exc}") from exc
        output = report.to_dict()
        output["status"] = "OK"
        output["contract_lock"] = lock_path
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if args.cmd == "capture-mkk-sample":
        if not args.api_config or not args.start or not args.end:
            raise SystemExit("--api-config, --start and --end are required for capture-mkk-sample")
        if args.out == "data/universe_stocks.csv" or not args.metadata_out:
            raise SystemExit("--out and --metadata-out are required for capture-mkk-sample")
        from src.ingest.api.mkk_contract import verify_mkk_contract_lock, write_mkk_contract_capture
        from src.ingest.api.mkk_kap import (
            KapApiConfigError,
            KapApiProtocolError,
            KapApiTransportError,
            MkkKapApiClient,
            MkkKapApiConfig,
        )
        start_at = _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            raise SystemExit(f"{args.api_key_env} environment variable is required")
        try:
            config = MkkKapApiConfig.from_json_file(args.api_config)
            config.validate_live_ready()
            if args.contract_lock:
                verify_mkk_contract_lock(args.contract_lock, config)
            client = MkkKapApiClient(config, api_key)
            capture = client.capture_contract_sample(
                start_at=start_at,
                end_at=end_at,
                cursor=args.cursor,
                validate_items_limit=args.validate_items_limit,
            )
            sample_path, metadata_path = write_mkk_contract_capture(
                sample_path=args.out,
                metadata_path=args.metadata_out,
                config=config,
                capture=capture,
                overwrite=args.force,
            )
        except (
            KapApiConfigError, KapApiProtocolError, KapApiTransportError,
            ValueError, OSError,
        ) as exc:
            raise SystemExit(f"MKK contract sample capture basarisiz: {exc}") from exc
        print(json.dumps({
            "status": "OK",
            "sample": str(sample_path),
            "metadata": str(metadata_path),
            "source_name": capture.source_name,
            "items_seen": capture.items_seen,
            "items_validated": capture.items_validated,
            "sample_sha256": capture.payload_sha256,
            "captured_at": capture.captured_at.isoformat(),
            "authentication_material_persisted": False,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "validate-mkk-suite":
        if not args.suite_config or not args.checked_at:
            raise SystemExit("--suite-config and --checked-at are required for validate-mkk-suite")
        from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError
        from src.ingest.api.mkk_suite import MkkProductSuite, validate_mkk_product_suite
        checked_at = _parse_aware_datetime(args.checked_at, "checked-at")
        try:
            suite = MkkProductSuite.from_json_file(args.suite_config)
            report = validate_mkk_product_suite(
                suite,
                checked_at=checked_at,
                require_api_keys=args.require_api_keys,
                require_live_ready=args.strict,
                validate_items_limit=args.validate_items_limit,
            )
        except (KapApiConfigError, KapApiProtocolError, ValueError, OSError) as exc:
            raise SystemExit(f"MKK suite dogrulama basarisiz: {exc}") from exc
        payload = report.to_dict()
        payload["status"] = "OK"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.cmd == "plan-mkk-suite-backfill":
        if not args.suite_config or not args.checked_at or not args.start or not args.end:
            raise SystemExit(
                "--suite-config, --checked-at, --start and --end are required for plan-mkk-suite-backfill"
            )
        from pathlib import Path
        from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError
        from src.ingest.api.mkk_suite import (
            MkkProductSuite,
            plan_mkk_suite_backfill,
            validate_mkk_product_suite,
        )
        checked_at = _parse_aware_datetime(args.checked_at, "checked-at")
        start_at = _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        try:
            suite = MkkProductSuite.from_json_file(args.suite_config)
            validation = validate_mkk_product_suite(
                suite,
                checked_at=checked_at,
                require_api_keys=args.require_api_keys,
                require_live_ready=args.strict,
                validate_items_limit=args.validate_items_limit,
            )
            plans = plan_mkk_suite_backfill(
                suite,
                validation,
                start_at=start_at,
                end_at=end_at,
                max_window_hours=args.max_window_hours,
                overlap_seconds=args.overlap_seconds,
            )
        except (KapApiConfigError, KapApiProtocolError, ValueError, OSError) as exc:
            raise SystemExit(f"MKK suite backfill plani basarisiz: {exc}") from exc
        payload = {
            "status": "OK",
            "suite_name": suite.suite_name,
            "suite_version": suite.suite_version,
            "checked_at": checked_at.isoformat(),
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "product_count": len(plans),
            "total_window_count": sum(len(item.windows) for item in plans),
            "live_ready": validation.live_ready,
            "products": [item.to_dict() for item in plans],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        plan_out = args.plan_out or (
            args.out if args.out != "data/universe_stocks.csv" else None
        )
        if plan_out:
            target = Path(plan_out)
            if target.exists() and not args.force:
                raise SystemExit(f"plan dosyasi zaten var; --force gerekli: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
            payload["plan_file"] = str(target)
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        print(text)
        return

    if args.cmd == "plan-mkk-backfill":
        if not args.start or not args.end:
            raise SystemExit("--start and --end are required for plan-mkk-backfill")
        from src.ingest.kap_sync import plan_kap_backfill_windows
        start_at = _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        try:
            windows = plan_kap_backfill_windows(
                start_at=start_at, end_at=end_at,
                max_window_hours=args.max_window_hours,
                overlap_seconds=args.overlap_seconds,
            )
        except ValueError as exc:
            raise SystemExit(f"MKK backfill plani basarisiz: {exc}") from exc
        payload = {
            "status": "OK",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "window_count": len(windows),
            "max_window_hours": args.max_window_hours,
            "overlap_seconds": args.overlap_seconds,
            "windows": [
                {
                    "index": item.index,
                    "start_at": item.start_at.isoformat(),
                    "end_at": item.end_at.isoformat(),
                    "overlap_seconds": item.overlap_seconds,
                }
                for item in windows
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        plan_out = args.plan_out or (
            args.out if args.out != "data/universe_stocks.csv" else None
        )
        if plan_out:
            with open(plan_out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        print(text)
        return

    if args.cmd == "fetch-kap-universe":
        from src.ingest.api.kap_public_universe import KapPublicUniverseClient, write_universe_snapshot
        snapshot = KapPublicUniverseClient(minimum_rows=args.minimum_rows).fetch()
        csv_path, meta_path = write_universe_snapshot(snapshot, args.out)
        print(json.dumps({
            "rows": int(len(snapshot.frame)),
            "csv": str(csv_path),
            "metadata": str(meta_path),
            "fetched_at": snapshot.fetched_at.isoformat(),
            "source_url": snapshot.source_url,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "check-mkk-kap":
        if not args.api_config or not args.start or not args.end:
            raise SystemExit("--api-config, --start and --end are required for check-mkk-kap")
        from src.ingest.api.mkk_kap import (
            KapApiConfigError,
            KapApiProtocolError,
            KapApiTransportError,
            MkkKapApiClient,
            MkkKapApiConfig,
        )
        start_at = _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            raise SystemExit(f"{args.api_key_env} environment variable is required")
        try:
            config = MkkKapApiConfig.from_json_file(args.api_config)
            if args.contract_lock:
                from src.ingest.api.mkk_contract import verify_mkk_contract_lock
                verify_mkk_contract_lock(args.contract_lock, config)
            client = MkkKapApiClient(config, api_key)
            report = client.probe(
                start_at=start_at,
                end_at=end_at,
                validate_items_limit=args.validate_items_limit,
            )
        except (KapApiConfigError, KapApiProtocolError, KapApiTransportError, ValueError) as exc:
            raise SystemExit(f"MKK KAP health-check basarisiz: {exc}") from exc
        print(json.dumps({
            "status": "OK",
            "endpoint_url": report.endpoint_url,
            "method": report.method,
            "items_seen": report.items_seen,
            "items_validated": report.items_validated,
            "next_cursor_present": report.next_cursor_present,
            "checked_at": report.checked_at.isoformat(),
            "first_disclosure_id": report.first_disclosure_id,
            "last_disclosure_id": report.last_disclosure_id,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "sync-mkk-kap" and args.no_persist:
        if args.resume:
            raise SystemExit("--resume veritabani checkpoint'i gerektirir; --no-persist ile kullanilamaz")
        if not args.api_config or not args.start or not args.end:
            raise SystemExit("--api-config, --start and --end are required for sync-mkk-kap")
        from src.ingest.api.mkk_kap import (
            KapApiConfigError,
            KapApiProtocolError,
            KapApiTransportError,
            MkkKapApiClient,
            MkkKapApiConfig,
        )
        from src.ingest.kap_sync import plan_kap_sync_window
        start_at = _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            raise SystemExit(f"{args.api_key_env} environment variable is required")
        try:
            config = MkkKapApiConfig.from_json_file(args.api_config)
            config.validate_live_ready()
            if args.contract_lock:
                from src.ingest.api.mkk_contract import verify_mkk_contract_lock
                verify_mkk_contract_lock(args.contract_lock, config)
            plan = plan_kap_sync_window(
                requested_start=start_at,
                requested_end=end_at,
                overlap_seconds=args.overlap_seconds,
                max_window_hours=args.max_window_hours,
            )
            client = MkkKapApiClient(config, api_key)
            result = client.fetch_disclosures(
                start_at=plan.start_at,
                end_at=plan.end_at,
                initial_cursor=args.cursor,
                max_pages=args.max_pages,
                quarantine_invalid_items=args.quarantine_invalid_items,
            )
        except (KapApiConfigError, KapApiProtocolError, KapApiTransportError, ValueError) as exc:
            raise SystemExit(f"MKK KAP dry-run basarisiz: {exc}") from exc
        print(json.dumps({
            "persisted": False,
            "rows": len(result.disclosures),
            "pages": result.pages_fetched,
            "next_cursor": result.next_cursor,
            "first_id": result.disclosures[0].disclosure_id if result.disclosures else None,
            "last_id": result.disclosures[-1].disclosure_id if result.disclosures else None,
            "quarantined": len(result.quarantined_items),
            "complete": result.complete,
            "planned_start": plan.start_at.isoformat(),
            "planned_end": plan.end_at.isoformat(),
            "requested_end": plan.requested_end.isoformat(),
            "window_truncated": plan.truncated,
        }, ensure_ascii=False, indent=2))
        if not result.complete:
            raise SystemExit(2)
        return

    if args.cmd == "preview-kap-bank-batch":
        if not args.file or not args.contexts_config:
            raise SystemExit("--file and --contexts-config are required for preview-kap-bank-batch")
        if not args.mapping_config or not args.semantic_config or not args.derivation_config:
            raise SystemExit("--mapping-config, --semantic-config and --derivation-config are required")
        if not args.anchor:
            raise SystemExit("--anchor is required for preview-kap-bank-batch")
        from datetime import date
        from src.analytics.kap_bank_batch_io import json_safe, run_batch_preview_from_files
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        try:
            anchor = date.fromisoformat(args.anchor)
        except ValueError as exc:
            raise SystemExit("--anchor must be an ISO date") from exc
        report = run_batch_preview_from_files(
            disclosures_path=args.file,
            contexts_path=args.contexts_config,
            analysis_at=analysis_at,
            anchor_period_end=anchor,
            fact_config_path=args.mapping_config,
            semantic_config_path=args.semantic_config,
            derivation_config_path=args.derivation_config,
            continue_on_error=not args.strict,
        )
        output = json.dumps(json_safe(report), ensure_ascii=False, indent=2)
        if args.report_out:
            with open(args.report_out, "w", encoding="utf-8") as handle:
                handle.write(output + "\n")
        print(output)
        return

    kap_batch_request = None
    if args.cmd == "run-kap-bank-batch":
        if not args.file or not args.contexts_config:
            raise SystemExit("--file and --contexts-config are required for run-kap-bank-batch")
        if not args.mapping_config or not args.semantic_config or not args.derivation_config:
            raise SystemExit("--mapping-config, --semantic-config and --derivation-config are required")
        if not args.anchor:
            raise SystemExit("--anchor is required for run-kap-bank-batch")
        if args.horizon_days <= 0:
            raise SystemExit("--horizon-days must be positive")
        pipeline_version = args.pipeline_version or "KAP_BANK_BATCH_V7"
        batch_source = args.batch_source or "MKK_KAP_BANK_E2E"
        if not isinstance(pipeline_version, str) or not pipeline_version.strip():
            raise SystemExit("--pipeline-version must be non-empty")
        if not isinstance(batch_source, str) or not batch_source.strip():
            raise SystemExit("--batch-source must be non-empty")
        from datetime import date
        from src.analytics.kap_bank_batch_io import json_safe, run_batch_preview_from_files
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        try:
            anchor = date.fromisoformat(args.anchor)
        except ValueError as exc:
            raise SystemExit("--anchor must be an ISO date") from exc
        report = run_batch_preview_from_files(
            disclosures_path=args.file,
            contexts_path=args.contexts_config,
            analysis_at=analysis_at,
            anchor_period_end=anchor,
            fact_config_path=args.mapping_config,
            semantic_config_path=args.semantic_config,
            derivation_config_path=args.derivation_config,
            continue_on_error=not args.strict,
        )
        if args.no_persist:
            output = json.dumps(json_safe(report), ensure_ascii=False, indent=2)
            if args.report_out:
                with open(args.report_out, "w", encoding="utf-8") as handle:
                    handle.write(output + "\n")
            print(output)
            return
        kap_batch_request = (report, args.horizon_days, pipeline_version, batch_source)

    kap_db_batch_request = None
    if args.cmd == "run-kap-bank-db":
        if not args.mapping_config or not args.semantic_config or not args.derivation_config:
            raise SystemExit("--mapping-config, --semantic-config and --derivation-config are required")
        if args.horizon_days <= 0:
            raise SystemExit("--horizon-days must be positive")
        if args.max_context_age_days <= 0:
            raise SystemExit("--max-context-age-days must be positive")
        pipeline_version = args.pipeline_version or "KAP_BANK_DB_BATCH_V8"
        batch_source = args.batch_source or "RAW_KAP_DATABASE"
        if not isinstance(pipeline_version, str) or not pipeline_version.strip():
            raise SystemExit("--pipeline-version must be non-empty")
        if not isinstance(batch_source, str) or not batch_source.strip():
            raise SystemExit("--batch-source must be non-empty")
        from datetime import date
        from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
        from src.ingest.api.semantic_facts import SemanticMappingConfig
        from src.ingest.bank_fact_materializer import BankDerivationConfig
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        anchor = None
        if args.anchor:
            try:
                anchor = date.fromisoformat(args.anchor)
            except ValueError as exc:
                raise SystemExit("--anchor must be an ISO date") from exc
        tickers = None if not args.tickers else [
            value.strip() for value in args.tickers.split(",") if value.strip()
        ]
        weights_raw = _load_json(args.weights)
        if isinstance(weights_raw, dict) and "base_weights" in weights_raw:
            weights_raw = weights_raw["base_weights"]
        kap_db_batch_request = {
            "analysis_at": analysis_at,
            "anchor_period_end": anchor,
            "tickers": tickers,
            "fact_config": KapFinancialFactConfig.from_json_file(args.mapping_config),
            "semantic_config": SemanticMappingConfig.from_json_file(args.semantic_config),
            "derivation_config": BankDerivationConfig.from_json_file(args.derivation_config),
            "total_weights": weights_raw,
            "source": args.source,
            "notification_type": args.notification_type,
            "horizon_days": args.horizon_days,
            "max_context_age_days": args.max_context_age_days,
            "pipeline_version": pipeline_version,
            "batch_source": batch_source,
            "continue_on_error": not args.strict,
            "persist": not args.no_persist,
        }

    ranking_request = None
    if args.cmd == "show-bank-ranking":
        if not args.asof:
            raise SystemExit("--asof is required for show-bank-ranking")
        if args.horizon_days <= 0 or args.limit <= 0:
            raise SystemExit("--horizon-days and --limit must be positive")
        from datetime import date
        try:
            ranking_asof = date.fromisoformat(args.asof)
        except ValueError as exc:
            raise SystemExit("--asof must be an ISO date") from exc
        ranking_request = (ranking_asof, args.horizon_days, args.limit)

    semantic_request = None
    if args.cmd == "map-kap-semantic-facts":
        if not args.semantic_config:
            raise SystemExit("--semantic-config is required for map-kap-semantic-facts")
        if not args.source_mapping_profile or args.source_mapping_version is None:
            raise SystemExit("--source-mapping-profile and --source-mapping-version are required")
        mapped_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        from src.ingest.api.semantic_facts import SemanticMappingConfig
        semantic_request = (SemanticMappingConfig.from_json_file(args.semantic_config), mapped_at)

    company_ratio_request = None
    if args.cmd == "calc-company-ratios":
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        tickers = None if not args.tickers else [
            value.strip() for value in args.tickers.split(",") if value.strip()
        ]
        since_period_end = None
        if args.since_period_end:
            from datetime import date
            try:
                since_period_end = date.fromisoformat(args.since_period_end)
            except ValueError as exc:
                raise SystemExit("--since-period-end must be an ISO date") from exc
        company_ratio_request = (analysis_at, tickers, since_period_end)

    company_material_request = None
    if args.cmd == "materialize-company-facts":
        if not args.derivation_config:
            raise SystemExit("--derivation-config is required for materialize-company-facts")
        if not args.anchor:
            raise SystemExit("--anchor is required for materialize-company-facts")
        from datetime import date
        from src.ingest.company_fact_materializer import CompanyDerivationConfig
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        try:
            anchor = date.fromisoformat(args.anchor)
        except ValueError as exc:
            raise SystemExit("--anchor must be an ISO date") from exc
        company_material_request = (
            CompanyDerivationConfig.from_json_file(args.derivation_config), analysis_at, anchor
        )

    insurance_metrics_request = None
    if args.cmd == "ingest-insurance-metrics":
        if not args.file:
            raise SystemExit("--file is required for ingest-insurance-metrics")
        from src.ingest.insurance_metrics import InsuranceMetricsIngestError, load_insurance_metrics_jsonl
        try:
            insurance_metrics_request = load_insurance_metrics_jsonl(args.file)
        except (InsuranceMetricsIngestError, OSError) as exc:
            raise SystemExit(f"INSURANCE metrics dosyasi gecersiz: {exc}") from exc
        if args.no_persist:
            print(json.dumps({
                "status": "OK", "row_count": len(insurance_metrics_request),
                "persisted_count": 0, "persisted": False,
            }, ensure_ascii=False, indent=2))
            return

    insurance_batch_request = None
    if args.cmd == "run-insurance-batch":
        if not args.valuation_config:
            raise SystemExit("--valuation-config is required for run-insurance-batch")
        from src.analytics.insurance_valuation import InsuranceValuationConfig, InsuranceValuationError
        from src.ingest.sector_routing import SectorRoutingConfig, SectorRoutingError
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        tickers = None if not args.tickers else [value.strip() for value in args.tickers.split(",") if value.strip()]
        try:
            routing = None if not args.routing_config else SectorRoutingConfig.from_json_file(args.routing_config)
            valuation_config = InsuranceValuationConfig.from_json_file(args.valuation_config)
        except (InsuranceValuationError, SectorRoutingError, OSError, ValueError) as exc:
            raise SystemExit(f"INSURANCE config gecersiz: {exc}") from exc
        insurance_batch_request = (analysis_at, tickers, valuation_config, routing)

    fi_metrics_request = None
    if args.cmd == "ingest-fi-metrics":
        if not args.file:
            raise SystemExit("--file is required for ingest-fi-metrics")
        from src.ingest.financial_institution_metrics import (
            FinancialInstitutionMetricsIngestError,
            load_financial_institution_metrics_jsonl,
        )
        try:
            fi_metrics_request = load_financial_institution_metrics_jsonl(args.file)
        except (FinancialInstitutionMetricsIngestError, OSError) as exc:
            raise SystemExit(f"FINANSAL KURULUS metrics dosyasi gecersiz: {exc}") from exc
        if args.no_persist:
            print(json.dumps({
                "status": "OK", "row_count": len(fi_metrics_request),
                "persisted_count": 0, "persisted": False,
            }, ensure_ascii=False, indent=2))
            return

    fi_batch_request = None
    if args.cmd == "run-fi-batch":
        if not args.valuation_config:
            raise SystemExit("--valuation-config is required for run-fi-batch")
        from src.analytics.financial_institution_valuation import (
            FinancialInstitutionValuationConfig,
            FinancialInstitutionValuationError,
        )
        from src.ingest.sector_routing import SectorRoutingConfig, SectorRoutingError
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        tickers = None if not args.tickers else [v.strip() for v in args.tickers.split(",") if v.strip()]
        try:
            routing = None if not args.routing_config else SectorRoutingConfig.from_json_file(args.routing_config)
            valuation_config = FinancialInstitutionValuationConfig.load(args.valuation_config)
        except (FinancialInstitutionValuationError, SectorRoutingError, OSError, ValueError) as exc:
            raise SystemExit(f"FINANSAL KURULUS config gecersiz: {exc}") from exc
        fi_batch_request = (analysis_at, tickers, valuation_config, routing)

    gyo_nav_request = None
    if args.cmd == "ingest-gyo-nav":
        if not args.file:
            raise SystemExit("--file is required for ingest-gyo-nav")
        from src.ingest.gyo_nav import GyoNavIngestError, load_gyo_nav_jsonl
        try:
            gyo_nav_request = load_gyo_nav_jsonl(args.file)
        except (GyoNavIngestError, OSError) as exc:
            raise SystemExit(f"GYO NAV dosyasi gecersiz: {exc}") from exc
        if args.no_persist:
            print(json.dumps({
                "status": "OK", "row_count": len(gyo_nav_request),
                "persisted_count": 0, "persisted": False,
            }, ensure_ascii=False, indent=2))
            return

    gyo_batch_request = None
    if args.cmd == "run-gyo-batch":
        if not args.valuation_config:
            raise SystemExit("--valuation-config is required for run-gyo-batch")
        from src.analytics.gyo_valuation import GyoValuationConfig, GyoValuationError
        from src.ingest.sector_routing import SectorRoutingConfig, SectorRoutingError
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        tickers = None if not args.tickers else [value.strip() for value in args.tickers.split(",") if value.strip()]
        try:
            routing = None if not args.routing_config else SectorRoutingConfig.from_json_file(args.routing_config)
            valuation_config = GyoValuationConfig.from_json_file(args.valuation_config)
        except (GyoValuationError, SectorRoutingError, OSError, ValueError) as exc:
            raise SystemExit(f"GYO config gecersiz: {exc}") from exc
        gyo_batch_request = (analysis_at, tickers, valuation_config, routing)

    holding_nav_request = None
    if args.cmd == "ingest-holding-nav":
        if not args.file:
            raise SystemExit("--file is required for ingest-holding-nav")
        from src.ingest.holding_nav import HoldingNavIngestError, load_holding_nav_jsonl
        try:
            holding_nav_request = load_holding_nav_jsonl(args.file)
        except (HoldingNavIngestError, OSError) as exc:
            raise SystemExit(f"HOLDING NAV dosyasi gecersiz: {exc}") from exc
        if args.no_persist:
            print(json.dumps({
                "status": "OK",
                "row_count": len(holding_nav_request),
                "persisted_count": 0,
                "persisted": False,
            }, ensure_ascii=False, indent=2))
            return

    holding_batch_request = None
    if args.cmd == "run-holding-batch":
        if not args.valuation_config:
            raise SystemExit("--valuation-config is required for run-holding-batch")
        from src.analytics.holding_valuation import HoldingValuationConfig, HoldingValuationError
        from src.ingest.sector_routing import SectorRoutingConfig, SectorRoutingError
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        tickers = None if not args.tickers else [
            value.strip() for value in args.tickers.split(",") if value.strip()
        ]
        try:
            routing = None if not args.routing_config else SectorRoutingConfig.from_json_file(args.routing_config)
            valuation_config = HoldingValuationConfig.from_json_file(args.valuation_config)
        except (HoldingValuationError, SectorRoutingError, OSError, ValueError) as exc:
            raise SystemExit(f"HOLDING config gecersiz: {exc}") from exc
        holding_batch_request = (analysis_at, tickers, valuation_config, routing)

    nonfin_batch_request = None
    if args.cmd == "run-nonfin-batch":
        if not args.valuation_config:
            raise SystemExit("--valuation-config is required for run-nonfin-batch")
        from datetime import date
        from src.analytics.nonfin_valuation import NonfinValuationConfig
        from src.ingest.sector_routing import SectorRoutingConfig
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        anchor = None
        if args.anchor:
            try:
                anchor = date.fromisoformat(args.anchor)
            except ValueError as exc:
                raise SystemExit("--anchor must be an ISO date") from exc
        tickers = None if not args.tickers else [
            value.strip() for value in args.tickers.split(",") if value.strip()
        ]
        routing = None if not args.routing_config else SectorRoutingConfig.from_json_file(args.routing_config)
        nonfin_batch_request = (
            analysis_at, anchor, tickers,
            NonfinValuationConfig.from_json_file(args.valuation_config), routing,
        )

    bank_material_request = None
    if args.cmd == "materialize-bank-facts":
        if not args.derivation_config:
            raise SystemExit("--derivation-config is required for materialize-bank-facts")
        if not args.anchor:
            raise SystemExit("--anchor is required for materialize-bank-facts")
        from datetime import date
        from src.ingest.bank_fact_materializer import BankDerivationConfig
        analysis_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
        try:
            anchor = date.fromisoformat(args.anchor)
        except ValueError as exc:
            raise SystemExit("--anchor must be an ISO date") from exc
        bank_material_request = (
            BankDerivationConfig.from_json_file(args.derivation_config), analysis_at, anchor
        )

    mkk_suite_runtime_request = None
    if args.cmd in {"check-mkk-suite-readiness", "sync-mkk-suite"}:
        if not args.suite_config or not args.checked_at or not args.end:
            raise SystemExit("--suite-config, --checked-at and --end are required")
        if args.cmd == "check-mkk-suite-readiness" and not args.start:
            raise SystemExit("--start is required for check-mkk-suite-readiness")
        if args.cmd == "sync-mkk-suite" and not args.resume and not args.start:
            raise SystemExit("--start is required unless --resume is used")
        if args.max_windows_per_product <= 0 or args.max_product_attempts <= 0:
            raise SystemExit("--max-windows-per-product and --max-product-attempts must be positive")
        from src.ingest.api.mkk_suite import MkkProductSuite, validate_mkk_product_suite
        suite = MkkProductSuite.from_json_file(args.suite_config)
        checked_at = _parse_aware_datetime(args.checked_at, "checked-at")
        start_at = None if not args.start else _parse_aware_datetime(args.start, "start")
        end_at = _parse_aware_datetime(args.end, "end")
        try:
            validation = validate_mkk_product_suite(
                suite,
                checked_at=checked_at,
                require_api_keys=args.cmd == "sync-mkk-suite",
                require_live_ready=args.cmd == "sync-mkk-suite",
            )
        except (ValueError, OSError) as exc:
            raise SystemExit(f"MKK suite dogrulama basarisiz: {exc}") from exc
        mkk_suite_runtime_request = (suite, validation, start_at, end_at)

    conn = get_conn()
    try:
        if args.cmd == "ingest-insurance-metrics":
            from src.ingest.insurance_metrics import persist_insurance_metrics_records
            count = persist_insurance_metrics_records(conn, insurance_metrics_request)
            print(json.dumps({
                "status": "OK", "row_count": len(insurance_metrics_request),
                "persisted_count": count, "persisted": True,
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "ingest-fi-metrics":
            from src.ingest.financial_institution_metrics import (
                persist_financial_institution_metrics_records,
            )
            count = persist_financial_institution_metrics_records(conn, fi_metrics_request)
            print(json.dumps({
                "status": "OK", "row_count": len(fi_metrics_request),
                "persisted_count": count, "persisted": True,
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "ingest-gyo-nav":
            from src.ingest.gyo_nav import persist_gyo_nav_records
            count = persist_gyo_nav_records(conn, gyo_nav_request)
            print(json.dumps({
                "status": "OK", "row_count": len(gyo_nav_request),
                "persisted_count": count, "persisted": True,
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "ingest-holding-nav":
            from src.ingest.holding_nav import persist_holding_nav_records
            count = persist_holding_nav_records(conn, holding_nav_request)
            print(json.dumps({
                "status": "OK",
                "row_count": len(holding_nav_request),
                "persisted_count": count,
                "persisted": True,
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "ingest-universe":
            from src.ingest.csv_to_core import ingest_csv, UNIVERSE_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(conn, args.file, UNIVERSE_SPEC, col_map=col_map, upsert=True, key_cols=["ticker"], chunksize=args.chunksize)
            print(f"Loaded {n} universe rows.")
        elif args.cmd == "ingest-prices":
            from src.ingest.csv_to_core import ingest_csv, PRICES_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(conn, args.file, PRICES_SPEC, col_map=col_map, upsert=True, key_cols=["ticker", "trade_date"], chunksize=args.chunksize)
            print(f"Loaded {n} price rows.")
        elif args.cmd == "ingest-index":
            from src.ingest.csv_to_core import ingest_csv, INDEX_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(conn, args.file, INDEX_SPEC, col_map=col_map, upsert=True, key_cols=["index_code", "trade_date"], chunksize=args.chunksize)
            print(f"Loaded {n} index rows.")
        elif args.cmd == "ingest-fin":
            from src.ingest.csv_to_core import ingest_csv, FIN_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(conn, args.file, FIN_SPEC, col_map=col_map, upsert=True, key_cols=["ticker", "period_end", "version_tag"], chunksize=args.chunksize)
            print(f"Loaded {n} financial rows.")
        elif args.cmd == "ingest-bank-metrics":
            from src.ingest.csv_to_core import ingest_csv, BANK_METRICS_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(
                conn, args.file, BANK_METRICS_SPEC, col_map=col_map, upsert=True,
                key_cols=["ticker", "period_end", "version_tag", "version_sequence", "published_at"],
                chunksize=args.chunksize,
            )
            print(f"Loaded {n} point-in-time bank metric rows.")
        elif args.cmd == "ingest-bank-assumptions":
            from src.ingest.csv_to_core import ingest_csv, BANK_ASSUMPTIONS_SPEC
            if not args.file:
                raise SystemExit("--file is required")
            n = ingest_csv(
                conn, args.file, BANK_ASSUMPTIONS_SPEC, col_map=col_map, upsert=True,
                key_cols=["scope_type", "scope_code", "effective_at"],
                chunksize=args.chunksize,
            )
            print(f"Loaded {n} point-in-time bank assumption rows.")
        elif args.cmd == "check-mkk-suite-readiness":
            from src.ingest.api.mkk_suite import plan_mkk_suite_backfill
            from src.ingest.mkk_suite_sync import check_mkk_suite_database_readiness
            suite, validation, start_at, end_at = mkk_suite_runtime_request
            database = check_mkk_suite_database_readiness(conn)
            plans = plan_mkk_suite_backfill(
                suite, validation,
                start_at=start_at, end_at=end_at,
                max_window_hours=args.max_window_hours,
                overlap_seconds=args.overlap_seconds,
            )
            payload = {
                "status": "READY" if database.ready and validation.live_ready else "NOT_READY",
                "suite": validation.to_dict(),
                "database": database.to_dict(),
                "total_window_count": sum(len(item.windows) for item in plans),
                "products": [item.to_dict() for item in plans],
            }
            output = json.dumps(payload, ensure_ascii=False, indent=2)
            if args.report_out:
                with open(args.report_out, "w", encoding="utf-8") as handle:
                    handle.write(output + "\n")
            print(output)
            if payload["status"] != "READY":
                raise SystemExit(2)
        elif args.cmd == "sync-mkk-suite":
            from src.ingest.mkk_suite_sync import (
                persist_mkk_suite_sync_report,
                run_mkk_product_suite_sync,
            )
            suite, validation, start_at, end_at = mkk_suite_runtime_request
            try:
                report, database = run_mkk_product_suite_sync(
                    conn, suite, validation,
                    requested_start=start_at,
                    requested_end=end_at,
                    resume=args.resume,
                    continue_on_error=args.continue_on_error,
                    overlap_seconds=args.overlap_seconds,
                    max_window_hours=args.max_window_hours,
                    max_windows_per_product=args.max_windows_per_product,
                    max_product_attempts=args.max_product_attempts,
                    max_pages=args.max_pages,
                    quarantine_invalid_items=args.quarantine_invalid_items,
                )
            except (ValueError, RuntimeError, OSError) as exc:
                raise SystemExit(f"MKK suite sync basarisiz: {exc}") from exc
            persistence_error = None
            try:
                persist_mkk_suite_sync_report(conn, report)
            except Exception as exc:
                persistence_error = f"{type(exc).__name__}: {exc}"
            payload = report.to_dict()
            payload["database"] = database.to_dict()
            payload["suite_report_persisted"] = persistence_error is None
            payload["suite_report_persistence_error"] = persistence_error
            output = json.dumps(payload, ensure_ascii=False, indent=2)
            if args.report_out:
                with open(args.report_out, "w", encoding="utf-8") as handle:
                    handle.write(output + "\n")
            print(output)
            if persistence_error is not None:
                raise SystemExit(1)
            if report.status != "COMPLETE":
                raise SystemExit(2)
        elif args.cmd == "sync-kap-universe":
            from src.ingest.api.kap_public_universe import KapPublicUniverseClient, write_universe_snapshot
            from src.ingest.kap_raw import persist_kap_universe
            snapshot = KapPublicUniverseClient(minimum_rows=args.minimum_rows).fetch()
            n = persist_kap_universe(conn, snapshot)
            csv_path, meta_path = write_universe_snapshot(snapshot, args.out)
            print(json.dumps({
                "persisted": n, "csv": str(csv_path), "metadata": str(meta_path),
                "fetched_at": snapshot.fetched_at.isoformat(),
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "sync-mkk-kap":
            if not args.api_config or not args.end:
                raise SystemExit("--api-config and --end are required for sync-mkk-kap")
            if not args.resume and not args.start:
                raise SystemExit("--start is required unless --resume is used")
            if args.resume and args.cursor:
                raise SystemExit("--cursor cannot be combined with --resume")
            from src.ingest.api.mkk_kap import (
                KapApiConfigError,
                KapApiProtocolError,
                KapApiTransportError,
                MkkKapApiClient,
                MkkKapApiConfig,
            )
            from src.ingest.kap_raw import persist_kap_disclosures
            from src.ingest.kap_sync import (
                acquire_kap_sync_lock,
                load_kap_sync_checkpoint,
                plan_kap_sync_window,
                release_kap_sync_lock,
            )
            start_at = None if not args.start else _parse_aware_datetime(args.start, "start")
            end_at = _parse_aware_datetime(args.end, "end")
            api_key = os.getenv(args.api_key_env)
            if not api_key:
                raise SystemExit(f"{args.api_key_env} environment variable is required")
            lock_key = None
            primary_error = None
            try:
                config = MkkKapApiConfig.from_json_file(args.api_config)
                config.validate_live_ready()
                if args.contract_lock:
                    from src.ingest.api.mkk_contract import verify_mkk_contract_lock
                    verify_mkk_contract_lock(args.contract_lock, config)
                lock_key = acquire_kap_sync_lock(
                    conn, source=config.source_name, stream_name=args.stream_name,
                )
                checkpoint = load_kap_sync_checkpoint(
                    conn,
                    source=config.source_name,
                    stream_name=args.stream_name,
                ) if args.resume else None
                plan = plan_kap_sync_window(
                    requested_start=start_at,
                    requested_end=end_at,
                    checkpoint=checkpoint,
                    resume=args.resume,
                    overlap_seconds=args.overlap_seconds,
                    max_window_hours=args.max_window_hours,
                )
                client = MkkKapApiClient(config, api_key)
                result = client.fetch_disclosures(
                    start_at=plan.start_at,
                    end_at=plan.end_at,
                    initial_cursor=args.cursor,
                    max_pages=args.max_pages,
                    quarantine_invalid_items=args.quarantine_invalid_items,
                )
                n = persist_kap_disclosures(conn, result, stream_name=args.stream_name)
            except (
                KapApiConfigError, KapApiProtocolError, KapApiTransportError,
                ValueError, RuntimeError,
            ) as exc:
                primary_error = exc
                raise SystemExit(f"MKK KAP sync basarisiz: {exc}") from exc
            finally:
                if lock_key is not None:
                    try:
                        release_kap_sync_lock(conn, lock_key)
                    except Exception as release_exc:
                        if primary_error is None:
                            raise SystemExit(
                                f"MKK KAP sync lock serbest birakilamadi: {release_exc}"
                            ) from release_exc
            print(json.dumps({
                "persisted": n, "pages": result.pages_fetched,
                "next_cursor": result.next_cursor,
                "window_start": result.start_at.isoformat(),
                "window_end": result.end_at.isoformat(),
                "requested_end": plan.requested_end.isoformat(),
                "window_truncated": plan.truncated,
                "resumed": plan.resume,
                "checkpoint_window_end": (
                    None if plan.checkpoint_window_end is None
                    else plan.checkpoint_window_end.isoformat()
                ),
                "quarantined": len(result.quarantined_items),
                "complete": result.complete,
                "checkpoint_advanced": result.complete,
                "sync_lock_key": lock_key,
            }, ensure_ascii=False, indent=2))
            if not result.complete:
                raise SystemExit(2)
        elif args.cmd == "extract-kap-facts":
            if not args.mapping_config:
                raise SystemExit("--mapping-config is required for extract-kap-facts")
            extracted_at = _parse_aware_datetime(args.analysis_at, "analysis-at")
            from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
            from src.ingest.kap_raw import extract_pending_kap_financial_facts
            config = KapFinancialFactConfig.from_json_file(args.mapping_config)
            report = extract_pending_kap_financial_facts(
                conn,
                config,
                source=args.source,
                notification_type=args.notification_type,
                limit=args.limit,
                retry_rejections=args.retry_rejections,
                extracted_at=extracted_at,
            )
            print(json.dumps({
                "disclosures_seen": report.disclosures_seen,
                "disclosures_extracted": report.disclosures_extracted,
                "disclosures_rejected": report.disclosures_rejected,
                "facts_written": report.facts_written,
                "rejected_ids": list(report.rejected_ids),
                "mapping_profile": config.mapping_profile,
                "mapping_version": config.mapping_version,
                "extracted_at": extracted_at.isoformat(),
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "map-kap-semantic-facts":
            from src.ingest.semantic_materialization import map_pending_semantic_facts
            config, mapped_at = semantic_request
            report = map_pending_semantic_facts(
                conn, config, source=args.source,
                source_mapping_profile=args.source_mapping_profile,
                source_mapping_version=args.source_mapping_version,
                limit=args.limit, retry_rejections=args.retry_rejections,
                mapped_at=mapped_at,
            )
            print(json.dumps({
                "disclosures_seen": report.disclosures_seen,
                "disclosures_mapped": report.disclosures_mapped,
                "disclosures_rejected": report.disclosures_rejected,
                "semantic_facts_written": report.semantic_facts_written,
                "rejected_ids": list(report.rejected_ids),
                "semantic_profile": config.mapping_profile,
                "semantic_version": config.mapping_version,
                "mapped_at": mapped_at.isoformat(),
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "materialize-bank-facts":
            from src.analytics.bank_batch_pipeline import fetch_active_bank_tickers
            from src.ingest.bank_fact_materializer import materialize_bank_metrics_batch
            config, analysis_at, anchor = bank_material_request
            tickers = (
                fetch_active_bank_tickers(conn)
                if not args.tickers
                else [value.strip() for value in args.tickers.split(",") if value.strip()]
            )
            report = materialize_bank_metrics_batch(
                conn, config=config, tickers=tickers, analysis_at=analysis_at,
                anchor_period_end=anchor, persist=not args.no_persist,
            )
            print(json.dumps({
                "tickers_seen": report.tickers_seen,
                "tickers_materialized": report.tickers_materialized,
                "tickers_rejected": report.tickers_rejected,
                "metrics_written": report.metrics_written,
                "rejected": report.rejected,
                "persisted": not args.no_persist,
                "analysis_at": analysis_at.isoformat(),
                "anchor_period_end": anchor.isoformat(),
                "derivation_profile": config.derivation_profile,
                "derivation_version": config.derivation_version,
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "materialize-company-facts":
            from src.ingest.company_fact_materializer import (
                fetch_active_company_tickers, materialize_company_metrics_batch,
            )
            config, analysis_at, anchor = company_material_request
            tickers = (
                [value.strip() for value in args.tickers.split(",") if value.strip()]
                if args.tickers else fetch_active_company_tickers(conn, config)
            )
            report = materialize_company_metrics_batch(
                conn, config=config, tickers=tickers, analysis_at=analysis_at,
                anchor_period_end=anchor, persist=not args.no_persist,
            )
            print(json.dumps({
                "status": "OK" if report.tickers_rejected == 0 else "PARTIAL",
                "tickers_seen": report.tickers_seen,
                "tickers_materialized": report.tickers_materialized,
                "tickers_rejected": report.tickers_rejected,
                "metrics_written": report.metrics_written,
                "rejected": dict(report.rejected),
            }, ensure_ascii=False, indent=2))
        elif args.cmd == "run-kap-bank-batch":
            from dataclasses import asdict
            from src.analytics.kap_bank_batch_io import json_safe
            from src.analytics.kap_bank_batch_persistence import persist_kap_bank_batch_report
            report, horizon_days, pipeline_version, batch_source = kap_batch_request
            saved = persist_kap_bank_batch_report(
                conn, report, horizon_days=horizon_days,
                pipeline_version=pipeline_version, source=batch_source,
            )
            output_payload = {
                "persistence": asdict(saved),
                "status": report["status"],
                "analysis_at": report["analysis_at"],
                "anchor_period_end": report["anchor_period_end"],
                "requested_count": report["requested_count"],
                "result_count": report["result_count"],
                "rejected_count": report["rejected_count"],
                "ranking": report["ranking"],
                "rejections": report["rejections"],
            }
            output = json.dumps(json_safe(output_payload), ensure_ascii=False, indent=2)
            if args.report_out:
                full_output = json.dumps(json_safe(report), ensure_ascii=False, indent=2)
                with open(args.report_out, "w", encoding="utf-8") as handle:
                    handle.write(full_output + "\n")
            print(output)
        elif args.cmd == "run-kap-bank-db":
            from dataclasses import asdict
            from src.analytics.kap_bank_batch_io import json_safe
            from src.analytics.kap_bank_db_workflow import run_kap_bank_database_batch
            result = run_kap_bank_database_batch(conn, **kap_db_batch_request)
            report = result.report
            output_payload = {
                "persistence": None if result.persistence is None else asdict(result.persistence),
                "status": report["status"],
                "analysis_at": report["analysis_at"],
                "anchor_period_end": report["anchor_period_end"],
                "tickers": list(result.tickers),
                "disclosures_loaded": result.disclosures_loaded,
                "context_ready_count": result.context_ready_count,
                "requested_count": report["requested_count"],
                "result_count": report["result_count"],
                "rejected_count": report["rejected_count"],
                "ranking": report["ranking"],
                "rejections": report["rejections"],
            }
            output = json.dumps(json_safe(output_payload), ensure_ascii=False, indent=2)
            if args.report_out:
                full_output = json.dumps(json_safe(report), ensure_ascii=False, indent=2)
                with open(args.report_out, "w", encoding="utf-8") as handle:
                    handle.write(full_output + "\n")
            print(output)
        elif args.cmd == "show-bank-ranking":
            from src.analytics.kap_bank_batch_io import json_safe
            from src.analytics.kap_bank_batch_persistence import fetch_latest_kap_bank_ranking
            ranking_asof, ranking_horizon, ranking_limit = ranking_request
            rows = fetch_latest_kap_bank_ranking(
                conn, asof_date=ranking_asof,
                horizon_days=ranking_horizon, limit=ranking_limit,
            )
            print(json.dumps(json_safe(rows), ensure_ascii=False, indent=2))
        elif args.cmd == "validate-core":
            from src.ingest.validate_core import validate_core
            print(json.dumps(validate_core(conn), indent=2, ensure_ascii=False))
        elif args.cmd == "fetch-yf-prices":
            from src.ingest.api.yfinance_prices import fetch_prices
            from src.ingest.csv_to_core import copy_dataframe, PRICES_SPEC
            if not args.tickers or not args.start or not args.end:
                raise SystemExit("--tickers, --start, --end are required")
            tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
            tmap = (symbols or {}).get("tickers", {}) if isinstance(symbols, dict) else {}
            df = fetch_prices(tickers, start=args.start, end=args.end, symbol_map=tmap)
            n = copy_dataframe(conn, PRICES_SPEC, df, upsert=True, key_cols=["ticker", "trade_date"])
            print(f"Fetched+loaded {n} daily price rows.")
        elif args.cmd == "fetch-yf-index":
            from src.ingest.api.yfinance_prices import fetch_index_prices
            from src.ingest.csv_to_core import copy_dataframe, INDEX_SPEC
            if not args.index or not args.start or not args.end:
                raise SystemExit("--index, --start, --end are required")
            yahoo_symbol = args.yahoo
            if yahoo_symbol is None and isinstance(symbols, dict):
                yahoo_symbol = symbols.get("indices", {}).get(args.index)
            if not yahoo_symbol:
                yahoo_symbol = f"{args.index}.IS"
            df = fetch_index_prices(args.index, yahoo_symbol=yahoo_symbol, start=args.start, end=args.end)
            n = copy_dataframe(conn, INDEX_SPEC, df, upsert=True, key_cols=["index_code", "trade_date"])
            print(f"Fetched+loaded {n} index price rows for {args.index} ({yahoo_symbol}).")
        elif args.cmd == "calc-ratios":
            from src.analytics.ratios_calc import run_ratios_calc
            run_ratios_calc(conn, ratios_json_path=args.ratios, since_period_end=args.since_period_end)
        elif args.cmd == "calc-company-ratios":
            from src.analytics.company_ratio_pipeline import run_company_core_ratios_asof
            analysis_at, tickers, since_period_end = company_ratio_request
            result = run_company_core_ratios_asof(
                conn, analysis_at=analysis_at, ratios_json_path=args.ratios,
                tickers=tickers, since_period_end=since_period_end,
                persist=not args.no_persist,
            )
            payload = {
                "status": "OK",
                "analysis_at": analysis_at.isoformat(),
                "ticker_count": int(result["ticker"].nunique()) if not result.empty else 0,
                "ratio_row_count": int(len(result)),
                "persisted": not args.no_persist,
                "core_only": True,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.cmd == "run-daily":
            from src.analytics.run_daily_pipeline import run_daily_pipeline
            if not args.asof:
                raise SystemExit("--asof is required for run-daily")
            run_daily_pipeline(
                conn, args.asof, args.ratios, args.sectors, args.weights,
                bank_analysis_at=args.analysis_at, bank_anchor_period_end=args.anchor,
            )
        elif args.cmd == "run-insurance-batch":
            from src.analytics.insurance_batch_pipeline import run_insurance_batch
            analysis_at, tickers, valuation_config, routing_config = insurance_batch_request
            report = run_insurance_batch(
                conn, analysis_at=analysis_at, config=valuation_config,
                tickers=tickers, routing_config=routing_config, persist=not args.no_persist,
            )
            output = {
                "status": "OK" if not report.get("rejections") else "PARTIAL",
                "analysis_at": analysis_at.isoformat(),
                "result_count": report.get("result_count", 0),
                "rejection_count": len(report.get("rejections", [])),
                "rejections": report.get("rejections", []),
                "persisted": not args.no_persist,
                "valuation_profile": valuation_config.valuation_profile,
                "valuation_version": valuation_config.valuation_version,
                "config_sha256": valuation_config.config_sha256,
                "ranking": [{
                    "ticker": row["ticker"], "m2": row["m2"]["m2"],
                    "valuation_status": row["valuation"]["status"],
                    "v_conf": row["valuation"]["v_conf"],
                    "v_mid": row["valuation"].get("V_mid"),
                    "target_pb": row["valuation"].get("target_pb"),
                    "target_pe": row["valuation"].get("target_pe"),
                    "technical_margin": row["valuation"].get("technical_margin"),
                    "combined_ratio": row["valuation"].get("combined_ratio"),
                } for row in report.get("results", [])],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "run-fi-batch":
            from src.analytics.financial_institution_batch_pipeline import (
                run_financial_institution_batch,
            )
            analysis_at, tickers, valuation_config, routing_config = fi_batch_request
            report = run_financial_institution_batch(
                conn, analysis_at=analysis_at, config=valuation_config,
                tickers=tickers, routing_config=routing_config, persist=not args.no_persist,
            )
            output = {
                "status": "OK" if not report.get("rejections") else "PARTIAL",
                "analysis_at": analysis_at.isoformat(),
                "result_count": report.get("result_count", 0),
                "rejection_count": len(report.get("rejections", [])),
                "rejections": report.get("rejections", []),
                "persisted": not args.no_persist,
                "valuation_profile": valuation_config.valuation_profile,
                "valuation_version": valuation_config.valuation_version,
                "config_sha256": valuation_config.config_sha256,
                "ranking": [{
                    "ticker": row["ticker"], "m2": row["m2"]["m2"],
                    "business_type": row["valuation"].get("business_type"),
                    "valuation_status": row["valuation"]["status"],
                    "v_conf": row["valuation"]["v_conf"],
                    "v_mid": row["valuation"].get("V_mid"),
                    "target_pb": row["valuation"].get("target_pb"),
                    "target_pe": row["valuation"].get("target_pe"),
                    "roe_ttm": row["valuation"].get("roe_ttm"),
                    "npl_ratio": row["valuation"].get("npl_ratio"),
                    "equity_buffer": row["valuation"].get("equity_buffer"),
                } for row in report.get("results", [])],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "run-gyo-batch":
            from src.analytics.gyo_batch_pipeline import run_gyo_batch
            analysis_at, tickers, valuation_config, routing_config = gyo_batch_request
            report = run_gyo_batch(
                conn, analysis_at=analysis_at, config=valuation_config,
                tickers=tickers, routing_config=routing_config, persist=not args.no_persist,
            )
            output = {
                "status": "OK" if not report.get("rejections") else "PARTIAL",
                "analysis_at": analysis_at.isoformat(),
                "result_count": report.get("result_count", 0),
                "rejection_count": len(report.get("rejections", [])),
                "rejections": report.get("rejections", []),
                "persisted": not args.no_persist,
                "valuation_profile": valuation_config.valuation_profile,
                "valuation_version": valuation_config.valuation_version,
                "config_sha256": valuation_config.config_sha256,
                "ranking": [{
                    "ticker": row["ticker"], "m2": row["m2"]["m2"],
                    "valuation_status": row["valuation"]["status"],
                    "v_conf": row["valuation"]["v_conf"],
                    "v_mid": row["valuation"].get("V_mid"),
                    "target_pd_nav": row["valuation"].get("target_pd_nav"),
                } for row in report.get("results", [])],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "run-holding-batch":
            from src.analytics.holding_batch_pipeline import run_holding_batch
            analysis_at, tickers, valuation_config, routing_config = holding_batch_request
            report = run_holding_batch(
                conn, analysis_at=analysis_at, config=valuation_config,
                tickers=tickers, routing_config=routing_config,
                persist=not args.no_persist,
            )
            output = {
                "status": "OK" if not report.get("rejections") else "PARTIAL",
                "analysis_at": analysis_at.isoformat(),
                "result_count": report.get("result_count", 0),
                "rejection_count": len(report.get("rejections", [])),
                "rejections": report.get("rejections", []),
                "persisted": not args.no_persist,
                "valuation_profile": valuation_config.valuation_profile,
                "valuation_version": valuation_config.valuation_version,
                "config_sha256": valuation_config.config_sha256,
                "ranking": [
                    {
                        "ticker": row["ticker"],
                        "m2": row["m2"]["m2"],
                        "valuation_status": row["valuation"]["status"],
                        "v_conf": row["valuation"]["v_conf"],
                        "v_mid": row["valuation"].get("V_mid"),
                        "target_discount": row["valuation"].get("target_discount"),
                    }
                    for row in report.get("results", [])
                ],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "run-nonfin-batch":
            from src.analytics.nonfin_batch_pipeline import run_nonfin_batch
            analysis_at, anchor, tickers, valuation_config, routing_config = nonfin_batch_request
            report = run_nonfin_batch(
                conn, analysis_at=analysis_at, config=valuation_config,
                anchor_period_end=anchor, tickers=tickers,
                routing_config=routing_config, persist=not args.no_persist,
            )
            output = {
                "status": "OK" if not report.get("rejections") else "PARTIAL",
                "analysis_at": analysis_at.isoformat(),
                "anchor_period_end": None if report.get("anchor_period_end") is None else report["anchor_period_end"].isoformat(),
                "result_count": report.get("result_count", 0),
                "rejection_count": len(report.get("rejections", [])),
                "rejections": report.get("rejections", []),
                "persisted": not args.no_persist,
                "valuation_profile": valuation_config.valuation_profile,
                "valuation_version": valuation_config.valuation_version,
                "config_sha256": valuation_config.config_sha256,
                "ranking": [
                    {
                        "ticker": row["ticker"],
                        "m2": row["m2"]["m2"],
                        "valuation_status": row["valuation"]["status"],
                        "v_conf": row["valuation"]["v_conf"],
                        "v_mid": row["valuation"].get("V_mid"),
                    }
                    for row in report.get("results", [])
                ],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "run-bank-batch":
            if not args.analysis_at:
                raise SystemExit("--analysis-at is required for run-bank-batch")
            from datetime import date, datetime
            from src.analytics.bank_batch_pipeline import run_bank_batch
            analysis_at = datetime.fromisoformat(args.analysis_at.replace("Z", "+00:00"))
            anchor = None if not args.anchor else date.fromisoformat(args.anchor)
            tickers = None if not args.tickers else [t.strip() for t in args.tickers.split(",") if t.strip()]
            result = run_bank_batch(
                conn, analysis_at=analysis_at, anchor_period_end=anchor,
                tickers=tickers, persist=not args.no_persist,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "bank-shadow-report":
            if not args.analysis_at:
                raise SystemExit("--analysis-at is required for bank-shadow-report")
            from datetime import datetime
            from src.analytics.bank_shadow_report import run_bank_shadow_report
            analysis_at = datetime.fromisoformat(args.analysis_at.replace("Z", "+00:00"))
            thresholds = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
            report = run_bank_shadow_report(
                conn, analysis_at=analysis_at, thresholds=thresholds, output_path=args.report_out
            )
            print(report.to_json(orient="records", force_ascii=False, indent=2, date_format="iso"))
        elif args.cmd == "backtest":
            from src.analytics.backtest_runner import run_backtest
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required for backtest")
            run_id = run_backtest(conn, args.start, args.end, args.rebalance, args.hold, args.ensure_scores, args.ratios, args.sectors, args.weights)
            print(f"Backtest run_id={run_id} (CSV: outputs/backtest_{run_id}.csv)")
        elif args.cmd == "optimize-weights":
            from src.analytics.weight_optimizer import optimize_weights
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required for optimize-weights")
            result = optimize_weights(
                conn, args.start, args.end,
                hold_days=args.hold, step=args.step,
                objective=args.objective, min_m2=args.min_m2,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
