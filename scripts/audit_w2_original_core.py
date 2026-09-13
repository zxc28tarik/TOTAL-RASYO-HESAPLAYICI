"""Reproduce the archived CORE output with its original, hash-bound derivation code."""
from __future__ import annotations

import ast
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import gzip
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_w2_current_provenance import OUT, LIVE, START, encoded, read, rows, sha, git, differences
from scripts.materialize_experimental_financial_facts import map_report


def run(output=OUT):
    from scripts import materialize_current_core_modules as core
    from scripts import experimental_core_module_materializer as engine
    from src.ingest import company_fact_materializer as derivation
    import subprocess
    evidence = json.loads(gzip.decompress((output / "primary_replay.json.gz").read_bytes()))
    archive_dir = ROOT / "private/reconstructed_kap_archives"
    for name, digest in evidence["archive_hashes"].items():
        if sha(archive_dir / name) != digest:
            raise ValueError("ARCHIVE_HASH_MISMATCH")
    # This is CPU-parallel parsing of already-local public reports, not agent delegation.
    tasks = [(str(archive_dir / r["archive_name"]), r) for r in evidence["reports"]]
    print("W2: remapping original CORE inputs with four parser processes", flush=True)
    with ProcessPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(map_report, tasks, chunksize=8))
    commit = git("log", "-1", "--format=%H", START, "--", "data/live/current_core_modules_v1/modules.jsonl")
    path = "src/ingest/company_fact_materializer.py"
    source = subprocess.check_output(["git", "show", commit+":"+path], cwd=ROOT).decode()
    old_ast, new_ast = ast.parse(source), ast.parse((ROOT / path).read_text(encoding="utf-8"))
    classes = lambda tree: [ast.dump(n) for n in tree.body if isinstance(n, ast.ClassDef)]
    if classes(old_ast) != classes(new_ast):
        raise ValueError("ORIGINAL_DERIVATION_CLASS_CONTRACT_CHANGED")
    namespace = dict(vars(derivation))
    functions = ast.Module(body=[n for n in old_ast.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], type_ignores=[])
    exec(compile(functions, path+"@"+commit, "exec"), namespace)
    diagnostics = {}
    original_build = core.build_core_modules
    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        diagnostics.update(result)
        return result
    def mapped(_directory, tickers):
        return [r for r in reports if r["report"]["source_entity_code"] in tickers], evidence["archive_hashes"]
    with tempfile.TemporaryDirectory(prefix="rasyo-w2-original-core-") as directory:
        with patch.object(core, "_mapped_reports", mapped), patch.object(core, "build_core_modules", build), \
             patch.object(engine, "derive_company_quarters", namespace["derive_company_quarters"]):
            core.materialize(archive_dir=archive_dir, routes_path=core.DEFAULT_ROUTES,
                route_manifest_path=core.DEFAULT_ROUTE_MANIFEST, output_dir=Path(directory),
                analysis_at=datetime.fromisoformat(read(LIVE / "current_core_modules_v1/receipt.json")["analysis_at"]))
        actual = rows(Path(directory) / "modules.jsonl")
    diff = differences(rows(LIVE / "current_core_modules_v1/modules.jsonl"), actual)
    used = {s["lineage_sha256"] for d in diagnostics["per_ticker"].values() for q in d["quarters"] for s in q["source_lineage"]}
    facts = {f["lineage_sha256"]: f for r in reports for f in r["facts"] if f["lineage_sha256"] in used}
    if set(facts) != used:
        raise ValueError("ORIGINAL_CORE_LINEAGE_UNBOUND")
    document = {"contract": "W2_ORIGINAL_CORE_GENERATOR_REPLAY_V1", "generator_commit": commit,
        "original_derivation_source_sha256": __import__("hashlib").sha256(source.encode()).hexdigest(),
        "original_derivation_source": source, "regenerated_core": actual,
        "original_core_diagnostics": diagnostics, "used_facts": facts,
        "differences_from_archived_core": diff, "archive_hashes": evidence["archive_hashes"]}
    (output / "original_core_replay.json.gz").write_bytes(gzip.compress(encoded(document), mtime=0))
    print(json.dumps({"core_rows": len(actual), "used_facts": len(facts), "differences": diff[:30]}), flush=True)


if __name__ == "__main__":
    run()
