# Night Sky Plugin — Guide

Tonight's stargazing conditions on your TRMNL display: a real-time star chart, light pollution rating, moon phase, visible planets, and a weather forecast for astronomers.

---

## Settings

| Setting | Description |
|---------|-------------|
| **Location** | City or address for the sky report. Examples: `London, UK` · `Sydney, Australia` · `Central Park, New York` |
| **Show Info Panel** | Show the conditions overlay (verdict, moon, weather, planets) on top of the chart. Disable for a clean chart-only view. |
| **During Daytime** | What to show when the sun is up: the chart as-is, fast-forward to earliest nightfall, or skip the plugin entirely. |
| **Constellations** | Draw constellation stick figures on the chart, optionally with names. |
| **Show Planet Names** | Label visible planets on the chart. |
| **Time Format** | 24-hour or 12-hour (AM/PM). |
| **Show Title Bar** | Show the plugin name bar at the bottom of the screen. |

---

## Views

| View | Content |
|------|---------|
| **Full** | Star chart + full overlay: verdict, best-viewing time, Bortle/NELM, moon phase & rise/set, sun times, weather (temp, dew point, wind, humidity, cloud layers), 4-day moon forecast, visible planets |
| **Half horizontal** | Star chart + compact overlay: verdict, moon phase, Bortle, sun/moon times, cloud %, temperature, planets |
| **Half vertical** | Star chart + condensed overlay: verdict, moon phase, Bortle, cloud %, temperature |
| **Quadrant** | Star chart + minimal overlay: verdict, moon illumination, cloud cover |

---

## Sky Chart

The chart is a stereographic perspective projection centred on the southern sky at 40° altitude — the same view you get when you point a camera south and tilt it up slightly. The projection preserves constellation shapes (no horizontal stretching).

- **Stars** are sized by magnitude — brighter stars appear larger.
- **Moon** is marked with a circle and labelled.
- **Planets** are marked with a dot (and name if enabled).
- **Constellation lines** connect the main stars of each figure (if enabled).

Objects north of the zenith or far east/west may be outside the frame; the chart covers roughly 100° of sky height centred at 40° altitude.

---

## Understanding the Data

### Verdict

**Excellent**, **Good**, **Fair**, or **Poor** — a summary of tonight's stargazing quality based on cloud cover, moon illumination, and Bortle scale.

### Best Viewing Window

Shown as ★ HH:MM in the full overlay. The hour after astronomical dusk when the moon is lowest (or has set) and cloud cover is expected to be lightest.

### Bortle Scale

A 1–9 measure of light pollution at your location, derived from VIIRS 2024 satellite data:

| Bortle | Label | NELM |
|--------|-------|------|
| 1 | Exceptional dark sky | 7.8 |
| 2 | Truly dark sky | 7.3 |
| 3 | Rural sky | 6.8 |
| 4 | Rural/suburban | 6.3 |
| 5 | Suburban sky | 5.8 |
| 6 | Bright suburban | 5.3 |
| 7 | Suburban/urban | 4.8 |
| 8 | City sky | 4.3 |
| 9 | Inner city | 3.5 |

### NELM — Naked Eye Limiting Magnitude

The faintest star visible with the naked eye under your sky conditions. Higher is better. A full moon or heavy light pollution can drop this below 4.0.

### Planets

Each visible planet (altitude > 5°) is listed with direction, altitude in degrees, magnitude, and the constellation it currently occupies.

---

## Data Sources

| Source | Used for |
|--------|----------|
| [Hipparcos Catalog](https://www.cosmos.esa.int/web/hipparcos) + [Skyfield](https://rhodesmill.org/skyfield/) | 118,000 stars for the sky chart |
| [PyEphem](https://rhodesmill.org/pyephem/) | Planet and moon positions, sun/moon rise–set times |
| [Stellarium modern_st](https://github.com/Stellarium/stellarium) | Constellation stick figure lines |
| [Open-Meteo](https://open-meteo.com) | Cloud cover, temperature, dew point, wind, humidity |
| [lightpollutionmap.info](https://www.lightpollutionmap.info) | Bortle scale and NELM (VIIRS 2024 satellite data) |

---

## Tips

- **Bortle 1–4** sites are worth travelling to for serious deep-sky work. Bortle 7+ means only the Moon, planets, and the brightest clusters are rewarding.
- **New moon + clear sky + Bortle ≤ 4** = an exceptional night.
- Set **During Daytime → Skip** if you only want the display active at night.
- The chart updates every 5 minutes internally; the TRMNL display refreshes every 15 minutes.
