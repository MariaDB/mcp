-- Geography Database Views
-- For querying cities by country, region, and subregion
-- Compatible with both 'geography' and 'demo' databases

-- =============================================================================
-- v_cities_full: Complete city information with full geographic hierarchy
-- =============================================================================
-- Use: SELECT * FROM v_cities_full WHERE country_code = 'US' LIMIT 10;
CREATE OR REPLACE VIEW v_cities_full AS
SELECT
    ci.id AS city_id,
    ci.name AS city_name,
    ci.population AS city_population,
    ci.latitude AS city_lat,
    ci.longitude AS city_lng,
    ci.timezone,
    s.id AS state_id,
    s.name AS state_name,
    s.iso2 AS state_code,
    co.id AS country_id,
    co.name AS country_name,
    co.iso2 AS country_code,
    co.iso3 AS country_iso3,
    co.capital,
    co.currency,
    sr.id AS subregion_id,
    sr.name AS subregion_name,
    r.id AS region_id,
    r.name AS region_name
FROM cities ci
JOIN states s ON ci.state_id = s.id
JOIN countries co ON ci.country_id = co.id
LEFT JOIN subregions sr ON co.subregion_id = sr.id
LEFT JOIN regions r ON co.region_id = r.id;

-- =============================================================================
-- v_cities_by_country: Simple view for querying cities by country
-- =============================================================================
-- Use: SELECT * FROM v_cities_by_country WHERE country_code = 'CH';
-- Use: SELECT * FROM v_cities_by_country WHERE country = 'Switzerland';
CREATE OR REPLACE VIEW v_cities_by_country AS
SELECT
    ci.name AS city,
    ci.population,
    s.name AS state,
    co.name AS country,
    co.iso2 AS country_code
FROM cities ci
JOIN states s ON ci.state_id = s.id
JOIN countries co ON ci.country_id = co.id
ORDER BY co.name, ci.population DESC;

-- =============================================================================
-- v_cities_by_region: View for querying cities by region/subregion
-- =============================================================================
-- Use: SELECT * FROM v_cities_by_region WHERE region = 'Europe';
-- Use: SELECT * FROM v_cities_by_region WHERE subregion = 'Western Europe';
CREATE OR REPLACE VIEW v_cities_by_region AS
SELECT
    r.name AS region,
    sr.name AS subregion,
    co.name AS country,
    s.name AS state,
    ci.name AS city,
    ci.population
FROM cities ci
JOIN states s ON ci.state_id = s.id
JOIN countries co ON ci.country_id = co.id
LEFT JOIN subregions sr ON co.subregion_id = sr.id
LEFT JOIN regions r ON co.region_id = r.id
ORDER BY r.name, sr.name, co.name, ci.population DESC;

-- =============================================================================
-- v_country_stats: Country statistics with city counts
-- =============================================================================
-- Use: SELECT * FROM v_country_stats WHERE region = 'Africa';
-- Use: SELECT * FROM v_country_stats ORDER BY city_count DESC LIMIT 20;
CREATE OR REPLACE VIEW v_country_stats AS
SELECT
    r.name AS region,
    sr.name AS subregion,
    co.name AS country,
    co.iso2 AS country_code,
    co.capital,
    co.population AS country_population,
    COUNT(ci.id) AS city_count,
    SUM(ci.population) AS total_city_population
FROM countries co
LEFT JOIN cities ci ON ci.country_id = co.id
LEFT JOIN subregions sr ON co.subregion_id = sr.id
LEFT JOIN regions r ON co.region_id = r.id
GROUP BY r.name, sr.name, co.id, co.name, co.iso2, co.capital, co.population
ORDER BY r.name, sr.name, co.name;

-- =============================================================================
-- v_region_summary: Aggregated statistics by region and subregion
-- =============================================================================
-- Use: SELECT * FROM v_region_summary;
-- Use: SELECT * FROM v_region_summary WHERE region = 'Asia';
CREATE OR REPLACE VIEW v_region_summary AS
SELECT
    r.name AS region,
    sr.name AS subregion,
    COUNT(DISTINCT co.id) AS country_count,
    COUNT(DISTINCT s.id) AS state_count,
    COUNT(ci.id) AS city_count,
    SUM(ci.population) AS total_population
FROM regions r
LEFT JOIN subregions sr ON sr.region_id = r.id
LEFT JOIN countries co ON co.subregion_id = sr.id
LEFT JOIN states s ON s.country_id = co.id
LEFT JOIN cities ci ON ci.country_id = co.id
GROUP BY r.id, r.name, sr.id, sr.name
ORDER BY r.name, sr.name;

-- =============================================================================
-- Example Queries
-- =============================================================================
--
-- All cities in a specific country:
--   SELECT * FROM v_cities_by_country WHERE country_code = 'DE' ORDER BY population DESC LIMIT 20;
--
-- All cities in a continent:
--   SELECT country, city, population FROM v_cities_by_region WHERE region = 'Africa' ORDER BY population DESC LIMIT 20;
--
-- All cities in a subregion:
--   SELECT country, city, population FROM v_cities_by_region WHERE subregion = 'South America' ORDER BY population DESC;
--
-- Country statistics for a region:
--   SELECT country, capital, city_count FROM v_country_stats WHERE region = 'Europe' ORDER BY city_count DESC;
--
-- Full city details with coordinates:
--   SELECT city_name, state_name, country_name, city_lat, city_lng FROM v_cities_full WHERE country_code = 'JP' AND city_population > 1000000;
