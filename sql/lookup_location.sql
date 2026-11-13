-- Look up one Fabric location_id -> address (providers come from lookup_providers.sql)
SELECT
  CAST(f.location_id AS STRING) AS location_id,
  LPAD(CAST(f.block_geoid AS STRING), 15, '0') AS block_geoid,
  f.address_primary, f.city, f.state, LPAD(CAST(f.zip AS STRING), 5, '0') AS zip
FROM `{fabric_table}` AS f
WHERE CAST(f.location_id AS STRING) = @location_id
LIMIT 1;
