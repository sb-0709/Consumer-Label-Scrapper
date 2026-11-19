-- Every terrestrial provider in one NBM release (@period) of the nested location table,
-- by number of locations served. Satellite (60, 61) excluded.
SELECT
  LPAD(CAST(p.frn AS STRING), 10, '0')                   AS frn,
  p.brand_name                                           AS brand_name,
  STRING_AGG(DISTINCT CAST(p.technology AS STRING), ',') AS technologies,
  COUNT(DISTINCT c.location_id)                          AS n_locations
FROM `{location_table}` AS c, UNNEST(c.provider_details) AS p
WHERE p.period = @period
  AND SAFE_CAST(p.technology AS INT64) NOT IN (60, 61)
  AND p.business_residential_code IN ('R', 'X')
  AND (ARRAY_LENGTH(@states) = 0 OR c.location_details.state IN UNNEST(@states))
GROUP BY 1, 2
ORDER BY n_locations DESC;
