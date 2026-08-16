# Total Rasyo Hesaplayıcı — Adım Adım Kurulum

## 1) Python paketlerini kur

```bash
pip install -r requirements.txt
```

## 2) PostgreSQL tablolarını oluştur

```bash
psql -f sql/010_create_core_tables.sql
psql -f sql/000_create_schemas.sql
psql -f sql/001_create_analytics_tables.sql
psql -f sql/003_decile_thresholds.sql
psql -f sql/005_backtest_tables.sql
psql -f sql/006_trailing_alpha_period_m2_tables.sql
psql -f sql/007_valuation_and_backtest_ext.sql
```

Alternatif:

```bash
make core
make migrate
```

## 3) Hisse evrenini yükle

```bash
python -m src.app.cli ingest-universe --file data/universe_stocks.csv
```

## 4) Hisse fiyatlarını yükle

```bash
python -m src.app.cli ingest-prices --file data/prices_daily.csv
```

## 5) Endeks fiyatlarını yükle

```bash
python -m src.app.cli ingest-index --file data/index_prices_daily.csv
```

## 6) Finansal verileri yükle

```bash
python -m src.app.cli ingest-fin --file data/financials_quarterly.csv
```

## 7) Core veri kontrolü yap

```bash
python -m src.app.cli validate-core
```

## 8) Oranları hesapla

```bash
python -m src.app.cli calc-ratios --ratios config/ratios.json
```

## 9) Ana pipeline'ı çalıştır

```bash
python -m src.app.cli run-daily --asof 2026-02-20 --ratios config/ratios.json --sectors config/sectors.json --weights config/weights.json
```

Bu komut yeni mantıkta şunları üretir:

```text
analytics.alpha_trailing
analytics.period_8q_comparison
analytics.expected_band_periods
analytics.m2_period_comparison
analytics.module_scores
```

## 10) M2 yorumlarını kontrol et

PostgreSQL'de:

```sql
SELECT ticker, m2_final, m2_label, m2_commentary
FROM analytics.m2_period_comparison
WHERE asof_date = '2026-02-20'
ORDER BY m2_final DESC;
```

## 11) Final skorları kontrol et

```sql
SELECT ticker, final_score, decision, m1, m2, m3, ek1, ek4, ek9
FROM analytics.module_scores
WHERE asof_date = '2026-02-20'
ORDER BY final_score DESC;
```
