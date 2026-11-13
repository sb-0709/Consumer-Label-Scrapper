-- =============================================================================
-- One-time: address lookup table for the web app (one row per Fabric location,
-- with every NBM provider at that location). Clustered by ZIP, so a lookup
-- for one address reads only a few MB.
-- =============================================================================
CREATE OR REPLACE TABLE `{address_index_table}`
CLUSTER BY zip AS
WITH prov AS (
  SELECT
    CAST(n.location_id AS STRING) AS location_id,
    ARRAY_AGG(STRUCT(
      CAST(n.provider_id AS STRING)               AS provider_id,
      LPAD(CAST(n.frn AS STRING), 10, '0')        AS frn,
      n.brand_name                                AS brand_name,
      SAFE_CAST(n.technology AS INT64)            AS technology,
      SAFE_CAST(n.max_advertised_download_speed AS FLOAT64) AS down,
      SAFE_CAST(n.max_advertised_upload_speed   AS FLOAT64) AS up,
      n.business_residential_code                 AS bus_res
    ) ORDER BY n.brand_name, n.technology) AS providers
  FROM `{nbm_table}` AS n
  WHERE TRUE
    {nbm_partition_filter}
  GROUP BY 1
)
SELECT
  CAST(f.location_id AS STRING)                AS location_id,
  LPAD(CAST(f.block_geoid AS STRING), 15, '0') AS block_geoid,
  f.address_primary, f.city, f.state,
  LPAD(CAST(f.zip AS STRING), 5, '0')          AS zip,
  f.unit_count, f.building_type_code, f.latitude, f.longitude,
  p.providers
FROM `{fabric_table}` AS f
LEFT JOIN prov AS p ON CAST(f.location_id AS STRING) = p.location_id
WHERE (ARRAY_LENGTH(@states) = 0 OR f.state IN UNNEST(@states));
