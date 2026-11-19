-- =============================================================================
-- One-time: the app's address lookup table, built from a location table that already
-- nests every NBM release per Fabric location (e.g. ck3378.combined_location_data):
--   location_id, block_geoid, location_details(address_primary, city, state, zip, ...),
--   provider_details[](frn, provider_id, brand_name, technology, max_advertised_*, period, ...)
-- Keeps the providers of ONE NBM release (@period). Same output columns as 04_build_address_index.sql.
-- =============================================================================
CREATE OR REPLACE TABLE `{address_index_table}`
CLUSTER BY zip AS
SELECT
  CAST(c.location_id AS STRING)                               AS location_id,
  LPAD(CAST(c.block_geoid AS STRING), 15, '0')                AS block_geoid,
  c.location_details.address_primary                          AS address_primary,
  c.location_details.city                                     AS city,
  c.location_details.state                                    AS state,
  LPAD(CAST(c.location_details.zip AS STRING), 5, '0')        AS zip,
  c.location_details.unit_count                               AS unit_count,
  c.location_details.building_type_code                       AS building_type_code,
  c.location_details.latitude                                 AS latitude,
  c.location_details.longitude                                AS longitude,
  ARRAY(
    SELECT AS STRUCT
      CAST(p.provider_id AS STRING)                           AS provider_id,
      LPAD(CAST(p.frn AS STRING), 10, '0')                    AS frn,
      p.brand_name                                            AS brand_name,
      SAFE_CAST(p.technology AS INT64)                        AS technology,
      SAFE_CAST(p.max_advertised_download_speed AS FLOAT64)   AS down,
      SAFE_CAST(p.max_advertised_upload_speed AS FLOAT64)     AS up,
      p.business_residential_code                             AS bus_res
    FROM UNNEST(c.provider_details) AS p
    WHERE p.period = @period
    ORDER BY p.brand_name, p.technology
  )                                                           AS providers
FROM `{location_table}` AS c
WHERE (ARRAY_LENGTH(@states) = 0 OR c.location_details.state IN UNNEST(@states));
