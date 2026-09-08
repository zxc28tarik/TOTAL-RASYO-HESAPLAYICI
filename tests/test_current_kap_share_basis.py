from decimal import Decimal

from scripts.capture_kap_share_class_history import validate_history_row


def test_current_share_basis_requires_explicit_per_class_nominal_values():
    row = validate_history_row("SAFE", {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "08/09/2026 18:30:00",
        "value": [{
            "shareGroup": "A", "monetaryUnitOfPerShare": {"key": "TRY"},
            "monetaryUnitOfShares": {"key": "TRY"},
            "nominalValuePerShare": "0,01", "nominalValueOfShares": "100,00",
            "exchangeTradedOrNot": "Traded", "registeredOrBearerShare": "Bearer",
        }],
    })
    assert row["usable"] is True
    assert row["derived_shares"] == "10000"
    assert row["total_nominal_value_try"] == "100.00"


def test_current_share_basis_rejects_missing_nominal_instead_of_assuming_one_try():
    row = validate_history_row("SAFE", {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "08/09/2026 18:30:00",
        "value": [{
            "shareGroup": "A", "monetaryUnitOfPerShare": {"key": "TRY"},
            "monetaryUnitOfShares": {"key": "TRY"},
            "nominalValuePerShare": None, "nominalValueOfShares": "100,00",
        }],
    })
    assert row["usable"] is False
    assert row["derived_shares"] is None


def test_dot_decimal_is_not_misread_as_thousands_separator():
    row = validate_history_row("SAFE", {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "08/09/2026 18:30:00",
        "value": [{
            "shareGroup": "A", "monetaryUnitOfPerShare": {"key": "TRY"},
            "monetaryUnitOfShares": {"key": "TRY"},
            "nominalValuePerShare": "0,01", "nominalValueOfShares": "191447068.25",
        }],
    })
    assert row["usable"] is True
    assert Decimal(row["derived_shares"]) == Decimal("19144706825")


def test_multiple_dot_groups_are_accepted_only_as_explicit_thousands_format():
    row = validate_history_row("SAFE", {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "08/09/2026 18:30:00",
        "value": [{
            "shareGroup": "A", "monetaryUnitOfPerShare": {"key": "TRY"},
            "monetaryUnitOfShares": {"key": "TRY"},
            "nominalValuePerShare": "1", "nominalValueOfShares": "2.000.000.000",
        }],
    })
    assert row["derived_shares"] == "2000000000"
