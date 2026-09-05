# Remaining execution migrated to Codex/Astra by user decision

Effective 2026-09-05, the user explicitly authorized Codex/Astra to complete
the remaining work without Claude. This prospective decision supersedes the
named GPT + Claude requirement in the execution plan and open issue templates
for remaining work only. Historical review records are unchanged.

The remaining acceptance gate is:

1. Codex/Astra implementation pass.
2. A separate Codex/Astra second-pass audit of the diff, source evidence,
   failure cases and mutation coverage, without assuming implementation claims.
3. Mutation/regression tests, applicable BANK v4.7 and database tests.
4. Successful CI and deterministic replay with source and artifact hashes.

Only completion of all applicable gates permits `ASTRA_CONVERGED_PASS`.
This is a same-model second pass, not cross-model review. No Claude PASS is
implied or fabricated. Authorization alone closes no technical acceptance gate.

## Integration boundary

The supplied Desktop `TOTAL-RASYO-HESAPLAYICI-main` directory has no `.git`.
A separate clone, `TOTAL-RASYO-HESAPLAYICI-astra`, was created from the canonical
repository. Branch `codex/astra-v24-finalize` starts from canonical base
`dd2028953bd24f4cd11d0beb2fda93b86ad94930`. The three source branches share that
merge base and have disjoint file changes; their histories were preserved by
merges into the integration branch for subsequent review:

- P1 semantic: `9a9d6e71677104c10a41e22b5a07547a1e5545d0`.
- P2 source evidence: `1219260f9b95b7c181526b033b1410b86272a19d`.
- Price-level contract: `5870723e1e4a6c44338e85dbd8aef1d71b3090c9`.

Integration is not technical approval or production promotion. Main is unchanged.
Experimental results remain `EXPERIMENTAL_RISK_ACCEPTED_5Y`; #24/#36 require
actual historical superseded-version enumeration before authoritative closure.

The Total Rasyo weights, veto, ranking, PIT timing, no-neutral-fill rule and
source completeness requirements remain unchanged.
