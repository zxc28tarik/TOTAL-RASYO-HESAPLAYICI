from scripts.capture_kap_share_class_history import parse_bist_members, validate_history_row


def test_parse_bist_members_reads_escaped_next_payload():
    page = (
        b'xx {\\"mkkMemberOid\\":\\"abc123\\",\\"kapMemberTitle\\":\\"A\\",'
        b'\\"stockCode\\":\\"AAA\\"} yy'
    )
    assert parse_bist_members(page) == {"AAA": "abc123"}


def test_parse_bist_members_expands_multi_code_issuer():
    page = (
        b'xx {\\"mkkMemberOid\\":\\"abc123\\",\\"kapMemberTitle\\":\\"A\\",'
        b'\\"stockCode\\":\\"KRDMA, KRDMB, KRDMD\\"} yy'
    )
    assert parse_bist_members(page) == {
        "KRDMA": "abc123", "KRDMB": "abc123", "KRDMD": "abc123",
    }


def test_explicit_class_nominals_derive_shares_without_one_tl_assumption():
    row = {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "02/05/2024 15:59:59",
        "value": [
            {
                "shareGroup": "A", "nominalValuePerShare": "0,01",
                "monetaryUnitOfPerShare": {"key": "TRY"},
                "nominalValueOfShares": "100", "monetaryUnitOfShares": {"key": "TRY"},
                "exchangeTradedOrNot": {"key": "1"}, "registeredOrBearerShare": None,
            },
            {
                "shareGroup": "B", "nominalValuePerShare": "2",
                "monetaryUnitOfPerShare": {"key": "TRY"},
                "nominalValueOfShares": "200", "monetaryUnitOfShares": {"key": "TRY"},
                "exchangeTradedOrNot": {"key": "0"}, "registeredOrBearerShare": None,
            },
        ],
    }
    result = validate_history_row("AAA", row)
    assert result["usable"] is True
    assert result["derived_shares"] == "10100"
    assert result["total_nominal_value_try"] == "300"


def test_incomplete_old_kap_row_stays_explicit_rejection():
    row = {
        "itemKey": "kpy41_acc5_sermayeyi_temsil_eden",
        "creationDate": "04/09/2023 11:09:56",
        "value": [{
            "shareGroup": "A", "nominalValuePerShare": None,
            "monetaryUnitOfPerShare": None, "nominalValueOfShares": "100",
            "monetaryUnitOfShares": None,
        }],
    }
    result = validate_history_row("AAA", row)
    assert result["usable"] is False
    assert result["status"] == "PER_SHARE_UNIT_NOT_TRY"
