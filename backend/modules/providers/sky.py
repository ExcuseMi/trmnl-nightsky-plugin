import asyncio, math, logging, base64
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import aiohttp
import ephem
from ephem import AlwaysUpError, NeverUpError, CircumpolarError

log = logging.getLogger(__name__)

BORTLE_MAP = {
    "1": {"nelm": 7.8, "stars": 45000, "label": "Exceptional"},
    "2": {"nelm": 7.3, "stars": 15000, "label": "Truly dark"},
    "3": {"nelm": 6.8, "stars": 8000,  "label": "Rural"},
    "4": {"nelm": 6.3, "stars": 3200,  "label": "Rural/suburban"},
    "5": {"nelm": 5.8, "stars": 1500,  "label": "Suburban"},
    "6": {"nelm": 5.3, "stars": 600,   "label": "Bright suburban"},
    "7": {"nelm": 4.8, "stars": 300,   "label": "Suburban/urban"},
    "8": {"nelm": 4.3, "stars": 150,   "label": "City"},
    "9": {"nelm": 3.5, "stars": 50,    "label": "Inner city"},
}

_PLANET_CLASSES = [
    ("Mercury", ephem.Mercury),
    ("Venus",   ephem.Venus),
    ("Mars",    ephem.Mars),
    ("Jupiter", ephem.Jupiter),
    ("Saturn",  ephem.Saturn),
    ("Uranus",  ephem.Uranus),
    ("Neptune", ephem.Neptune),
]


def _az_to_dir(az_rad: float) -> str:
    az_deg = math.degrees(az_rad) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(az_deg / 45) % 8]


def _wind_dir(deg: float) -> str:
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(deg / 45) % 8]


def _get_planets(lat: str, lon: str) -> list[dict]:
    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.date = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")

    visible = []
    for name, PlanetClass in _PLANET_CLASSES:
        body = PlanetClass()
        body.compute(obs)
        alt_deg = math.degrees(float(body.alt))
        if alt_deg > 5:
            try:
                constellation = ephem.constellation(body)[1]
            except Exception:
                constellation = ""
            visible.append({
                "name":          name,
                "dir":           _az_to_dir(float(body.az)),
                "az":            round(math.degrees(float(body.az))),
                "alt":           round(alt_deg),
                "mag":           round(float(body.mag), 1),
                "size":          round(float(body.size), 1),
                "constellation": constellation,
            })
    return sorted(visible, key=lambda x: -x["alt"])


async def geocode(address: str) -> tuple[str | None, str | None]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "trmnl-nightsky-plugin/1.0"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
            results = await r.json(content_type=None)
            if results:
                return results[0]["lat"], results[0]["lon"]
            return None, None


def _compute_sun(lat: str, lon: str, tz_str: str) -> dict:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.date = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    sun = ephem.Sun(obs)

    def _to_local(ephem_date) -> str | None:
        dt = ephem.Date(ephem_date).datetime().replace(tzinfo=timezone.utc).astimezone(tz)
        return dt.strftime("%H:%M")

    is_up = float(sun.alt) > 0
    rises = sets = None
    try:
        sets = _to_local(obs.next_setting(sun) if is_up else obs.previous_setting(sun))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass
    try:
        rises = _to_local(obs.next_rising(sun))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass

    return {"rises": rises, "sets": sets}


