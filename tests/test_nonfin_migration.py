from pathlib import Path


def test_nonfin_migration_has_point_in_time_and_geometry_constraints():
    sql = Path("sql/022_nonfin_relative_valuation.sql").read_text(encoding="utf-8")
    assert "analytics.nonfin_valuation_periods" in sql
    assert "analytics.nonfin_m2_scores" in sql
    assert "AT TIME ZONE 'Europe/Istanbul'" in sql
    assert "v_low > 0 AND v_low <= v_mid AND v_mid <= v_high" in sql
    assert "config_sha256 ~ '^[0-9a-f]{64}$'" in sql
    assert "source_derivation_profile TEXT NOT NULL" in sql
    assert "source_derivation_version INT NOT NULL" in sql


def test_migration_order_includes_nonfin_valuation():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "psql -f sql/021_company_semantic_materialization.sql" in makefile
    assert "psql -f sql/022_nonfin_relative_valuation.sql" in makefile
    assert makefile.index("sql/021_company_semantic_materialization.sql") < makefile.index(
        "sql/022_nonfin_relative_valuation.sql"
    )


def test_make_has_nonfin_batch_and_self_audit_targets():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "run-nonfin-batch:" in makefile
    assert "self-audit-nonfin-valuation:" in makefile
    assert "scripts/self_audit_nonfin_valuation.py" in makefile
