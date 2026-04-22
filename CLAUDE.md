# trmnl-nightsky-plugin

TRMNL e-paper plugin that shows a real-time panoramic star chart with an optional
conditions overlay. The chart renders the sky as it looks right now — stars sized by
magnitude, moon, constellation lines, and visible planets. The overlay shows Bortle
scale, NELM, moon phase, cloud cover forecast, temperature, dew point, and a
best-viewing window estimate.

## Repo layout

```
backend/          Python/Quart backend
  app.py          HTTP routes: /health, /chart, /data
  modules/
    providers/
      sky.py      Star chart generation, planet/constellation/moon data
      light_pollution.py  Bortle lookup from VIIRS raster
    utils/
      ip_whitelist.py     TRMNL IP allowlist (fetched from trmnl.com/api/ips)
  requirements.txt
  Dockerfile
plugin/           TRMNL plugin (managed by trmnlp CLI)
  src/
    shared.liquid   Main template (all 4 view sizes)
    settings.yml    Plugin settings schema
    transform.js    Data passthrough (must include every key the template uses)
    .trmnlp.yml     Local dev variables / mock data
assets/           icon.svg, icon-orange.svg
docker-compose.yml  backend + Redis
.env.example
```

## Running locally

```bash
# Backend (Docker — includes Redis)
docker compose up --build

# Frontend dev server (from plugin/)
cd plugin && trmnlp serve

# Push/pull plugin to TRMNL
cd plugin && trmnlp push
```

Set `ENABLE_IP_WHITELIST=false` in `.env` for local curl/browser testing.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BACKEND_PORT` | `8080` | Host port for the backend |
| `BASE_URL` | _(derived)_ | Public URL of backend, e.g. `https://example.com/nightsky` |
| `REDIS_URL` | `redis://redis:6379` | Redis connection string |
| `ENABLE_IP_WHITELIST` | `true` | Enforce TRMNL IP allowlist on `/data`; `/chart` returns black PNG for non-TRMNL IPs |
| `IP_REFRESH_HOURS` | `24` | How often to refresh the TRMNL IP list |

## Key architecture decisions

### Coordinate system
The star chart is a linear panoramic projection: azimuth 0–360° left-to-right,
altitude 0–90° bottom-to-top. Both the matplotlib PNG (stars/moon) and the SVG
overlay (constellations, planets) use the same mapping:
```
px = az / 360 * W
py = (1 - alt / 90) * H
```

### Epoch pinning (alignment guarantee)
`/data` computes a single `snap` epoch (5-minute UTC boundary) and:
1. Passes it to `build_sky_data` → `_constellation_svg_data` for LST computation
2. Encodes it as `t=<unix_timestamp>` in the chart URL

`/chart` reads `t` from the URL and uses that exact epoch for chart LST computation.
This guarantees the star positions in the PNG and the constellation lines in the SVG
are computed for the same instant, giving pixel-perfect alignment.

### Chart caching (Redis)
Charts are cached in Redis under key `chart:<lat>|<lon>|<tz>|<w>|<h>|<t>` with a
300-second TTL. Redis is configured with `maxmemory 64mb` + `allkeys-lru` so it
self-manages memory. If Redis is unavailable, charts are generated on every request.

### Constellation data
`_CONST_POLYLINES` in `sky.py` stores RA/Dec polylines (degrees) for ~30
constellations, sourced from d3-celestial. `_radec_altaz()` converts to alt/az
using GMST → LST → hour angle. Pre-clipped segments (below-horizon and wrap-around
filtered) are returned in the `/data` payload as `data.constellations`.

### transform.js
Every key the Liquid template reads from `data.*` must be explicitly passed through
`plugin/src/transform.js`. Missing keys are silently dropped by TRMNL.

## Testing

```bash
# Backend unit test
cd backend && python test_sky_chart.py

# Check constellation output for a location
cd backend && python -c "
from modules.providers.sky import _constellation_svg_data
from datetime import datetime, timezone
epoch = datetime.now(timezone.utc)
r = _constellation_svg_data('51.5', '-0.1', 'names', epoch)
print(len(r), 'constellations')
"
```
