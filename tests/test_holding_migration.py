from pathlib import Path


def test_holding_migration_has_point_in_time_immutability_and_geometry_guards():
    sql = Path("sql/023_holding_nav_valuation.sql").read_text(encoding="utf-8")
    assert "core.holding_nav_snapshots" in sql
    assert "analytics.holding_valuation_periods" in sql
    assert "analytics.holding_m2_scores" in sql
    assert "analytics.holding_valuation_rejections" in sql
    assert "currency TEXT NOT NULL" in sql
    assert "published_at TIMESTAMPTZ" in sql
    assert "nav_asof_date <= (published_at AT TIME ZONE 'Europe/Istanbul')::date" in sql
    assert "nav_published_at <= analysis_at" in sql
    assert "v_low > 0 AND v_low <= v_mid AND v_mid <= v_high" in sql
    assert "reject_holding_nav_mutation" in sql
    assert "source_sha256 ~ '^[0-9a-f]{64}$'" in sql
    assert "canonical_sha256 ~ '^[0-9a-f]{64}$'" in sql


def test_migration_order_includes_holding_after_nonfin():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "psql -f sql/022_nonfin_relative_valuation.sql" in makefile
    assert "psql -f sql/023_holding_nav_valuation.sql" in makefile
    assert makefile.index("sql/022_nonfin_relative_valuation.sql") < makefile.index(
        "sql/023_holding_nav_valuation.sql"
    )


def test_make_has_holding_targets():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "ingest-holding-nav:" in makefile
    assert "run-holding-batch:" in makefile
    assert "self-audit-holding-valuation:" in makefile
    assert "scripts/self_audit_holding_valuation.py" in makefile


def test_holding_migration_persists_share_basis_contract():
    sql = Path("sql/023_holding_nav_valuation.sql").read_text(encoding="utf-8")
    assert sql.count("share_basis TEXT NOT NULL") >= 2
    config = Path("config/holding_valuation.nav_discount_v1.json").read_text(encoding="utf-8")
    assert '"share_basis": "ADJUSTED_PRICE_SERIES_V1"' in config
