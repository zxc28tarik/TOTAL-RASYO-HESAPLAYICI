"""Offline, frozen-clock W2 audit. Never writes data/live or changes scoring policy."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/audit/w2_current_provenance_v1"
BASE = "0db6cc19a357892afdd43b2fa103968d30ce1825"
START = "f3731ceb47e666f483474f97fe47be1d69ec1cc1"
LIVE = ROOT / "data/live"


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                       allow_nan=False, separators=(",", ":")) + "\n").encode()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_value(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def fingerprint(path):
    path = Path(path)
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path),
            "lf_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()}


def differences(before, after, location="", tolerance=1e-12):
    """All structural/identity changes are exact; only finite float round-off is tolerated."""
    if isinstance(before, dict) and isinstance(after, dict):
        if before.keys() != after.keys():
            return [location + ":KEY_SET"]
        return [p for key in before for p in differences(before[key], after[key], location+"/"+key, tolerance)]
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [location + ":LENGTH"]
        return [p for i, (a, b) in enumerate(zip(before, after)) for p in differences(a, b, location+f"/{i}", tolerance)]
    if isinstance(before, bool) or isinstance(after, bool):
        return [] if type(before) is type(after) and before == after else [location]
    if isinstance(before, (float, int)) and isinstance(after, (float, int)):
        return [] if math.isfinite(before) and math.isfinite(after) and math.isclose(before, after, rel_tol=tolerance, abs_tol=tolerance) else [location]
    return [] if before == after else [location]


def dependency_closure(seed):
    todo, found = [seed], set()
    while todo:
        name = todo.pop()
        if name in found or not (ROOT / name).is_file():
            continue
        found.add(name)
        module = name[:-3].replace("/", ".")
        for node in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join(module.split(".")[:-node.level] + ([base] if base else []))
                names = [base] + [base+"."+n.name for n in node.names]
            todo.extend(n.replace(".", "/")+".py" for n in names if n.startswith(("src.", "scripts.")))
    return sorted(found)


def baseline(output):
    target = output / "baseline.json"
    if not target.exists():
        if git("diff", BASE, "HEAD", "--", "data/live"):
            raise ValueError("LIVE_DATA_CHANGED_SINCE_W1_BASELINE")
        paths = git("ls-files", "data/live").splitlines()
        doc = {"contract": "W2_START_BASELINE_V1", "start_commit": START,
               "w1_baseline_sha256": sha(ROOT / "data/audit/w1_live_fail_closed_v1/baseline.json"),
               "live_files": [fingerprint(ROOT / p) for p in paths],
               "production_changes_in_w2": False}
        output.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(encoded(doc))
    return read(target)


def primary_replay(output, archive_dir):
    from scripts import materialize_current_nonfin_valuation as nonfin
    from scripts import materialize_current_core_modules as core
    from scripts import experimental_core_module_materializer as core_engine
    valuation_receipt = read(LIVE / "current_nonfin_valuation_v1/receipt.json")
    core_receipt = read(LIVE / "current_core_modules_v1/receipt.json")
    for name, expected in valuation_receipt["archive_hashes"].items():
        if sha(archive_dir / name) != expected or core_receipt["archive_hashes"][name] != expected:
            raise ValueError("ARCHIVE_HASH_MISMATCH:"+name)
    market_tickers = set(pd.read_csv(LIVE / "current_market_modules_v1/modules.csv").ticker)
    print("W2: remapping frozen primary KAP archives", flush=True)
    reports, archive_hashes = nonfin._mapped_reports(archive_dir, market_tickers | set(valuation_receipt["routed_tickers"]))
    facts = {f["lineage_sha256"]: f for r in reports for f in r["facts"]}
    mapped_metadata = [r["report"] for r in reports]
    quarters, snapshots, core_diagnostics = {}, {}, {}
    original_derive = nonfin.derive_company_quarters
    original_value = nonfin.value_nonfin_snapshot
    original_core = core.build_core_modules

    def mapped(_directory, tickers):
        return [r for r in reports if r["report"]["source_entity_code"] in tickers], archive_hashes

    def derive(*args, **kwargs):
        result = original_derive(*args, **kwargs)
        quarters[kwargs["ticker"]] = [asdict(q) for q in result]
        return result

    def value(target, peers, config):
        snapshots[target.ticker+":"+str(target.anchor_period_end)] = {
            "target": asdict(target), "peers": [p.ticker for p in peers]}
        return original_value(target, peers, config)

    def build_core(*args, **kwargs):
        result = original_core(*args, **kwargs)
        core_diagnostics.update(result)
        return result

    frozen = datetime.fromisoformat(valuation_receipt["analysis_at"])
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    with tempfile.TemporaryDirectory(prefix="rasyo-w2-replay-") as temp:
        tmp = Path(temp)
        with patch.object(nonfin, "datetime", FrozenClock), patch.object(nonfin, "_mapped_reports", mapped), \
             patch.object(nonfin, "derive_company_quarters", derive), patch.object(nonfin, "value_nonfin_snapshot", value):
            nonfin.materialize(archive_dir=archive_dir, basis_dir=nonfin.DEFAULT_BASIS,
                              routes_path=nonfin.DEFAULT_ROUTES, output_dir=tmp / "nonfin")
        print("W2: NONFIN raw -> valuation/FOLLOW/M2 replay finished", flush=True)
        with patch.object(core, "_mapped_reports", mapped), patch.object(core, "build_core_modules", build_core):
            core.materialize(archive_dir=archive_dir, routes_path=core.DEFAULT_ROUTES,
                             route_manifest_path=core.DEFAULT_ROUTE_MANIFEST, output_dir=tmp / "core",
                             analysis_at=datetime.fromisoformat(core_receipt["analysis_at"]))
        regenerated = {name: rows(tmp / "nonfin" / (name+".jsonl")) for name in
                       ("valuations", "previous_valuations", "follow", "m2")}
        regenerated["core"] = rows(tmp / "core/modules.jsonl")
    used = {s["lineage_sha256"] for qs in quarters.values() for q in qs for s in q["source_lineage"]}
    # Core cohort diagnostics are retained too: Total's M1/Ek1 use cross-company ratios.
    used.update(s["lineage_sha256"] for d in core_diagnostics["per_ticker"].values()
                for q in d["quarters"] for s in q["source_lineage"])
    if not used.issubset(facts):
        raise ValueError("DERIVED_FACT_LINEAGE_UNBOUND")
    evidence = {"contract": "W2_PRIMARY_REPLAY_EVIDENCE_V1", "archive_hashes": archive_hashes,
                "reports": mapped_metadata, "used_facts": {k: facts[k] for k in sorted(used)},
                "nonfin_quarters": quarters, "nonfin_snapshots": snapshots,
                "core_diagnostics": core_diagnostics, "regenerated": regenerated,
                "share_field_note": "Derived capital/1 shares are diagnostic only; materializer scrubs them and uses certified current quoted nominal units."}
    (output / "primary_replay.json.gz").write_bytes(gzip.compress(encoded(evidence), mtime=0))
    print(json.dumps({"reports": len(reports), "used_facts": len(used), "m2": len(regenerated["m2"])}), flush=True)
    return json.loads(encoded(evidence))


def load_original_module(path, name):
    """Execute the pinned original module in memory; never replace working files."""
    source = subprocess.check_output(["git", "show", BASE+":"+path], cwd=ROOT).decode()
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / path)
    sys.modules[name] = module
    exec(compile(source, path+"@"+BASE, "exec"), module.__dict__)
    return module


def indexed(values):
    result = {row["ticker"]: row for row in values}
    if len(result) != len(values):
        raise ValueError("DUPLICATE_TICKER")
    return result


def replay_valuations(evidence):
    from src.analytics.nonfin_valuation import (NonfinSnapshot, NonfinValuationConfig,
        value_nonfin_snapshot, combine_nonfin_m2, build_nonfin_snapshot)
    from src.analytics.current_nonfin_follow import materialize_current_nonfin_follow
    config = NonfinValuationConfig.from_json_file(ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json")
    caps = pd.read_csv(LIVE / "current_price_level_basis_v1/market_caps.csv").set_index("ticker")
    snapshots = {}
    for key, entry in evidence["nonfin_snapshots"].items():
        values = dict(entry["target"])
        values["analysis_at"] = datetime.fromisoformat(values["analysis_at"])
        for field in ("anchor_period_end", "price_trade_date"):
            values[field] = date.fromisoformat(values[field])
        target = NonfinSnapshot(**values)
        # Independently bind TTM snapshot back to the derived quarters and
        # certified quote-unit denominator, not the diagnostic capital/1 field.
        quarters = [q for q in evidence["nonfin_quarters"][target.ticker]
                    if q["period_end"] <= str(target.anchor_period_end)][-4:]
        inputs = [dict(q["values"], period_end=date.fromisoformat(q["period_end"]),
                       published_at=datetime.fromisoformat(q["published_at"]),
                       derivation_profile=q["derivation_profile"], derivation_version=q["derivation_version"])
                  for q in quarters]
        if not inputs:
            raise ValueError("QUARTER_LINEAGE_MISSING")
        inputs[-1]["shares_out"] = float(caps.loc[target.ticker, "quoted_nominal_units_out"])
        rebuilt = build_nonfin_snapshot(ticker=target.ticker, analysis_at=target.analysis_at,
            sector_code=target.sector_code, current_price=target.current_price,
            price_trade_date=target.price_trade_date, quarters=inputs)
        if differences(json.loads(encoded(asdict(target))), json.loads(encoded(asdict(rebuilt)))):
            raise ValueError("SNAPSHOT_QUARTER_OR_SHARE_BINDING_MISMATCH:"+key)
        snapshots[key] = target
    current, previous = [], []
    expected_current = indexed(evidence["regenerated"]["valuations"])
    for key, target in snapshots.items():
        peers = [snapshots[t+":"+str(target.anchor_period_end)]
                 for t in evidence["nonfin_snapshots"][key]["peers"]]
        val = value_nonfin_snapshot(target, peers, config)
        dest = current if str(target.anchor_period_end) == expected_current[target.ticker]["anchor_period_end"] else previous
        dest.append(val)
    prices = pd.read_csv(LIVE / "current_market_modules_v1/stock_prices.csv.gz")
    follow, rejected = materialize_current_nonfin_follow(current_valuations=current,
        previous_valuations=previous, adjusted_prices=prices)
    fmap = indexed(follow)
    m2 = [combine_nonfin_m2(v, follow_score=fmap[v["ticker"]]["follow_score"], follow_active=True, config=config)
          for v in current if v["status"] == "OK" and v["ticker"] in fmap]
    return {k: json.loads(encoded(sorted(v, key=lambda r: r["ticker"])))
            for k, v in {"valuations": current, "previous_valuations": previous, "follow": follow, "m2": m2}.items()}


def market_phases():
    from src.analytics import historical_pit_ek9_replay as latest
    from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay
    from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
    market = LIVE / "current_market_modules_v1"
    receipt = read(market / "receipt.json")
    stocks, indices = pd.read_csv(market / "stock_prices.csv.gz"), pd.read_csv(market / "index_prices.csv.gz")
    calendar = indices.loc[indices.index_code.eq("XU100"), ["trade_date"]]
    routes = pd.read_csv(ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz")
    day = pd.Timestamp(receipt["cutoff_date"])
    active = routes.loc[pd.to_datetime(routes.valid_from).le(day) &
        (pd.to_datetime(routes.valid_to, errors="coerce").isna() | pd.to_datetime(routes.valid_to, errors="coerce").gt(day)) &
        routes.sector_index_code.isin(["XUSIN", "XUHIZ", "XUTEK"]), ["ticker", "sector_index_code"]].drop_duplicates()
    kwargs = dict(analysis_at=datetime.fromisoformat(receipt["captured_at"]),
                  asof_date=date.fromisoformat(receipt["cutoff_date"]),
                  market_asof_date=date.fromisoformat(receipt["market_asof_date"]),
                  universe=active, trading_calendar=calendar, stock_prices=stocks)
    original = load_original_module("src/analytics/historical_pit_ek9_replay.py", "w2_original_ek9_replay")
    original.compute_ek9_volatility_scores = load_original_module(
        "src/analytics/ek9_volatility.py", "w2_original_ek9_math").compute_ek9_volatility_scores
    phases = {}
    for name in ("BASELINE", "W1_A", "W1_B", "W1_C"):
        engine = latest if name == "W1_C" else original
        result = engine.run_historical_pit_ek9_replay(**kwargs)
        phases[name] = {"scores": indexed(json.loads(encoded(result.ek9_scores.to_dict("records")))),
                        "rejections": json.loads(encoded(result.rejections.to_dict("records")))}
    other = {}
    for name, engine, attr in (("M3", run_historical_pit_m3_replay, "m3_scores"),
                               ("Ek4", run_historical_pit_ek4_replay, "ek4_scores")):
        result = engine(**kwargs, index_prices=indices)
        other[name] = indexed(json.loads(encoded(getattr(result, attr).to_dict("records"))))
    return phases, other, stocks


def validate_lineage(evidence):
    report_by_member = {r["member_sha256"]: r for r in evidence["reports"]}
    for digest, fact in evidence["used_facts"].items():
        report = report_by_member.get(fact["dimensions"]["member_sha256"])
        if (not report or fact["lineage_sha256"] != digest or
            fact["disclosure_id"] != "KAP:"+str(report["notification_id"]) or
            datetime.fromisoformat(fact["published_at"]) != datetime.fromisoformat(report["published_at"])):
            raise ValueError("FACT_REPORT_IDENTITY_MISMATCH")
    all_quarters = list(evidence["nonfin_quarters"].values()) + [
        d["quarters"] for d in evidence["core_diagnostics"]["per_ticker"].values()]
    for quarters in all_quarters:
        for q in quarters:
            for item in q["source_lineage"]:
                fact = evidence["used_facts"].get(item["lineage_sha256"])
                if not fact or any(item[k] != fact[k] for k in ("disclosure_id", "canonical_field", "period_end")):
                    raise ValueError("QUARTER_FACT_IDENTITY_MISMATCH")


def assemble(output):
    from src.analytics.total_rasyo_score import MODULE_KEYS, compute_total_rasyo
    evidence = json.loads(gzip.decompress((output / "primary_replay.json.gz").read_bytes()))
    validate_lineage(evidence)
    rebuilt = replay_valuations(evidence)
    for key, value in rebuilt.items():
        if differences(evidence["regenerated"][key], value):
            raise ValueError("PRIMARY_SNAPSHOT_REPLAY_MISMATCH:"+key)
    phases, market_other, prices = market_phases()
    source_dir = LIVE / "current_nonfin_valuation_v1"
    original = {name: indexed(rows(source_dir / (name+".jsonl"))) for name in rebuilt}
    regenerated = {name: indexed(value) for name, value in rebuilt.items()}
    original_core = indexed(rows(LIVE / "current_core_modules_v1/modules.jsonl"))
    regenerated_core = indexed(evidence["regenerated"]["core"])
    original_market = pd.read_csv(LIVE / "current_market_modules_v1/modules.csv").set_index("ticker")
    totals = rows(LIVE / "current_total_scores_v1/totals.jsonl")
    changed = set(git("diff", "--name-only", BASE, START, "--", "src", "scripts").splitlines())
    seeds = ["materialize_current_nonfin_valuation", "materialize_current_core_modules",
             "materialize_current_market_modules", "materialize_current_total_rasyo"]
    closures = {s: dependency_closure("scripts/"+s+".py") for s in seeds}
    if any("src/analytics/run_daily_pipeline.py" in paths for paths in closures.values()):
        raise ValueError("W1_DAILY_PATH_REACHABLE_REQUIRES_NEW_AUDIT")
    # Only the Ek9 helper/replay may differ in this current assembly's dependencies.
    allowed = {"src/analytics/ek9_volatility.py", "src/analytics/historical_pit_ek9_replay.py"}
    if (set().union(*map(set, closures.values())) & changed) - allowed:
        raise ValueError("UNEXPLAINED_W1_DEPENDENCY_CHANGE")
    output_rows = []
    phase_names = ["W1_A", "W1_B", "W1_C"]
    caps = pd.read_csv(LIVE / "current_price_level_basis_v1/market_caps.csv").set_index("ticker")
    bundles = {r["ticker"]: r for r in read(LIVE / "current_price_level_basis_v1/action_bundle_index.json")["entries"]}
    for ticker, row in original["m2"].items():
        diffs = {key: differences(original[key].get(ticker), regenerated[key].get(ticker)) for key in rebuilt}
        trace = {
            "generator": "scripts/materialize_current_nonfin_valuation.py:materialize",
            "current_valuation_sha256": digest_value(original["valuations"][ticker]),
            "previous_valuation_sha256": digest_value(original["previous_valuations"][ticker]),
            "follow_row": original["follow"][ticker],
            "quarter_evidence_key": "nonfin_quarters/"+ticker,
            "snapshot_evidence_keys": [key for key in evidence["nonfin_snapshots"] if key.startswith(ticker+":")],
            "share_basis_row": caps.loc[ticker].to_dict(), "action_bundle": bundles[ticker],
            "current_peer_tickers": original["valuations"][ticker]["diagnostics"]["peer_tickers"],
            "previous_peer_tickers": original["previous_valuations"][ticker]["diagnostics"]["peer_tickers"],
        }
        output_rows.append({"module": "M2", "ticker": ticker,
            "classification": "UNRESOLVED" if any(diffs.values()) else "UNAFFECTED",
            "differences": diffs, "baseline_row_sha256": digest_value(row),
            "before": row["m2"], "after": regenerated["m2"].get(ticker, {}).get("m2"),
            "phases": {p: {"w1_effect": "UNAFFECTED", "reason": "CURRENT_GENERATOR_DOES_NOT_REACH_DAILY_M2_M3_READERS"} for p in phase_names},
            "provenance": trace})
    for ticker, row in original_market.loc[original_market.ek9.notna()].iterrows():
        phase_values = {p: phases[p]["scores"].get(ticker, {}).get("ek9") for p in ["BASELINE"]+phase_names}
        end = phases["W1_C"]["scores"].get(ticker, {})
        window = prices.loc[prices.ticker.eq(ticker) & prices.trade_date.ge(end.get("start_date", "9999")) &
                            prices.trade_date.le(end.get("end_date", "0000"))].sort_values("trade_date")
        diffs = {p: differences(float(row.ek9), v) for p, v in phase_values.items()}
        output_rows.append({"module": "Ek9", "ticker": ticker,
            "classification": "UNRESOLVED" if any(diffs.values()) else "UNAFFECTED",
            "before": float(row.ek9), "after": phase_values["W1_C"], "phases": phase_values,
            "differences": diffs, "provenance": {"production_engine": "run_historical_pit_ek9_replay",
                "market_capture_at": read(LIVE / "current_market_modules_v1/receipt.json")["captured_at"],
                "window": end, "selected_stock_rows": window.to_dict("records"),
                "selected_stock_rows_sha256": digest_value(window.to_dict("records"))}})
    for row in totals:
        ticker = row["ticker"]
        core = regenerated_core.get(ticker, {})
        component_changes = differences(original_core[ticker], core)
        phase_values = {}
        for phase in ["BASELINE"]+phase_names:
            scores = {key: row[key.lower()] for key in MODULE_KEYS}
            scores["M2"] = regenerated["m2"].get(ticker, {}).get("m2")
            scores["Ek9"] = phases[phase]["scores"].get(ticker, {}).get("ek9")
            phase_values[phase] = compute_total_rasyo(scores, good_count_ge8=row["good_count_ge8"])["final_score"]
        for module in ("M3", "Ek4"):
            component_changes.extend(differences(row[module.lower()], market_other[module][ticker][module.lower()], module))
        latest_scores = {key: row[key.lower()] for key in MODULE_KEYS}
        latest_scores.update(M1=core.get("m1"), Ek1=core.get("ek1"))
        latest = compute_total_rasyo(latest_scores, good_count_ge8=core["good_count_ge8"]) if core else None
        output_rows.append({"module": "Total", "ticker": ticker,
            "classification": "UNRESOLVED" if component_changes else "UNAFFECTED",
            "before": row["final_score"], "after_w1_only": phase_values["W1_C"],
            "latest_core_replay_total": latest, "differences": component_changes,
            "phases": phase_values, "provenance": {"original_module_inputs": row,
                "original_core_row": original_core[ticker], "latest_core_replay_row": core,
                "core_generator_commit": git("log", "-1", "--format=%H", START, "--", "data/live/current_core_modules_v1/modules.jsonl"),
                "core_evidence_key": "core_diagnostics/per_ticker/"+ticker,
                "market_replayed_inputs": {m: market_other[m][ticker] for m in ("M3", "Ek4")}}})
    # Count every target explicitly; never infer completeness from a nonempty result.
    if Counter(r["module"] for r in output_rows) != {"M2": 48, "Ek9": 11, "Total": 2}:
        raise ValueError("TARGET_POPULATION_MISMATCH")
    (output / "rows.jsonl").write_bytes(b"".join(encoded(row) for row in output_rows))
    core_changes = {ticker: differences(before, regenerated_core.get(ticker)) for ticker, before in original_core.items()}
    core_changes = {k: v for k, v in core_changes.items() if v}
    stage_report = {"contract": "W2_W1_COMPONENT_COUNTERFACTUALS_V1",
        "phases": {p: {"ek9_valid": len(v["scores"]), "ek9_rejected": len(v["rejections"]),
                       "score_rows_sha256": digest_value(v["scores"]), "rejections_sha256": digest_value(v["rejections"])}
                   for p, v in phases.items()},
        "method": "Retrospective isolated component replays; not contemporaneous checkpoints. W1 A/B/C shipped in one code commit.",
        "current_dependency_closures": closures, "w1_changed_dependencies": sorted(set().union(*map(set, closures.values())) & changed),
        "current_m2_reader_source": "NONFIN_RELATIVE_TWO_AXIS_V1_FILE_NOT_NULLABLE_DB_READER",
        "core_source_replay_changes": core_changes}
    (output / "phases.json").write_bytes(encoded(stage_report))
    summary = {"contract": "W2_CURRENT_PROVENANCE_AUDIT_V1", "baseline_commit": BASE,
        "w2_start_commit": START, "input_baseline_sha256": sha(output / "baseline.json"),
        "network_access": False, "production_or_live_artifacts_modified": False,
        "target_counts": dict(Counter(r["module"] for r in output_rows)),
        "classification_counts": dict(Counter(r["classification"] for r in output_rows)),
        "core_replay_changed_ticker_count": len(core_changes),
        "source_replay_count": {k: len(v) for k, v in evidence["regenerated"].items()},
        "primary_bytes_replayed_locally": True, "primary_archives_available_in_ci": False,
        "ci_verification_scope": "Hash-bound evidence + independent snapshot/phase replay; not primary ZIP remapping",
        "stale_run_receipt": {"path": "data/live/current_total_rasyo_run_v1/receipt.json",
            "status": "STALE_2026_09_08_RUN_NOT_CURRENT_COMPONENT_ASSEMBLY",
            "old_m2": 0, "old_total": 0, "current_m2": 48, "current_total": 2},
        "outputs": {p.name: sha(p) for p in (output / "primary_replay.json.gz", output / "rows.jsonl", output / "phases.json")}}
    (output / "receipt.json").write_bytes(encoded(summary))
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "private/reconstructed_kap_archives")
    parser.add_argument("--primary-replay", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    baseline(args.output_dir)
    if args.primary_replay:
        evidence = primary_replay(args.output_dir, args.archive_dir)
        for name, actual in evidence["regenerated"].items():
            source = LIVE / ("current_core_modules_v1/modules.jsonl" if name == "core" else
                             "current_nonfin_valuation_v1/"+name+".jsonl")
            print(name, "differences", differences(rows(source), actual)[:30], flush=True)
    if args.assemble:
        assemble(args.output_dir)


if __name__ == "__main__":
    main()
