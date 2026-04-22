# Night Sky Plugin — Guide

Tonight's stargazing conditions on your TRMNL display: sky chart, light pollution, moon phase, visible planets, and an hourly weather forecast for astronomers.

---

## Setup

1. Install the plugin from the TRMNL marketplace.
2. Set your **Location** in the plugin settings (city name or address).
3. The display refreshes once per day by default — adjust **Refresh Interval** if needed.

---

## Settings

| Setting | Description |
|---------|-------------|
| **Location** | City or address to generate the sky report for. Examples: `London, UK` · `Sydney, Australia` · `Central Park, New York` |
| **Hide Info Panel** | When enabled, shows only the star chart with no overlay. Ideal for a clean sky view. |

---

## Views

| View | Layout | Content |
|------|--------|---------|
| **Full** | Fullscreen | Sky chart + full info overlay: verdict, moon, hourly cloud/seeing/transparency bars, conditions, 4-day moon forecast, planets |
| **Half horizontal** | Wide half | Sky chart + compact overlay: verdict, moon phase, Bortle, conditions, planets |
| **Half vertical** | Tall half | Sky chart + condensed overlay: verdict, moon, Bortle, cloud, temperature |
| **Quadrant** | Quarter | Sky chart + minimal overlay: verdict, moon illumination, cloud cover |

---

## Sky Chart

The chart is a panoramic projection of the sky from horizon to zenith, left to right from North → East → South → West → North. Generated in real time for your location and the current time of day.

- **Stars** are sized and brightened by magnitude — brighter stars appear larger and more opaque.
- **Constellation lines** connect the main stars of each constellation.
- **Moon** is marked with a circle and labelled.
- **Planets** are marked with a dot and their abbreviated name.

---

## Understanding the Data

### Verdict

A single-word summary of tonight's stargazing quality: **Excellent**, **Good**, **Fair**, or **Poor**. Based on cloud cover, Bortle scale, and atmospheric conditions.

### Bortle Scale

A 1–9 measure of light pollution at your location:

| Bortle | Label | NELM |
|--------|-------|------|
| 1 | Pristine dark sky | 7.6–8.0 |
| 2 | Truly dark sky | 7.1–7.5 |
| 3 | Rural sky | 6.6–7.0 |
| 4 | Rural/suburban | 6.1–6.5 |
| 5 | Suburban sky | 5.6–6.0 |
| 6 | Bright suburban | 5.1–5.5 |
| 7 | Suburban/urban | 4.6–5.0 |
| 8 | City sky | 4.1–4.5 |
| 9 | Inner city | < 4.0 |

### NELM — Naked Eye Limiting Magnitude

The faintest star you can see with the naked eye under current conditions. Higher is better: NELM 6.5 means you can see stars of magnitude 6.5 or fainter. The full moon or strong light pollution can drop this to 4.0 or below.

### Seeing (Antoniadi Scale 1–8)

Atmospheric stability — how steady the air is. Poor seeing causes stars to twinkle and planets to blur. Matters most for telescopic observation of planets and double stars.

| Score | Description |
|-------|-------------|
| 1–2 | Very poor — strong turbulence |
| 3–4 | Poor to average |
| 5–6 | Good — some undulations |
| 7–8 | Excellent — near perfect |

### Transparency (1–8)

Atmospheric clarity — how well light passes through the air. Affected by humidity, aerosols, and high cloud. Low transparency washes out faint nebulae and galaxies even in a dark sky.

### Hourly Bars

The three bar charts in the full view cover the upcoming night hours:

- **☁ Cloud** — total cloud cover percentage. Taller = more cloud.
- **Seeing** — Antoniadi score. Taller = better seeing.
- **Transp** — Transparency score. Taller = better transparency.

Bar shading: dark = best, grey = moderate, light = poor.

### Planets

Each visible planet (above the horizon) is listed with:
- **Direction** (N / NE / E / SE / S / SW / W / NW)
- **Altitude** in degrees above the horizon
- **Magnitude** — brightness (lower/negative = brighter)
- **Constellation** it currently occupies

---

## Data Sources

| Source | Used for |
|--------|----------|
| [USNO Astronomical API](https://aa.usno.navy.mil/data/api) | Sun/moon rise-set times, moon phase, planet positions |
| [Open-Meteo](https://open-meteo.com) | Cloud cover, seeing, transparency, temperature, wind, humidity |
| [lightpollutionmap.info](https://www.lightpollutionmap.info) | Bortle scale and NELM (VIIRS 2024 data) |
| [Hipparcos Catalog](https://www.cosmos.esa.int/web/hipparcos) | 118,000 stars for the sky chart |

---

## Tips

- **Best viewing window** is shown as ★ HH:MM in the full view overlay — this is the hour with the lowest combined cloud + seeing score.
- Bortle 1–4 sites are worth travelling to for serious deep-sky work. Bortle 7+ means only the Moon, planets, and the brightest clusters are rewarding.
- High transparency + good seeing + new moon + Bortle ≤ 4 = a great night.
