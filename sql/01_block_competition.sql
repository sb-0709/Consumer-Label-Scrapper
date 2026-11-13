-- =============================================================================
-- STEP 1  Census-block competition from the FCC National Broadband Map (NBM)
-- -----------------------------------------------------------------------------
-- Output: one row per census block (block_geoid) with, for each definition,
--   n_isp_<def>              distinct ISPs offering >= @min_down/@min_up anywhere in the block
--   avg_isp_per_bsl_<def>    average # of qualifying ISPs per serviceable location (BSL)
--   share_bsl_competitive_<def>  share of BSLs with >= @competitive_threshold ISPs
--   competitive_<def>        n_isp_<def> >= @competitive_threshold
--
-- BDC technology codes: 10 copper, 40 cable, 50 fiber, 60 GSO satellite,
-- 61 NGSO (LEO) satellite, 70 unlicensed FW, 71 licensed FW, 72 licensed-by-rule FW, 0 other
--
-- Definitions:
--   terrestrial : technology NOT IN (60, 61)          (excludes LEO & GEO)
--   wired       : technology IN (10, 40, 50)
--   fiber_fw    : technology IN (50, 70, 71, 72)
--   fiber       : technology = 50                     ("more than one fiber company")
-- =============================================================================
CREATE OR REPLACE TABLE `{competition_table}` AS
WITH q AS (           -- every (location, provider, technology) that meets the speed threshold
  SELECT
    LPAD(CAST(n.block_geoid AS STRING), 15, '0') AS block_geoid,
    CAST(n.location_id AS STRING)    AS location_id,
    CAST(n.provider_id AS STRING)    AS provider_id,
    n.brand_name,
    SAFE_CAST(n.technology AS INT64) AS technology
  FROM `{nbm_table}` AS n
  WHERE SAFE_CAST(n.max_advertised_download_speed AS FLOAT64) >= @min_down
    AND SAFE_CAST(n.max_advertised_upload_speed   AS FLOAT64) >= @min_up
    AND (NOT @residential_only OR n.business_residential_code IN ('R', 'X'))
    AND (ARRAY_LENGTH(@states) = 0 OR n.state_usps IN UNNEST(@states))
    {nbm_partition_filter}
),
per_location AS (     -- ISP count at each individual location
  SELECT block_geoid, location_id,
    COUNT(DISTINCT IF(technology NOT IN (60, 61),       provider_id, NULL)) AS n_terrestrial,
    COUNT(DISTINCT IF(technology IN (10, 40, 50),       provider_id, NULL)) AS n_wired,
    COUNT(DISTINCT IF(technology IN (50, 70, 71, 72),   provider_id, NULL)) AS n_fiber_fw,
    COUNT(DISTINCT IF(technology = 50,                  provider_id, NULL)) AS n_fiber
  FROM q
  GROUP BY block_geoid, location_id
),
loc_agg AS (
  SELECT block_geoid,
    SUM(n_terrestrial) AS sum_terrestrial,
    SUM(n_wired)       AS sum_wired,
    SUM(n_fiber_fw)    AS sum_fiber_fw,
    SUM(n_fiber)       AS sum_fiber,
    COUNTIF(n_terrestrial >= @competitive_threshold) AS nloc_comp_terrestrial,
    COUNTIF(n_wired       >= @competitive_threshold) AS nloc_comp_wired,
    COUNTIF(n_fiber_fw    >= @competitive_threshold) AS nloc_comp_fiber_fw,
    COUNTIF(n_fiber       >= @competitive_threshold) AS nloc_comp_fiber
  FROM per_location
  GROUP BY block_geoid
),
per_block AS (        -- distinct ISPs anywhere in the block
  SELECT block_geoid,
    COUNT(DISTINCT IF(technology NOT IN (60, 61),     provider_id, NULL)) AS n_isp_terrestrial,
    COUNT(DISTINCT IF(technology IN (10, 40, 50),     provider_id, NULL)) AS n_isp_wired,
    COUNT(DISTINCT IF(technology IN (50, 70, 71, 72), provider_id, NULL)) AS n_isp_fiber_fw,
    COUNT(DISTINCT IF(technology = 50,                provider_id, NULL)) AS n_isp_fiber,
    STRING_AGG(DISTINCT IF(technology NOT IN (60, 61),
               CONCAT(brand_name, ' (', CAST(technology AS STRING), ')'), NULL), '; ') AS providers_terrestrial
  FROM q
  GROUP BY block_geoid
),
bsl AS (              -- denominator: broadband serviceable locations from the Fabric
  SELECT LPAD(CAST(block_geoid AS STRING), 15, '0') AS block_geoid,
         ANY_VALUE(state) AS state,
         COUNT(*)         AS n_bsl
  FROM `{fabric_table}`
  WHERE LOWER(CAST(bsl_flag AS STRING)) IN ('true', '1')
    AND (ARRAY_LENGTH(@states) = 0 OR state IN UNNEST(@states))
  GROUP BY 1
)
SELECT
  b.block_geoid,
  b.state,
  b.n_bsl,
  IFNULL(p.n_isp_terrestrial, 0) AS n_isp_terrestrial,
  IFNULL(p.n_isp_wired, 0)       AS n_isp_wired,
  IFNULL(p.n_isp_fiber_fw, 0)    AS n_isp_fiber_fw,
  IFNULL(p.n_isp_fiber, 0)       AS n_isp_fiber,
  ROUND(SAFE_DIVIDE(IFNULL(l.sum_terrestrial, 0), b.n_bsl), 3) AS avg_isp_per_bsl_terrestrial,
  ROUND(SAFE_DIVIDE(IFNULL(l.sum_wired, 0),       b.n_bsl), 3) AS avg_isp_per_bsl_wired,
  ROUND(SAFE_DIVIDE(IFNULL(l.sum_fiber_fw, 0),    b.n_bsl), 3) AS avg_isp_per_bsl_fiber_fw,
  ROUND(SAFE_DIVIDE(IFNULL(l.sum_fiber, 0),       b.n_bsl), 3) AS avg_isp_per_bsl_fiber,
  ROUND(SAFE_DIVIDE(IFNULL(l.nloc_comp_terrestrial, 0), b.n_bsl), 3) AS share_bsl_competitive_terrestrial,
  ROUND(SAFE_DIVIDE(IFNULL(l.nloc_comp_wired, 0),       b.n_bsl), 3) AS share_bsl_competitive_wired,
  ROUND(SAFE_DIVIDE(IFNULL(l.nloc_comp_fiber_fw, 0),    b.n_bsl), 3) AS share_bsl_competitive_fiber_fw,
  ROUND(SAFE_DIVIDE(IFNULL(l.nloc_comp_fiber, 0),       b.n_bsl), 3) AS share_bsl_competitive_fiber,
  IFNULL(p.n_isp_terrestrial, 0) >= @competitive_threshold AS competitive_terrestrial,
  IFNULL(p.n_isp_wired, 0)       >= @competitive_threshold AS competitive_wired,
  IFNULL(p.n_isp_fiber_fw, 0)    >= @competitive_threshold AS competitive_fiber_fw,
  IFNULL(p.n_isp_fiber, 0)       >= @competitive_threshold AS competitive_fiber,
  p.providers_terrestrial
FROM bsl AS b
LEFT JOIN per_block AS p USING (block_geoid)
LEFT JOIN loc_agg   AS l USING (block_geoid);
