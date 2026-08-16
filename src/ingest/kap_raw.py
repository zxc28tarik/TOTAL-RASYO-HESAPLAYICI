from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.ingest.api.mkk_kap import KapApiProtocolError, KapDisclosureEnvelope, KapFetchResult
from src.ingest.api.kap_public_universe import KapUniverseSnapshot
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig, KapFinancialFactExtractor


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def persist_kap_disclosures(
    conn: Any,
    result: KapFetchResult,
    *,
    stream_name: str = "disclosures",
) -> int:
    if not isinstance(result, KapFetchResult):
        raise TypeError("result KapFetchResult olmali")
    if not isinstance(stream_name, str) or not stream_name.strip():
        raise ValueError("stream_name bos olamaz")
    disclosures = tuple(result.disclosures)
    quarantined = tuple(result.quarantined_items)
    if result.complete == bool(quarantined):
        raise ValueError("result complete/quarantine sozlesmesi tutarsiz")
    sources = (
        {result.source}
        | {item.source for item in disclosures}
        | {item.source for item in quarantined}
    )
    if len(sources) > 1:
        raise ValueError("tek sync batch birden fazla source iceremez")
    source = next(iter(sources))
    last_success_at = max(
        (item.fetched_at for item in disclosures),
        default=result.completed_at,
    )

    with conn:
        with conn.cursor() as cur:
            for item in disclosures:
                cur.execute(
                    """
                    INSERT INTO raw.kap_disclosures (
                      source, disclosure_id, published_at, ticker, company_id,
                      notification_type, subject, source_url, payload,
                      payload_sha256, fetched_at, first_seen_at, last_seen_at
                    ) VALUES (
                      %s, %s, %s, %s, %s,
                      %s, %s, %s, %s::jsonb,
                      %s, %s, %s, %s
                    )
                    ON CONFLICT (source, disclosure_id) DO UPDATE SET
                      published_at = EXCLUDED.published_at,
                      ticker = COALESCE(EXCLUDED.ticker, raw.kap_disclosures.ticker),
                      company_id = COALESCE(EXCLUDED.company_id, raw.kap_disclosures.company_id),
                      notification_type = COALESCE(EXCLUDED.notification_type, raw.kap_disclosures.notification_type),
                      subject = COALESCE(EXCLUDED.subject, raw.kap_disclosures.subject),
                      source_url = COALESCE(EXCLUDED.source_url, raw.kap_disclosures.source_url),
                      payload = EXCLUDED.payload,
                      payload_sha256 = EXCLUDED.payload_sha256,
                      fetched_at = GREATEST(raw.kap_disclosures.fetched_at, EXCLUDED.fetched_at),
                      last_seen_at = GREATEST(raw.kap_disclosures.last_seen_at, EXCLUDED.last_seen_at)
                    """,
                    (
                        item.source,
                        item.disclosure_id,
                        item.published_at,
                        item.ticker,
                        item.company_id,
                        item.notification_type,
                        item.subject,
                        item.source_url,
                        _json_text(item.payload),
                        item.payload_sha256,
                        item.fetched_at,
                        item.fetched_at,
                        item.fetched_at,
                    ),
                )

            for item in quarantined:
                cur.execute(
                    """
                    INSERT INTO raw.kap_api_quarantine (
                      source, stream_name, window_start, window_end,
                      page_number, item_index, cursor_value, reason,
                      payload, payload_sha256, fetched_at, first_seen_at, last_seen_at,
                      attempts
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s::jsonb, %s, %s, %s, %s,
                      1
                    )
                    ON CONFLICT (
                      source, stream_name, window_start, window_end,
                      page_number, item_index, payload_sha256
                    ) DO UPDATE SET
                      last_seen_at = GREATEST(raw.kap_api_quarantine.last_seen_at, EXCLUDED.last_seen_at),
                      attempts = raw.kap_api_quarantine.attempts + 1,
                      reason = EXCLUDED.reason
                    """,
                    (
                        item.source, stream_name.strip(), result.start_at, result.end_at,
                        item.page_number, item.item_index, item.cursor_value, item.reason,
                        _json_text(item.payload), item.payload_sha256, item.fetched_at,
                        item.fetched_at, item.fetched_at,
                    ),
                )

            cur.execute(
                """
                INSERT INTO raw.kap_sync_runs (
                  source, stream_name, window_start, window_end, completed_at,
                  status, rows_seen, quarantined_count, pages_fetched,
                  next_cursor, metadata
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s::jsonb
                )
                """,
                (
                    source, stream_name.strip(), result.start_at, result.end_at,
                    result.completed_at, "COMPLETE" if result.complete else "QUARANTINED",
                    len(disclosures), len(quarantined), result.pages_fetched,
                    result.next_cursor,
                    _json_text({
                        "id_first": disclosures[0].disclosure_id if disclosures else None,
                        "id_last": disclosures[-1].disclosure_id if disclosures else None,
                    }),
                ),
            )

            if result.complete:
                cur.execute(
                """
                INSERT INTO raw.kap_sync_state (
                  source, stream_name, cursor_value, window_start, window_end,
                  last_success_at, rows_seen, pages_fetched, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source, stream_name) DO UPDATE SET
                  cursor_value = EXCLUDED.cursor_value,
                  window_start = EXCLUDED.window_start,
                  window_end = EXCLUDED.window_end,
                  last_success_at = EXCLUDED.last_success_at,
                  rows_seen = EXCLUDED.rows_seen,
                  pages_fetched = EXCLUDED.pages_fetched,
                  metadata = EXCLUDED.metadata
                WHERE EXCLUDED.window_end >= raw.kap_sync_state.window_end
                """,
                    (
                        source,
                        stream_name.strip(),
                        result.next_cursor,
                        result.start_at,
                        result.end_at,
                        last_success_at,
                        len(disclosures),
                        result.pages_fetched,
                        _json_text({
                            "id_first": disclosures[0].disclosure_id if disclosures else None,
                            "id_last": disclosures[-1].disclosure_id if disclosures else None,
                        }),
                    ),
                )
    return len(disclosures)


