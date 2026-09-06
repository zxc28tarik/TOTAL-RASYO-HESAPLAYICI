# Positive recovery of one actual financial correction pair

Five focused official KAP requests captured one March 2023 financial-disclosure
query, two disclosure pages, and their two Excel exports. No calendar-completeness
endpoint was used. `python -m scripts.audit_p7_version_pair` verifies the captured
bytes and builds the same receipt twice.

KORTS 2022 financial report #1122417 was published 9 March 2023 at 18:36:13 +03:00.
Report #1126845 was published 21 March 2023 at 18:32:11 +03:00. The latter page
explicitly links to the former through “Düzeltilmiş Bildirim”. The detailed query
independently supplies both publication timestamps and correction metadata.

Both exports contain 437 numeric slots, with four changes. The original assigned
1,833,392 / 2,079,640 to non-controlling interests and -76 / -155 to parent interests;
the revision reverses these assignments. These amounts are presented in thousands
of TRY. A cutoff between the two publications must not use the corrected parent
profit. The audit tests this ordering using the two actual recovered sources.

The preserved original-hash 2022 annual bulk ZIP includes only the newer KORTS
disclosure. Its 437 numeric slots equal the newer export, although the full export
bytes differ from the archived member. The separately recorded bulk comparison
does not mislabel those differing bytes as identical.

This is a positive recovery result and a concrete demonstration of the historical
version risk. It does not establish exhaustive report-version enumeration for the
6000-cell universe. The current query has no proven historical retention or deleted
version guarantee. P7 remains blocked; the demonstrated two-version selector is
research code and does not change production source selection.
