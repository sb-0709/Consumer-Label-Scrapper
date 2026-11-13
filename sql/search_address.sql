-- Candidate Fabric locations for an address typed in the app (ZIP + house number)
SELECT
  location_id, block_geoid, address_primary, city, state, zip,
  unit_count, building_type_code, latitude, longitude,
  TO_JSON_STRING(providers) AS providers_json
FROM `{address_index_table}`
WHERE zip = @zip
  AND (@house = '' OR STARTS_WITH(UPPER(address_primary), CONCAT(@house, ' ')))
LIMIT 300;
