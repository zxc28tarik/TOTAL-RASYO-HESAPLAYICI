-- Python/psycopg2 çağrısı. Asıl seçim mantığı migration içinde tanımlanan
-- analytics.bank_point_in_time_slots fonksiyonundadır; kabul testi de aynısını çağırır.
SELECT *
FROM analytics.bank_point_in_time_slots(
  %(ticker)s::text,
  %(analysis_at)s::timestamptz,
  %(anchor_period_end)s::date
);