def persist_kap_universe(conn: Any, snapshot: KapUniverseSnapshot) -> int:
    required = {
        "ticker", "company_name", "sector_index_code", "sector_code", "is_active",
        "kap_company_id", "source_url", "universe_source",
    }
    missing = required - set(snapshot.frame.columns)
    if missing:
        raise ValueError(f"KAP universe frame eksik kolonlar: {sorted(missing)}")

    with conn:
        with conn.cursor() as cur:
            for row in snapshot.frame.to_dict(orient="records"):
                cur.execute(
                    """
                    INSERT INTO core.universe_stocks (
                      ticker, company_name, sector_index_code, sector_code, is_active,
                      kap_company_id, source_url, universe_source, source_updated_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (ticker) DO UPDATE SET
                      company_name = EXCLUDED.company_name,
                      kap_company_id = EXCLUDED.kap_company_id,
                      source_url = EXCLUDED.source_url,
                      universe_source = EXCLUDED.universe_source,
                      source_updated_at = EXCLUDED.source_updated_at,
                      is_active = TRUE,
                      updated_at = now(),
                      sector_index_code = COALESCE(core.universe_stocks.sector_index_code, EXCLUDED.sector_index_code),
                      sector_code = COALESCE(core.universe_stocks.sector_code, EXCLUDED.sector_code)
                    """,
                    (
                        row["ticker"],
                        row["company_name"],
                        row.get("sector_index_code"),
                        row.get("sector_code"),
                        bool(row["is_active"]),
                        row.get("kap_company_id"),
                        row.get("source_url"),
                        row.get("universe_source"),
                        snapshot.fetched_at,
                    ),
                )
    return int(len(snapshot.frame))


def _persist_fact_cursor(cur: Any, fact: Any) -> None:
    cur.execute(
        """
        INSERT INTO raw.kap_financial_facts (
          source, disclosure_id, mapping_profile, mapping_version, fact_key,
          ticker, published_at, version_tag, version_sequence, fact_code,
          period_start, period_end, currency, unit_scale, raw_value_text,
          normalized_value, scaled_value, statement_scope, dimensions, extracted_at
        ) VALUES (
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s::jsonb, %s
        )
        ON CONFLICT (source, disclosure_id, mapping_profile, mapping_version, fact_key)
        DO UPDATE SET extracted_at = GREATEST(
          raw.kap_financial_facts.extracted_at, EXCLUDED.extracted_at
        )
        """,
        (
            fact.source, fact.disclosure_id, fact.mapping_profile,
            fact.mapping_version, fact.fact_key, fact.ticker,
            fact.published_at, fact.version_tag, fact.version_sequence,
            fact.fact_code, fact.period_start, fact.period_end,
            fact.currency, fact.unit_scale, fact.raw_value_text,
            fact.normalized_value, fact.scaled_value, fact.statement_scope,
            _json_text(fact.dimensions), fact.extracted_at,
        ),
    )


def persist_kap_financial_facts(conn: Any, facts: Iterable[Any]) -> int:
    facts = tuple(facts)
    with conn:
        with conn.cursor() as cur:
            for fact in facts:
                _persist_fact_cursor(cur, fact)
    return len(facts)


@dataclass(frozen=True)
class KapFactExtractionReport:
    disclosures_seen: int
    disclosures_extracted: int
    disclosures_rejected: int
    facts_written: int
    rejected_ids: tuple[str, ...]