def _moon_day(lat: str, lon: str, tz_str: str, offset_days: int) -> dict:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    target_utc = datetime.now(timezone.utc) + timedelta(days=offset_days)
    target_local = target_utc.astimezone(tz)

    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.pressure = 0
    obs.date = target_utc.strftime("%Y/%m/%d 20:00:00")  # evening reference

    moon = ephem.Moon(obs)
    sun  = ephem.Sun(obs)

    illumination = round(moon.phase)
    ecl_moon = ephem.Ecliptic(moon)
    ecl_sun  = ephem.Ecliptic(sun)
    elong_deg = math.degrees((ecl_moon.lon - ecl_sun.lon) % (2 * math.pi))

    if   elong_deg <  22.5: phase = "New Moon"
    elif elong_deg <  67.5: phase = "Waxing Crescent"
    elif elong_deg < 112.5: phase = "First Quarter"
    elif elong_deg < 157.5: phase = "Waxing Gibbous"
    elif elong_deg < 202.5: phase = "Full Moon"
    elif elong_deg < 247.5: phase = "Waning Gibbous"
    elif elong_deg < 292.5: phase = "Last Quarter"
    elif elong_deg < 337.5: phase = "Waning Crescent"
    else:                   phase = "New Moon"

    if offset_days == 0:
        label = "Tonight"
    elif offset_days == 1:
        label = "Tomorrow"
    else:
        label = target_local.strftime("%a")

    return {
        "label":       label,
        "date":        target_local.strftime("%-d %b"),
        "phase":       phase,
        "illumination": illumination,
        "waxing":      elong_deg < 180,
    }


def _compute_moon(lat: str, lon: str, tz_str: str) -> tuple[dict, str | None]:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.pressure = 0
    obs.date = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")

    moon = ephem.Moon(obs)
    sun  = ephem.Sun(obs)

    illumination = round(moon.phase)

    ecl_moon = ephem.Ecliptic(moon)
    ecl_sun  = ephem.Ecliptic(sun)
    elong_deg = math.degrees((ecl_moon.lon - ecl_sun.lon) % (2 * math.pi))
    if   elong_deg <  22.5: phase = "New Moon"
    elif elong_deg <  67.5: phase = "Waxing Crescent"
    elif elong_deg < 112.5: phase = "First Quarter"
    elif elong_deg < 157.5: phase = "Waxing Gibbous"
    elif elong_deg < 202.5: phase = "Full Moon"
    elif elong_deg < 247.5: phase = "Waning Gibbous"
    elif elong_deg < 292.5: phase = "Last Quarter"
    elif elong_deg < 337.5: phase = "Waning Crescent"
    else:                   phase = "New Moon"

    def _to_local(ephem_date) -> str | None:
        if ephem_date is None:
            return None
        dt = ephem.Date(ephem_date).datetime().replace(tzinfo=timezone.utc).astimezone(tz)
        return dt.strftime("%H:%M")

    rises = sets = None
    try:
        rises = _to_local(obs.next_rising(moon))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass
    try:
        sets = _to_local(obs.next_setting(moon))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass

    best_from = None
    try:
        obs_twi = ephem.Observer()
        obs_twi.lat = lat
        obs_twi.lon = lon
        obs_twi.elevation = 0
        obs_twi.pressure = 0
        obs_twi.horizon = '-18'
        obs_twi.date = obs.date
        best_from = _to_local(obs_twi.next_setting(sun, use_center=True))
    except Exception:
        pass

    if illumination > 50 and sets:
        best_from = sets

    return {
        "phase":        phase,
        "illumination": illumination,
        "rises":        rises,
        "sets":         sets,
        "alt":          round(math.degrees(float(moon.alt))),
        "az":           round(math.degrees(float(moon.az))),
    }, best_from


async def _fetch_weather(session: aiohttp.ClientSession, lat: str, lon: str) -> dict:
    fields = (
        "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
        "temperature_2m,dewpoint_2m,relative_humidity_2m,"
        "wind_speed_10m,wind_direction_10m"
    )
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly={fields}&forecast_days=2&timezone=UTC&wind_speed_unit=kmh"
    )
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        return await r.json(content_type=None)


async def _fetch_seeing(session: aiohttp.ClientSession, lat: str, lon: str) -> dict:
    url = (
        f"http://www.7timer.info/bin/astro.php"
        f"?lon={lon}&lat={lat}&ac=0&lang=en&unit=metric&output=json&tzshift=0"
    )
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
        return await r.json(content_type=None)


