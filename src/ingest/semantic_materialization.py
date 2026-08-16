from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import groupby
from typing import Any, Iterable, Mapping

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import (
    SemanticFactMapper,
    SemanticFinancialFact,
    SemanticMappingConfig,
)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _persist_semantic_cursor(cur: Any, fact: SemanticFinancialFact) -> None:
    cur.execute(
        """
        INSERT INTO core.semantic_financial_facts (
          source, disclosure_id, semantic_profile, semantic_version,
          canonical_field, lineage_sha256, ticker, sector_family,
          published_at, version_tag, version_sequence, nature,
          period_start, period_end, currency, statement_scope, value,
          source_fact_code, source_fact_key, source_mapping_profile,
          source_mapping_version, dimensions, mapped_at
        ) VALUES (
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s::jsonb, %s
        )
        ON CONFLICT (
          source, disclosure_id, semantic_profile, semantic_version,
          canonical_field, lineage_sha256
        ) DO UPDATE SET
          mapped_at = GREATEST(core.semantic_financial_facts.mapped_at, EXCLUDED.mapped_at)
        """,
        (
            fact.source,
            fact.disclosure_id,
            fact.semantic_profile,
            fact.semantic_version,
            fact.canonical_field,
            fact.lineage_sha256,
            fact.ticker,
            fact.sector_family,
            fact.published_at,
            fact.version_tag,
            fact.version_sequence,
            fact.nature,
            fact.period_start,
            fact.period_end,
            fact.currency,
            fact.statement_scope,
            fact.value,
            fact.source_fact_code,
            fact.source_fact_key,
            fact.source_mapping_profile,
            fact.source_mapping_version,
            _json_text(fact.dimensions),
            fact.mapped_at,
        ),
    )


def persist_semantic_facts(conn: Any, facts: Iterable[SemanticFinancialFact]) -> int:
    rows = tuple(facts)
    if not rows:
        return 0
    with conn:
        with conn.cursor() as cur:
            for row in rows:
                _persist_semantic_cursor(cur, row)
    return len(rows)


@dataclass(frozen=True)
class SemanticMappingReport:
    disclosures_seen: int
    disclosures_mapped: int
    disclosures_rejected: int
    semantic_facts_written: int
    rejected_ids: tuple[str, ...]


