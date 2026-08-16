"""
V22-B — toplayici katman. Saf hesaplayiciyi (reconciliation_module_freshness)
veritabanina baglar. BAGIMSIZ sorgularla calisir; V19/V20/V21'in kapali
dosyalarina DOKUNMAZ.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.analytics.reconciliation_module_freshness import (
    ConsumedModule,
    ProducerSuccessor,
)
from src.analytics.total_rasyo_score import MODULE_KEYS

CONSUMED_SQL = """
SELECT module, module_score, module_missing, module_source_at,
       module_analysis_at, module_source_run_key, identity_known
FROM analytics.total_rasyo_module_input
WHERE total_rasyo_run_id = %(run_id)s AND ticker = %(ticker)s
"""

# TOTAL_STALE (M1/M3/Ek1/Ek4/Ek9): HALEF KURALI -- ASLA look-ahead degil.
SUCCESSOR_NEWER_SQL = """
SELECT count(*) FROM analytics.module_production_lineage
WHERE ticker = %(ticker)s AND module = %(module)s
  AND analysis_at <= %(total_rasyo_analysis_at)s
  AND analysis_at > %(consumed_analysis_at)s
"""

# MODULE_LINEAGE_STALE: AYNI etiket, GUNCEL kimlik.
SUCCESSOR_SAME_LABEL_SQL = """
SELECT source_version_id FROM analytics.module_production_lineage
WHERE ticker = %(ticker)s AND module = %(module)s
  AND analysis_at = %(consumed_analysis_at)s
"""

# M2 ZAYIF PROXY: guncel kanonik satirin m2_source_at'i.
M2_CANONICAL_SQL = """
SELECT m2_source_at FROM analytics.company_total_rasyo_result
WHERE ticker = %(ticker)s AND analysis_at = %(total_rasyo_analysis_at)s
"""


def fetch_consumed_modules(conn: Any, *, total_rasyo_run_id: str,
                           ticker: str) -> Optional[dict[str, ConsumedModule]]:
    """
    V22-A snapshot'ini okur. Hic satir yoksa None doner -- reconciliation
    bunu INCOMPLETE olarak ele almalidir (kanit hic YAZILMAMIS demektir).
    """
    with conn.cursor() as cur:
        cur.execute(CONSUMED_SQL, {"run_id": total_rasyo_run_id,
                                   "ticker": ticker.strip().upper()})
        satirlar = cur.fetchall()
    if not satirlar:
        return None
    out: dict[str, ConsumedModule] = {}
    for modul, skor, eksik, kaynak_at, analiz_at, kimlik, bilinen in satirlar:
        out[modul] = ConsumedModule(
            module=modul, missing=bool(eksik), source_at=kaynak_at,
            analysis_at=analiz_at, source_run_key=kimlik,
            identity_known=bool(bilinen))
    return out


def fetch_successors(conn: Any, *, ticker: str, total_rasyo_analysis_at: datetime,
                     consumed_modules: dict[str, ConsumedModule],
                     ) -> dict[str, ProducerSuccessor]:
    """
    Her modul icin GUNCEL uretici durumunu BAGIMSIZ sorgularla toplar.
    """
    kod = ticker.strip().upper()
    out: dict[str, ProducerSuccessor] = {}
    with conn.cursor() as cur:
        for anahtar in MODULE_KEYS:
            tuketilen = consumed_modules.get(anahtar)
            if tuketilen is None or tuketilen.missing:
                out[anahtar] = ProducerSuccessor(
                    module=anahtar, newer_eligible_exists=False,
                    same_label_source_run_key=None,
                    freshness_available=False, lineage_lookup_available=False)
                continue

            if anahtar == "M2":
                # ZAYIF PROXY: module_production_lineage'de M2 YOK.
                if tuketilen.source_at is None:
                    out[anahtar] = ProducerSuccessor(
                        "M2", False, None, False, False)
                    continue
                cur.execute(M2_CANONICAL_SQL,
                           {"ticker": kod,
                            "total_rasyo_analysis_at": total_rasyo_analysis_at})
                satir = cur.fetchone()
                guncel_m2_source_at = satir[0] if satir else None
                yeni_var = (guncel_m2_source_at is not None
                           and guncel_m2_source_at > tuketilen.source_at)
                out["M2"] = ProducerSuccessor(
                    module="M2", newer_eligible_exists=yeni_var,
                    same_label_source_run_key=None,
                    freshness_available=guncel_m2_source_at is not None,
                    lineage_lookup_available=False)  # M2 lineage HICBIR ZAMAN
                continue

            if tuketilen.analysis_at is None:
                # Baglam bilinmiyor: ne freshness ne lineage yapilabilir.
                out[anahtar] = ProducerSuccessor(
                    anahtar, False, None, False, False)
                continue

            cur.execute(SUCCESSOR_NEWER_SQL,
                       {"ticker": kod, "module": anahtar,
                        "total_rasyo_analysis_at": total_rasyo_analysis_at,
                        "consumed_analysis_at": tuketilen.analysis_at})
            yeni_var = cur.fetchone()[0] > 0

            guncel_kimlik = None
            lineage_lookup_ok = False
            if tuketilen.identity_known:
                cur.execute(SUCCESSOR_SAME_LABEL_SQL,
                           {"ticker": kod, "module": anahtar,
                            "consumed_analysis_at": tuketilen.analysis_at})
                satir = cur.fetchone()
                if satir is not None:
                    guncel_kimlik = satir[0]
                    lineage_lookup_ok = True

            out[anahtar] = ProducerSuccessor(
                module=anahtar, newer_eligible_exists=yeni_var,
                same_label_source_run_key=guncel_kimlik,
                freshness_available=True,
                lineage_lookup_available=lineage_lookup_ok)
    return out
