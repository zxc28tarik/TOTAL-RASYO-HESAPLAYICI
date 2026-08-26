# Historical Cutoff / Execution Policy — TOTAL_RASYO_MONTHLY_OPEN_V1

Status: **PR candidate — independent audit required before merge/closure**

Scope: the monthly BIST 100 / Total Rasyo historical backtest for the exact 60-month window **2021-08 through 2026-07**.

This document authorizes the *timing policy only*. It does not claim that the final 60-month portfolio simulation has already been run.

## 1. Decision

The authorized profile is:

`TOTAL_RASYO_MONTHLY_OPEN_V1`

For each monthly signal date (the first observed XU100 trading day of the month):

1. **Information cutoff** = the end of the immediately preceding observed XU100 trading session in `Europe/Istanbul`.
   - normal/full session end: **18:10**
   - half-day session end: **12:40**
2. **Execution accounting timestamp** = **10:00** on the signal date in `Europe/Istanbul`.
3. **Execution price basis** = the already-authorized historical **daily OPEN** price.
4. Information with a timestamp **after the prior-session cutoff is excluded**, even if it became public overnight before the next opening.
5. No same-day information is allowed into the signal.

The 10:00 timestamp is not a claim that an order is first submitted at 10:00. It is the accounting boundary used for the daily OPEN execution assumption: the signal was frozen before the opening session, while the opening price is formed during the opening process immediately before continuous trading begins at 10:00.

## 2. Why this policy

### 2.1 No same-open hindsight

The backtest buys/sells at a daily OPEN price. Therefore the selection must be frozen before that opening price is observed. A prior-session cutoff gives a hard causal ordering:

`known information -> frozen Total Rasyo signal -> next signal-day opening execution`

The alternative of using a signal-day morning cutoff would require a separately audited model of KAP/vendor publication latency, ingestion latency and calculation latency. That latency model does not currently exist, so same-day information is deliberately excluded.

### 2.2 Market-hours basis

Borsa İstanbul announced that, effective 14 November 2016, Equity Market opening order collection begins at 09:40, continuous trading begins at 10:00, and full-day trading ends at 18:10. These hours therefore cover the entire 2021-08..2026-07 backtest window.

Official / primary references:

- Borsa İstanbul — `Borsa İstanbul Seans Saatleri değişiyor` (effective 2016-11-14):
  https://www.borsaistanbul.com/duyuru/11541/borsa-istanbul-seans-saatleri-degisiyor
- Borsa İstanbul — Equity Market trading hours:
  https://www.borsaistanbul.com/en/markets/equity-market/trading-hours
- Borsa İstanbul — 2021 Equity Market holiday table (28 October 2021 is half day):
  https://www.borsaistanbul.com/files/PayPiyasasi2021YiliTatilTablosu.pdf
- Borsa İstanbul — 2026 holiday announcement / Pay Market holiday-table attachment:
  https://www.borsaistanbul.com/files/41625-sayili-duyurumuz-hk.-turkce-04122025.pdf

Independent historical corroboration for the relevant half-day close:

- 28 October 2021: continuous trading to 12:30, closing session 12:30-12:40.
- 27 June 2023: Pay Market closed at 12:40 for the Kurban Bayramı half day.
- 26 May 2026: Pay Market half-day schedule closed at 12:40.

The implementation does **not** treat 18:10 as universal. It uses 12:40 on the three half-day sessions that are the immediately preceding trading day for one of the 60 monthly executions:

- `2021-10-28` -> execution month `2021-11`
- `2023-06-27` -> execution month `2023-07`
- `2026-05-26` -> execution month `2026-06`

All other predecessor sessions in the exact 60-month schedule are full sessions and use 18:10.

## 3. Closed upstream sources reused

The timing layer does not invent trading dates.

It reuses:

- `data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv`
  - exactly 60 first-observed XU100 trading dates;
  - the existing source file remains unchanged with blank `cutoff_at` / `execution_at` and `UNRESOLVED` policy status. This source remains a *date fact source*, not a policy file.
- `data/backtest_sources/m3_source_package/index_closes.csv.gz`
  - closed XU100 trading calendar covering the required lookback and all 60 signal months.

The policy layer derives the predecessor trading date from the closed XU100 calendar. It does not use weekday arithmetic or a current calendar fallback.

## 4. Code authority

Policy code:

`src/analytics/historical_cutoff_execution_policy.py`

Authorized object:

`TOTAL_RASYO_MONTHLY_OPEN_V1`

Pure derivation:

`build_authorized_cutoff_execution_schedule(...)`

Exact validator:

`validate_authorized_cutoff_execution_schedule(...)`

Machine-readable contract:

`cutoff_execution_policy_evidence()`

The generic append-only schedule registry remains generic. This policy is a versioned profile layered on top of it; future asset classes or execution models require a new explicitly authorized policy/profile rather than silently changing V1.

## 5. Fail-closed rules

The policy rejects:

- anything other than the exact ordered 60 months `2021-08..2026-07`;
- a non-XU100 signal source;
- duplicate signal dates or trading dates;
- a signal date that is not the first observed XU100 trading day of its month;
- a missing preceding XU100 trading day;
- any unapproved policy object;
- any registered/supplied schedule that differs from the derived schedule in date, session type, cutoff clock, execution clock, price basis, source identity or policy status;
- a same-day information cutoff;
- changing a half-day cutoff from 12:40 to the normal 18:10 clock;
- changing execution from 10:00 to the opening-auction placeholder/fixure time.

The policy additionally requires that its three half-day exceptions are **all and only actually exercised** by the 60-month predecessor-date set. This prevents a stale or silently unused exception list.

## 6. Fixture separation

Existing tests that use timestamps such as `previous day 20:00 / signal day 10:00` remain test fixtures for lower-level generic schedule contracts. They are **not** the authorized historical policy.

The authorized path is identified only by profile key:

`TOTAL_RASYO_MONTHLY_OPEN_V1`

and exact validation against `historical_cutoff_execution_policy.py`.

## 7. What becomes true only after this PR is independently audited and merged

After clean independent audit + merge + successful push CI/evidence persistence, the project may mark:

`real_cutoff_execution_clock_policy_authorized = true`

That statement means only that the timing contract is closed. It does **not** by itself prove portfolio performance.

The next stage must prove that the real 60-cutoff Total Rasyo production and subsequent monthly portfolio simulation actually consume this authorized profile and no fixture/arbitrary schedule.

## 8. Independent audit / mutation targets

At minimum mutate and require failure for:

1. full-day session end `18:10 -> 20:00`;
2. half-day session end `12:40 -> 18:10`;
3. execution `10:00 -> 09:55`;
4. same-day cutoff `09:30`;
5. remove one of the three half-day exception dates;
6. add a false half-day exception date;
7. move a signal to the second trading day of a month;
8. remove the predecessor trading day from the calendar;
9. reorder/remove one of the 60 months;
10. mutate the original signal-date source so `UNRESOLVED` is no longer preserved;
11. alter policy source hash/profile identity;
12. attempt to pass an arbitrary schedule that still satisfies only the generic `cutoff_at < execution_at` DB constraint.
