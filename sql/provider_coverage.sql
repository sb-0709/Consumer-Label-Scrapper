-- Every terrestrial NBM provider in the indexed states, by number of locations served
-- (the to-do list for plan files). Satellite (60, 61) excluded.
SELECT
  LPAD(CAST(n.frn AS STRING), 10, '0')                   AS frn,
  n.brand_name                                           AS brand_name,
  STRING_AGG(DISTINCT CAST(n.technology AS STRING), ',') AS technologies,
  COUNT(DISTINCT n.location_id)                          AS n_locations
FROM `{nbm_table}` AS n
WHERE SAFE_CAST(n.technology AS INT64) NOT IN (60, 61)
  AND n.business_residential_code IN ('R', 'X')
  {nbm_partition_filter}
GROUP BY 1, 2
ORDER BY n_locations DESC;