def _build_forecast(weather_raw: dict, seeing_raw: dict, now_utc: datetime, tz) -> dict:
    # Parse 7timer seeing/transparency — keyed by UTC hour offset from init
    seeing_series = []
    init_dt = None
    if isinstance(seeing_raw, dict) and "dataseries" in seeing_raw:
        try:
            init_dt = datetime.strptime(seeing_raw["init"], "%Y%m%d%H").replace(tzinfo=timezone.utc)
            seeing_series = seeing_raw["dataseries"]
        except Exception:
            pass

    def _seeing_at(utc_dt: datetime) -> tuple[int, int]:
        if not init_dt or not seeing_series:
            return 0, 0
        offset_h = (utc_dt - init_dt).total_seconds() / 3600
        nearest = min(seeing_series, key=lambda e: abs(e["timepoint"] - offset_h))
        return nearest.get("seeing", 0), nearest.get("transparency", 0)

    now_cond = {}
    hourly = []

    if not (isinstance(weather_raw, dict) and "hourly" in weather_raw):
        return {"now": now_cond, "hourly": hourly}

    h = weather_raw["hourly"]
    times   = h.get("time", [])
    clouds  = h.get("cloud_cover", [])
    cl_low  = h.get("cloud_cover_low", [])
    cl_mid  = h.get("cloud_cover_mid", [])
    cl_high = h.get("cloud_cover_high", [])
    temps   = h.get("temperature_2m", [])
    dews    = h.get("dewpoint_2m", [])
    rhs     = h.get("relative_humidity_2m", [])
    winds   = h.get("wind_speed_10m", [])
    wdirs   = h.get("wind_direction_10m", [])

    now_prefix = now_utc.strftime("%Y-%m-%dT%H:")
    idx = next((i for i, t in enumerate(times) if t.startswith(now_prefix)), 0)

    def _v(arr, i, default=0):
        return arr[i] if i < len(arr) else default

    now_cond = {
        "cloud":      _v(clouds, idx),
        "cloud_low":  _v(cl_low, idx),
        "cloud_mid":  _v(cl_mid, idx),
        "cloud_high": _v(cl_high, idx),
        "temp":       round(_v(temps, idx, 0)),
        "dew_point":  round(_v(dews, idx, 0)),
        "humidity":   round(_v(rhs, idx, 0)),
        "wind_speed": round(_v(winds, idx, 0)),
        "wind_dir":   _wind_dir(_v(wdirs, idx, 0)),
    }

    next6 = [_v(clouds, idx + j) for j in range(6) if idx + j < len(clouds)]
    now_cond["next6h_avg"] = round(sum(next6) / len(next6)) if next6 else now_cond["cloud"]

    for offset in range(8):
        i = idx + offset
        if i >= len(times):
            break
        t_utc = datetime.fromisoformat(times[i]).replace(tzinfo=timezone.utc)
        t_local = t_utc.astimezone(tz)
        seeing, transp = _seeing_at(t_utc)
        hourly.append({
            "hour":        t_local.strftime("%H"),
            "cloud":       _v(clouds, i),
            "cloud_low":   _v(cl_low, i),
            "cloud_mid":   _v(cl_mid, i),
            "cloud_high":  _v(cl_high, i),
            "seeing":      seeing,
            "transparency": transp,
        })

    return {"now": now_cond, "hourly": hourly}


def _compute_verdict(bortle: int, illumination: int, cloud_now: int) -> dict:
    score = 10
    if cloud_now > 80:   score -= 5
    elif cloud_now > 60: score -= 3
    elif cloud_now > 30: score -= 2
    elif cloud_now > 15: score -= 1
    if illumination > 85:   score -= 3
    elif illumination > 60: score -= 2
    elif illumination > 30: score -= 1
    if bortle >= 8:   score -= 2
    elif bortle >= 6: score -= 1
    if bortle <= 2:   score += 1
    score = max(0, min(10, score))

    if score >= 8:   verdict, stars = "Excellent", "★★★★"
    elif score >= 6: verdict, stars = "Good",      "★★★"
    elif score >= 4: verdict, stars = "Fair",       "★★"
    else:            verdict, stars = "Poor",       "★"

    return {"verdict": verdict, "stars_text": stars, "score": score}