def extract_pending_kap_financial_facts(
    conn: Any,
    config: KapFinancialFactConfig,
    *,
    source: str = "MKK_KAP_API",
    notification_type: str | None = "FINANCIAL_STATEMENT",
    limit: int = 1000,
    retry_rejections: bool = False,
    extracted_at: datetime,
) -> KapFactExtractionReport:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit pozitif Python int olmali")
    if not isinstance(extracted_at, datetime) or extracted_at.tzinfo is None or extracted_at.utcoffset() is None:
        raise ValueError("extracted_at timezone iceren datetime olmali")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source bos olamaz")
    if notification_type is not None and (
        not isinstance(notification_type, str) or not notification_type.strip()
    ):
        raise ValueError("notification_type None veya dolu metin olmali")
    if not isinstance(retry_rejections, bool):
        raise ValueError("retry_rejections Python bool olmali")
    extractor = KapFinancialFactExtractor(config)

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  d.source, d.disclosure_id, d.published_at, d.ticker, d.company_id,
                  d.notification_type, d.subject, d.source_url, d.payload,
                  d.payload_sha256, d.fetched_at
                FROM raw.kap_disclosures d
                WHERE d.source = %s
                  AND (%s IS NULL OR d.notification_type = %s)
                  AND NOT EXISTS (
                    SELECT 1
                    FROM raw.kap_financial_facts f
                    WHERE f.source = d.source
                      AND f.disclosure_id = d.disclosure_id
                      AND f.mapping_profile = %s
                      AND f.mapping_version = %s
                  )
                  AND (
                    %s
                    OR NOT EXISTS (
                      SELECT 1
                      FROM raw.kap_fact_extraction_rejections r
                      WHERE r.source = d.source
                        AND r.disclosure_id = d.disclosure_id
                        AND r.mapping_profile = %s
                        AND r.mapping_version = %s
                        AND r.payload_sha256 = d.payload_sha256
                    )
                  )
                ORDER BY d.published_at, d.disclosure_id
                LIMIT %s
                """,
                (
                    source.strip(), notification_type, notification_type,
                    config.mapping_profile, config.mapping_version,
                    retry_rejections, config.mapping_profile, config.mapping_version,
                    limit,
                ),
            )
            rows = cur.fetchall()
            extracted_count = 0
            rejected: list[str] = []
            facts_written = 0
            for row in rows:
                disclosure_id = str(row[1])
                try:
                    payload = row[8]
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError as exc:
                            raise KapApiProtocolError("raw payload gecersiz JSON") from exc
                    if not isinstance(payload, Mapping):
                        raise KapApiProtocolError("raw payload JSON nesnesi olmali")
                    envelope = KapDisclosureEnvelope(
                        source=row[0], disclosure_id=disclosure_id, published_at=row[2],
                        ticker=row[3], company_id=row[4], notification_type=row[5],
                        subject=row[6], source_url=row[7], payload=payload,
                        payload_sha256=row[9], fetched_at=row[10],
                    )
                    facts = extractor.extract(envelope, extracted_at=extracted_at)
                except KapApiProtocolError as exc:
                    rejected.append(disclosure_id)
                    cur.execute(
                        """
                        INSERT INTO raw.kap_fact_extraction_rejections (
                          source, disclosure_id, mapping_profile, mapping_version,
                          payload_sha256, reason, first_rejected_at, last_rejected_at, attempts
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                        ON CONFLICT (source, disclosure_id, mapping_profile, mapping_version)
                        DO UPDATE SET
                          payload_sha256 = EXCLUDED.payload_sha256,
                          reason = EXCLUDED.reason,
                          last_rejected_at = EXCLUDED.last_rejected_at,
                          attempts = raw.kap_fact_extraction_rejections.attempts + 1
                        """,
                        (
                            row[0], disclosure_id,
                            config.mapping_profile, config.mapping_version,
                            row[9], str(exc), extracted_at, extracted_at,
                        ),
                    )
                    continue
                for fact in facts:
                    _persist_fact_cursor(cur, fact)
                cur.execute(
                    """
                    DELETE FROM raw.kap_fact_extraction_rejections
                    WHERE source = %s AND disclosure_id = %s
                      AND mapping_profile = %s AND mapping_version = %s
                    """,
                    (
                        envelope.source, envelope.disclosure_id,
                        config.mapping_profile, config.mapping_version,
                    ),
                )
                extracted_count += 1
                facts_written += len(facts)

    return KapFactExtractionReport(
        disclosures_seen=len(rows),
        disclosures_extracted=extracted_count,
        disclosures_rejected=len(rejected),
        facts_written=facts_written,
        rejected_ids=tuple(rejected),
    )
