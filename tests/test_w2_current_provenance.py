import pytest

from scripts.audit_w2_current_provenance import differences, indexed, validate_lineage


@pytest.mark.parametrize("before,after", [(.5, .51), (True, 1), ({"a": 1}, {"b": 1}),
                                         ([1], [1, 2]), (None, .5), (float("nan"), float("nan"))])
def test_changed_missing_or_ambiguous_value_is_never_unaffected(before, after):
    assert differences(before, after)


def test_only_finite_roundoff_is_tolerated():
    assert differences({"score": .5}, {"score": .5 + 1e-15}) == []


def test_duplicate_target_is_rejected():
    with pytest.raises(ValueError, match="DUPLICATE_TICKER"):
        indexed([{"ticker": "X"}, {"ticker": "X"}])


def fixture():
    fact = {"lineage_sha256": "fact", "dimensions": {"member_sha256": "member"},
            "disclosure_id": "KAP:123", "published_at": "2026-09-01T00:00:00+00:00",
            "canonical_field": "REVENUE", "period_end": "2026-06-30"}
    return {"reports": [{"member_sha256": "member", "notification_id": "123",
                          "published_at": fact["published_at"]}],
            "used_facts": {"fact": fact}, "nonfin_quarters": {"X": [{"source_lineage": [
                {k: fact[k] for k in ("lineage_sha256", "disclosure_id", "canonical_field", "period_end")}]}]},
            "core_diagnostics": {"per_ticker": {}}}


@pytest.mark.parametrize("mutation", ["disclosure", "publication", "member", "quarter"])
def test_tampered_primary_lineage_is_rejected(mutation):
    data = fixture()
    validate_lineage(data)
    if mutation == "quarter":
        data["nonfin_quarters"]["X"][0]["source_lineage"][0]["period_end"] = "2026-03-31"
    elif mutation == "member":
        data["used_facts"]["fact"]["dimensions"]["member_sha256"] = "other"
    else:
        data["used_facts"]["fact"]["disclosure_id" if mutation == "disclosure" else "published_at"] = (
            "KAP:456" if mutation == "disclosure" else "2026-09-02T00:00:00+00:00")
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        validate_lineage(data)


@pytest.mark.parametrize("replacement,changed", [(b"original\r\n", False), (b"changed\n", True)])
def test_baseline_allows_only_checkout_newlines(monkeypatch, tmp_path, replacement, changed):
    from scripts import audit_w2_current_provenance as audit
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    source = tmp_path / "source.txt"
    source.write_bytes(b"original\n")
    (tmp_path / "baseline.json").write_bytes(audit.encoded({"live_files": [audit.fingerprint(source)]}))
    source.write_bytes(replacement)
    if changed:
        with pytest.raises(ValueError, match="W2_FROZEN_INPUT_CHANGED"):
            audit.baseline(tmp_path)
    else:
        audit.baseline(tmp_path)