# (RA decimal hours, Dec degrees, visual magnitude)
_BRIGHT_STARS = [
    (6.753, -16.716, -1.46), (6.400, -52.696, -0.74), (14.261,  19.182, -0.05),
    (18.615,  38.784,  0.03), (5.278,  45.998,  0.08), ( 5.242,  -8.202,  0.12),
    ( 7.655,   5.225,  0.38), (1.628, -57.237,  0.46), ( 5.919,   7.407,  0.50),
    (14.063, -60.373,  0.61), (12.443, -63.099,  0.76), (19.846,   8.868,  0.77),
    ( 4.598,  16.509,  0.85), (16.490, -26.432,  0.96), (13.420, -11.161,  0.98),
    ( 7.755,  28.026,  1.14), (22.961, -29.622,  1.16), (12.795, -59.688,  1.25),
    (20.690,  45.280,  1.25), (10.140,  11.967,  1.35), ( 6.977, -28.972,  1.50),
    ( 7.577,  31.889,  1.58), (12.519, -57.113,  1.63), (17.560, -37.104,  1.63),
    ( 5.419,   6.350,  1.64), ( 5.438,  28.608,  1.65), ( 9.220, -69.717,  1.68),
    ( 5.604,  -1.202,  1.70), (22.137, -46.961,  1.74), ( 5.679,  -1.943,  1.74),
    (12.901,  55.960,  1.77), (11.062,  61.751,  1.79), ( 3.405,  49.861,  1.79),
    ( 8.160, -47.337,  1.83), ( 7.139, -26.393,  1.84), (18.403, -34.384,  1.85),
    (17.622, -43.000,  1.86), ( 8.375, -59.510,  1.86), (13.792,  49.313,  1.86),
    ( 5.992,  44.948,  1.90), (16.811, -69.028,  1.92), ( 6.629,  16.399,  1.93),
    (20.428, -56.735,  1.94), ( 6.378, -17.956,  1.98), ( 9.460,  -8.659,  1.99),
    ( 2.530,  89.264,  1.97), ( 2.120,  23.462,  2.00), (10.333,  19.845,  2.01),
    ( 0.727, -17.987,  2.04), (18.921, -26.297,  2.05), ( 0.140,  29.090,  2.07),
    ( 1.162,  35.620,  2.07), ( 2.065,  42.330,  2.10), ( 5.796,  -9.670,  2.07),
    (17.582,  12.560,  2.08), (14.845,  74.156,  2.08), ( 3.136,  40.957,  2.09),
    (11.818,  14.572,  2.14), ( 0.945,  60.717,  2.15), (12.692, -48.960,  2.20),
    ( 8.060, -40.003,  2.21), ( 9.285, -59.275,  2.21), ( 9.133, -43.433,  2.23),
    (15.578,  26.715,  2.23), ( 5.534,  -0.299,  2.23), ( 0.675,  56.537,  2.23),
    ( 0.153,  59.150,  2.27), (13.399,  54.925,  2.27), (17.943,  51.490,  2.23),
    (16.836, -34.293,  2.29), (15.999, -22.622,  2.32), (14.749,  27.074,  2.35),
    (11.031,  56.383,  2.37), ( 0.436, -42.306,  2.40), (17.172, -15.724,  2.43),
    (11.897,  53.695,  2.44), (23.063,  28.083,  2.44), ( 7.401, -29.303,  2.45),
    (21.310,  62.585,  2.45), (20.770,  33.970,  2.46), (23.079,  15.205,  2.49),
    (11.235,  20.524,  2.56), ( 5.545, -17.822,  2.58), (12.264, -17.541,  2.59),
    (15.283,  -9.383,  2.61), (15.737,   6.426,  2.63), ( 1.911,  20.808,  2.64),
    ( 5.662, -34.074,  2.65), (12.573, -23.397,  2.65), ( 1.430,  60.235,  2.68),
    ( 4.950,  33.166,  2.69), (14.844, -16.042,  2.75), ( 5.138,  -5.087,  2.79),
    ( 0.221,  15.184,  2.83), ( 5.471, -20.759,  2.84), ( 3.906,  31.884,  2.85),
    (21.526,  -5.571,  2.87), (22.711, -46.885,  2.87), (22.097,  -0.320,  2.96),
    (19.512,  27.960,  3.09), (16.619, -10.567,  3.02), (17.937, -37.103,  3.17),
    ( 6.332,  22.514,  3.18), (10.827, -16.194,  2.99), (15.258,  33.314,  3.16),
    (16.688,  31.603,  3.15), ( 5.908,  37.213,  3.03), ( 6.247,  22.507,  3.35),
    ( 4.300,  15.628,  3.54), (13.911,  18.397,  3.49), ( 8.745, -54.708,  1.96),
    (21.736, -16.132,  3.77), (22.027, -16.662,  3.77), ( 4.597,  16.509,  0.85),
]

