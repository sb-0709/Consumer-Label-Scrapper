-- =============================================================================
-- STEP 3  Randomly sample locations in the selected blocks and attach addresses
-- -----------------------------------------------------------------------------
-- The NBM has location_id but no address -> join to the Fabric (Dec 2025 = v8).
-- Also returns every NBM provider at each sampled location (providers_json), so we
-- know which ISP websites to visit and which FRN each label *should* carry.
-- =============================================================================
WITH sb AS (
  SELECT grp, block_geoid, n_isp FROM `{selected_blocks_table}`
),
fab AS (
  SELECT
    sb.grp,
    sb.n_isp AS block_n_isp,
    CAST(f.location_id AS STRING) AS location_id,
    sb.block_geoid,
    f.address_primary, f.city, f.state, LPAD(CAST(f.zip AS STRING), 5, '0') AS zip,
    f.latitude, f.longitude, f.building_type_code, f.unit_count,
    ROW_NUMBER() OVER (
      PARTITION BY sb.block_geoid
      ORDER BY FARM_FINGERPRINT(CONCAT(CAST(f.location_id AS STRING), @seed))
    ) AS rn_block
  FROM `{fabric_table}` AS f
  JOIN sb ON LPAD(CAST(f.block_geoid AS STRING), 15, '0') = sb.block_geoid
  WHERE LOWER(CAST(f.bsl_flag AS STRING)) IN ('true', '1')
    AND (NOT @residential_only OR f.building_type_code IN ('R', 'X'))
    AND f.address_primary IS NOT NULL
),
picked AS (
  SELECT * EXCEPT (rn_block),
    ROW_NUMBER() OVER (
      PARTITION BY grp
      ORDER BY FARM_FINGERPRINT(CONCAT(location_id, @seed, 'grp'))
    ) AS rn_grp
  FROM fab
  WHERE rn_block <= @per_block
),
final AS (
  SELECT * EXCEPT (rn_grp) FROM picked WHERE rn_grp <= @max_per_group
),
prov AS (
  SELECT
    CAST(n.location_id AS STRING) AS location_id,
    TO_JSON_STRING(ARRAY_AGG(STRUCT(
      CAST(n.provider_id AS STRING)               AS provider_id,
      LPAD(CAST(n.frn AS STRING), 10, '0')        AS frn,
      n.brand_name                                AS brand_name,
      SAFE_CAST(n.technology AS INT64)            AS technology,
      SAFE_CAST(n.max_advertised_download_speed AS FLOAT64) AS down,
      SAFE_CAST(n.max_advertised_upload_speed   AS FLOAT64) AS up
    ) ORDER BY n.brand_name, n.technology)) AS providers_json
  FROM `{nbm_table}` AS n
  WHERE CAST(n.location_id AS STRING) IN (SELECT location_id FROM final)
    AND (NOT @residential_only OR n.business_residential_code IN ('R', 'X'))
    {nbm_partition_filter}
  GROUP BY 1
)
SELECT f.*, p.providers_json
FROM final AS f
LEFT JOIN prov AS p USING (location_id)
ORDER BY grp, block_geoid, location_id;