def _row_to_raw_fact(row: Mapping[str, Any]) -> KapFinancialFact:
    dimensions = row["dimensions"]
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions)
        except json.JSONDecodeError as exc:
            raise KapApiProtocolError("raw semantic source dimensions gecersiz JSON") from exc
    if not isinstance(dimensions, Mapping):
        raise KapApiProtocolError("raw semantic source dimensions nesne olmali")
    normalized = Decimal(str(row["normalized_value"]))
    scaled = Decimal(str(row["scaled_value"]))
    return KapFinancialFact(
        source=row["source"],
        disclosure_id=row["disclosure_id"],
        mapping_profile=row["mapping_profile"],
        mapping_version=int(row["mapping_version"]),
        fact_key=row["fact_key"],
        ticker=row["ticker"],
        published_at=row["published_at"],
        version_tag=row["version_tag"],
        version_sequence=int(row["version_sequence"]),
        fact_code=row["fact_code"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        currency=row["currency"],
        unit_scale=int(row["unit_scale"]),
        raw_value_text=row["raw_value_text"],
        normalized_value=normalized,
        scaled_value=scaled,
        statement_scope=row["statement_scope"],
        dimensions=dict(dimensions),
        extracted_at=row["extracted_at"],
    )


def map_pending_semantic_facts(
    conn: Any,
    config: SemanticMappingConfig,
    *,
    source: str = "MKK_KAP_API",
    source_mapping_profile: str,
    source_mapping_version: int,
    limit: int = 1000,
    retry_rejections: bool = False,
    mapped_at: datetime,
) -> SemanticMappingReport:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source bos olamaz")
    if not isinstance(source_mapping_profile, str) or not source_mapping_profile.strip():
        raise ValueError("source_mapping_profile bos olamaz")
    if isinstance(source_mapping_version, bool) or not isinstance(source_mapping_version, int) or source_mapping_version <= 0:
        raise ValueError("source_mapping_version pozitif Python int olmali")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit pozitif Python int olmali")
    if not isinstance(retry_rejections, bool):
        raise ValueError("retry_rejections Python bool olmali")
    if not isinstance(mapped_at, datetime) or mapped_at.tzinfo is None or mapped_at.utcoffset() is None:
        raise ValueError("mapped_at timezone iceren datetime olmali")

    mapper = SemanticFactMapper(config)
    columns = (
        "source", "disclosure_id", "mapping_profile", "mapping_version", "fact_key",
        "ticker", "published_at", "version_tag", "version_sequence", "fact_code",
        "period_start", "period_end", "currency", "unit_scale", "raw_value_text",
        "normalized_value", "scaled_value", "statement_scope", "dimensions", "extracted_at",
        "payload_sha256",
    )

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH pending AS (
                  SELECT DISTINCT
                    f.source, f.disclosure_id, d.published_at
                  FROM raw.kap_financial_facts f
                  JOIN raw.kap_disclosures d
                    ON d.source = f.source AND d.disclosure_id = f.disclosure_id
                  WHERE f.source = %s
                    AND f.mapping_profile = %s
                    AND f.mapping_version = %s
                    AND f.ticker IS NOT NULL
                    AND NOT EXISTS (
                      SELECT 1
                      FROM core.semantic_financial_facts s
                      WHERE s.source = f.source
                        AND s.disclosure_id = f.disclosure_id
                        AND s.semantic_profile = %s
                        AND s.semantic_version = %s
                    )
                    AND (
                      %s
                      OR NOT EXISTS (
                        SELECT 1
                        FROM core.semantic_mapping_rejections r
                        WHERE r.source = f.source
                          AND r.disclosure_id = f.disclosure_id
                          AND r.semantic_profile = %s
                          AND r.semantic_version = %s
                          AND r.source_payload_sha256 = d.payload_sha256
                      )
                    )
                  ORDER BY d.published_at, f.disclosure_id
                  LIMIT %s
                )
                SELECT
                  f.source, f.disclosure_id, f.mapping_profile, f.mapping_version,
                  f.fact_key, f.ticker, f.published_at, f.version_tag,
                  f.version_sequence, f.fact_code, f.period_start, f.period_end,
                  f.currency, f.unit_scale, f.raw_value_text, f.normalized_value,
                  f.scaled_value, f.statement_scope, f.dimensions, f.extracted_at,
                  d.payload_sha256
                FROM pending p
                JOIN raw.kap_financial_facts f
                  ON f.source = p.source AND f.disclosure_id = p.disclosure_id
                JOIN raw.kap_disclosures d
                  ON d.source = f.source AND d.disclosure_id = f.disclosure_id
                WHERE f.mapping_profile = %s AND f.mapping_version = %s
                ORDER BY f.source, f.disclosure_id, f.period_end, f.fact_code, f.fact_key
                """,
                (
                    source.strip(), source_mapping_profile.strip(), source_mapping_version,
                    config.mapping_profile, config.mapping_version,
                    retry_rejections, config.mapping_profile, config.mapping_version,
                    limit,
                    source_mapping_profile.strip(), source_mapping_version,
                ),
            )
            db_rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            seen = 0
            mapped = 0
            written = 0
            rejected: list[str] = []
            key_fn = lambda row: (row["source"], row["disclosure_id"])
            for (_, disclosure_id), grouped in groupby(db_rows, key=key_fn):
                seen += 1
                rows = list(grouped)
                payload_sha = rows[0]["payload_sha256"]
                try:
                    raw_facts = tuple(_row_to_raw_fact(row) for row in rows)
                    semantic = mapper.map_facts(raw_facts, mapped_at=mapped_at)
                except (KapApiProtocolError, ValueError, ArithmeticError) as exc:
                    rejected.append(str(disclosure_id))
                    cur.execute(
                        """
                        INSERT INTO core.semantic_mapping_rejections (
                          source, disclosure_id, semantic_profile, semantic_version,
                          source_payload_sha256, reason, first_rejected_at,
                          last_rejected_at, attempts
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                        ON CONFLICT (
                          source, disclosure_id, semantic_profile, semantic_version
                        ) DO UPDATE SET
                          source_payload_sha256 = EXCLUDED.source_payload_sha256,
                          reason = EXCLUDED.reason,
                          last_rejected_at = EXCLUDED.last_rejected_at,
                          attempts = core.semantic_mapping_rejections.attempts + 1
                        """,
                        (
                            rows[0]["source"], disclosure_id,
                            config.mapping_profile, config.mapping_version,
                            payload_sha, str(exc), mapped_at, mapped_at,
                        ),
                    )
                    continue
                for row in semantic:
                    _persist_semantic_cursor(cur, row)
                cur.execute(
                    """
                    DELETE FROM core.semantic_mapping_rejections
                    WHERE source = %s AND disclosure_id = %s
                      AND semantic_profile = %s AND semantic_version = %s
                    """,
                    (
                        rows[0]["source"], disclosure_id,
                        config.mapping_profile, config.mapping_version,
                    ),
                )
                mapped += 1
                written += len(semantic)

    return SemanticMappingReport(
        disclosures_seen=seen,
        disclosures_mapped=mapped,
        disclosures_rejected=len(rejected),
        semantic_facts_written=written,
        rejected_ids=tuple(rejected),
    )