_PLANET_ABBR = {
    "Mercury": "Mer", "Venus": "Ven", "Mars": "Mar",
    "Jupiter": "Jup", "Saturn": "Sat", "Uranus": "Ura", "Neptune": "Nep",
}


def _moon_crescent_svg(illum: int, is_waxing: bool, r: float, cx: float, cy: float) -> str:
    cx_s, cy_s = f"{cx:.1f}", f"{cy:.1f}"
    if illum <= 2:
        return f'<circle cx="{cx_s}" cy="{cy_s}" r="{r}" fill="#111" stroke="#888" stroke-width="0.5"/>'
    if illum >= 98:
        return f'<circle cx="{cx_s}" cy="{cy_s}" r="{r}" fill="white" stroke="#888" stroke-width="0.5"/>'
    frac   = illum / 100
    ex     = round(abs(1 - 2 * frac) * r, 1)
    bs     = 1 if is_waxing else 0
    ts     = bs if frac < 0.5 else (1 - bs)
    tx, ty = f"{cx:.1f}", f"{cy - r:.1f}"
    bx, by = f"{cx:.1f}", f"{cy + r:.1f}"
    d = f"M {tx},{ty} A {r} {r} 0 0 {bs} {bx},{by} A {ex} {r} 0 0 {ts} {tx},{ty} Z"
    return (f'<circle cx="{cx_s}" cy="{cy_s}" r="{r}" fill="#111" stroke="#888" stroke-width="0.5"/>'
            f'<path d="{d}" fill="white"/>')


