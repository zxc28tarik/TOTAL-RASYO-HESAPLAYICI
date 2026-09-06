# Retrospective share/capital continuity research

`continuity_receipt.json` is a reproducible research receipt, not a runtime action
bundle. Run `python -m scripts.audit_p2_share_continuity_v1` from the repository.
The two builds must have identical bytes. Production remains fail-closed.

Five additional financial reports were extracted from ZIP archives whose SHA256
matches the unchanged original archive manifest. Each captured member has its own
hash. They add INVES 2022 Q3, KLRHO 2023 Q2/Q3, and ASGYO 2023 year-end/2024 Q1.
Together with the six earlier dated reports, eight consecutive paid-in-capital
links reconcile. INVES remains TRY187.5m, ASGYO remains TRY659m, and KLRHO changes
from TRY650m to TRY1,625m through its documented 150% bonus.

KLRHO notifications #1139424 (17 April decision), #1155805 (2 June approval), and
#1156058 (5 June announcement of the 6 June ex-date) are observations of the same
economic action. They must not be counted as three actions. The first decision
does not establish an effective date, and none applies within the twelve P2
valuation intervals. Their exact public HTML bytes are preserved.

The next financial statements are deliberately retrospective evidence. They are
not supplied as facts available at the earlier P2 knowledge cutoff. In particular,
ASGYO 2023 year-end cannot bracket its February 2024 price date; its March 2024
balance point is used for that retrospective comparison.

Equal capital endpoints do not rule out offsetting intermediate transactions or
nominal-value changes. The new balance points report paid-in capital; they do not
independently establish unchanged denomination. Repeated official searches and
monthly partition equality establish response consistency, not historical
retention, superseded-version enumeration, or exhaustive prior-announcement
coverage. These limits are explicit in the receipt's negative-evidence contract.

Consequently all twelve cells retain `ACTION_COMPLETENESS_EVIDENCE_MISSING`.
No completeness assertion or executable action bundle is generated.
