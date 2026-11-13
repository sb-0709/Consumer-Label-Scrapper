-- =============================================================================
-- STEP 2  Pick the MOST and LEAST competitive census blocks
-- -----------------------------------------------------------------------------
-- {definition} is one of terrestrial | wired | fiber_fw | fiber (whitelisted in Python).
-- Ties are broken by a seeded hash so the selection is random but reproducible.
-- "Least competitive" requires >= 1 qualifying ISP; otherwise there is no plan to look up.
-- =============================================================================
CREATE OR REPLACE TABLE `{selected_blocks_table}` AS
WITH c AS (
  SELECT
    *,
    n_isp_{definition}           AS n_isp,
    avg_isp_per_bsl_{definition} AS avg_isp,
    FARM_FINGERPRINT(CONCAT(block_geoid, @seed)) AS tiebreak
  FROM `{competition_table}`
  WHERE n_bsl >= @min_bsl
),
most AS (
  SELECT 'most_competitive' AS grp, * FROM c
  ORDER BY n_isp DESC, avg_isp DESC, tiebreak
  LIMIT @n_blocks
),
least AS (
  SELECT 'least_competitive' AS grp, * FROM c
  WHERE n_isp >= 1
  ORDER BY n_isp ASC, avg_isp ASC, tiebreak
  LIMIT @n_blocks
)
SELECT * EXCEPT (tiebreak) FROM most
UNION ALL
SELECT * EXCEPT (tiebreak) FROM least;
