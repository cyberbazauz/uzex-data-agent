\echo == counts by year
SELECT extract(year FROM deal_date)::int AS year, count(*) AS deals
FROM deals GROUP BY 1 ORDER BY 1;

\echo == reference counts
SELECT
  (SELECT count(*) FROM contracts) AS contracts,
  (SELECT count(*) FROM participants) AS participants,
  (SELECT count(*) FROM brokers) AS brokers,
  (SELECT count(*) FROM usd_rates) AS usd_rates;

\echo == contract types
SELECT contract_type, count(*) FROM deals GROUP BY 1 ORDER BY 1;

\echo == control sums
SELECT
  SUM(d.deal_amount) FILTER (WHERE c.currency = 'Узбекский сум') AS internal_uzs,
  SUM(d.deal_amount) FILTER (WHERE c.currency = 'Доллар США') AS export_usd,
  SUM(d.deal_amount * u.uzs_per_usd) FILTER (WHERE c.currency = 'Доллар США') AS export_converted_uzs,
  SUM(d.deal_amount) FILTER (WHERE c.currency = 'Узбекский сум')
    + SUM(d.deal_amount * u.uzs_per_usd) FILTER (WHERE c.currency = 'Доллар США') AS total_uzs
FROM deals d
JOIN contracts c USING (contract_no)
JOIN usd_rates u ON u.rate_date = d.deal_date;

\echo == nulls
SELECT
  count(*) FILTER (WHERE payment_date IS NULL) AS payment_null,
  count(*) FILTER (WHERE delivery_date IS NULL) AS delivery_null,
  count(*) FILTER (WHERE delivered_lots IS NULL) AS delivered_null
FROM deals;

\echo == statuses
SELECT status, count(*) FROM deals GROUP BY 1 ORDER BY 2 DESC;

\echo == 2024 golden
SELECT count(DISTINCT deal_no) FROM deals
WHERE deal_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31';

\echo == reader write attempt must fail
INSERT INTO deals (deal_no) VALUES ('x');
