-- Every NBM provider at one location (partition filter keeps this to one state's file)
SELECT
  CAST(n.provider_id AS STRING)        AS provider_id,
  LPAD(CAST(n.frn AS STRING), 10, '0') AS frn,
  n.brand_name                         AS brand_name,
  SAFE_CAST(n.technology AS INT64)     AS technology,
  SAFE_CAST(n.max_advertised_download_speed AS FLOAT64) AS down,
  SAFE_CAST(n.max_advertised_upload_speed   AS FLOAT64) AS up
FROM `{nbm_table}` AS n
WHERE CAST(n.location_id AS STRING) = @location_id
  {nbm_partition_filter}
ORDER BY brand_name, technology;