def _generate_sky_chart(lat: str, lon: str, moon_data: dict, planets: list) -> str:
    SIZE, R, CX, CY = 200, 86, 100, 100

    obs = ephem.Observer()
    obs.lat      = lat
    obs.lon      = lon
    obs.elevation = 0
    obs.pressure  = 0
    obs.date = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")

    def _xy(alt_deg: float, az_deg: float) -> tuple[float, float]:
        rr     = R * (1 - alt_deg / 90)
        az_rad = math.radians(az_deg)
        return CX + rr * math.sin(az_rad), CY - rr * math.cos(az_rad)

    p: list[str] = []

    # Background
    p.append(f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="black"/>')
    # Altitude rings at 30° and 60°
    for alt in (30, 60):
        rr = R * (90 - alt) / 90
        p.append(f'<circle cx="{CX}" cy="{CY}" r="{rr:.1f}" fill="none" stroke="#444" stroke-width="0.5" stroke-dasharray="2,2"/>')
    # Crosshairs
    p.append(f'<line x1="{CX}" y1="{CY-R}" x2="{CX}" y2="{CY+R}" stroke="#333" stroke-width="0.5"/>')
    p.append(f'<line x1="{CX-R}" y1="{CY}" x2="{CX+R}" y2="{CY}" stroke="#333" stroke-width="0.5"/>')
    # Cardinal labels
    p.append(f'<text x="{CX}" y="{CY-R-4}" text-anchor="middle" font-size="10" font-family="sans-serif" fill="white" font-weight="bold">N</text>')
    p.append(f'<text x="{CX+R+5}" y="{CY+4}" text-anchor="start" font-size="10" font-family="sans-serif" fill="white" font-weight="bold">E</text>')
    p.append(f'<text x="{CX}" y="{CY+R+13}" text-anchor="middle" font-size="10" font-family="sans-serif" fill="white" font-weight="bold">S</text>')
    p.append(f'<text x="{CX-R-5}" y="{CY+4}" text-anchor="end" font-size="10" font-family="sans-serif" fill="white" font-weight="bold">W</text>')

    # Stars
    star_body = ephem.FixedBody()
    star_body._epoch = ephem.J2000
    for ra_h, dec_d, mag in _BRIGHT_STARS:
        star_body._ra  = ra_h / 12 * math.pi
        star_body._dec = math.radians(dec_d)
        star_body.compute(obs)
        alt_deg = math.degrees(float(star_body.alt))
        if alt_deg < 0:
            continue
        az_deg = math.degrees(float(star_body.az))
        sx, sy = _xy(alt_deg, az_deg)
        sr = 3.0 if mag < 0 else 2.5 if mag < 1 else 2.0 if mag < 2 else 1.5 if mag < 3 else 1.0
        p.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{sr}" fill="white"/>')

    # Moon
    moon_alt = moon_data.get("alt", -1)
    moon_az  = moon_data.get("az", 0)
    if moon_alt > 0:
        mx, my = _xy(moon_alt, moon_az)
        illum    = moon_data.get("illumination", 50)
        is_waxing = "Waxing" in moon_data.get("phase", "") or moon_data.get("phase") in ("New Moon", "First Quarter")
        p.append(_moon_crescent_svg(illum, is_waxing, 7, mx, my))
        p.append(f'<text x="{mx:.1f}" y="{my - 10:.1f}" text-anchor="middle" font-size="7" font-family="sans-serif" fill="white">Moon</text>')

    # Planets (on top of stars)
    for pl in planets:
        px, py = _xy(pl["alt"], pl["az"])
        abbr = _PLANET_ABBR.get(pl["name"], pl["name"][:3])
        p.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="white" stroke="black" stroke-width="0.5"/>')
        p.append(f'<text x="{px:.1f}" y="{py - 7:.1f}" text-anchor="middle" font-size="7" font-family="sans-serif" fill="white">{abbr}</text>')

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}">'
           + "".join(p) + "</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _format_stars(n: int) -> str:
    return f"{n // 1000}k+" if n >= 1000 else str(n)


async def build_sky_data(lat: str, lon: str, bortle_str: str, tz_str: str) -> dict:
    bortle_str = bortle_str if bortle_str in BORTLE_MAP else "5"
    bortle_info = BORTLE_MAP[bortle_str]
    bortle_int = int(bortle_str)

    try:
        local_tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        local_tz = timezone.utc

    now_utc  = datetime.now(timezone.utc)
    date_str = now_utc.astimezone(local_tz).strftime("%-d %b %Y")

    moon, best_from = _compute_moon(lat, lon, tz_str)
    moon["days"] = [_moon_day(lat, lon, tz_str, i) for i in range(4)]
    sun = _compute_sun(lat, lon, tz_str)

    async with aiohttp.ClientSession() as session:
        weather_raw, seeing_raw = await asyncio.gather(
            _fetch_weather(session, lat, lon),
            _fetch_seeing(session, lat, lon),
            return_exceptions=True,
        )

    if isinstance(weather_raw, Exception):
        log.warning("Weather fetch failed: %s", weather_raw)
        weather_raw = {}
    if isinstance(seeing_raw, Exception):
        log.warning("Seeing fetch failed: %s", seeing_raw)
        seeing_raw = {}

    forecast = _build_forecast(weather_raw, seeing_raw, now_utc, local_tz)

    planets = _get_planets(lat, lon)
    viewing = _compute_verdict(bortle_int, moon["illumination"], forecast["now"].get("cloud", 0))
    viewing["date"] = date_str
    if best_from:
        viewing["best_from"] = best_from

    chart = _generate_sky_chart(lat, lon, moon, planets)

    return {
        "sky": {
            "bortle":          bortle_int,
            "bortle_label":    bortle_info["label"],
            "nelm":            bortle_info["nelm"],
            "stars":           bortle_info["stars"],
            "stars_formatted": _format_stars(bortle_info["stars"]),
            "chart":           chart,
        },
        "sun":      sun,
        "moon":     moon,
        "forecast": forecast,
        "planets":  planets,
        "viewing":  viewing,
    }
