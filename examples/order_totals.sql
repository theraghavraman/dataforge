SELECT region, SUM(CAST(amount AS REAL)) AS total
FROM orders WHERE status='accepted' GROUP BY region ORDER BY region;
