import asyncio, math, logging
from datetime import datetime, timezone
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

    return {"phase": phase, "illumination": illumination, "rises": rises, "sets": sets}, best_from


async def _fetch_clouds(session: aiohttp.ClientSession, lat: str, lon: str) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=cloud_cover&forecast_days=1&timezone=UTC"
    )
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        return await r.json(content_type=None)


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
    date_str = datetime.now(local_tz).strftime("%-d %b %Y")

    moon, best_from = _compute_moon(lat, lon, tz_str)
    sun = _compute_sun(lat, lon, tz_str)

    async with aiohttp.ClientSession() as session:
        cloud_raw = await _fetch_clouds(session, lat, lon)

    clouds = {"now": 0, "next6h_avg": 0}
    if isinstance(cloud_raw, dict) and "hourly" in cloud_raw:
        hourly = cloud_raw["hourly"]
        times  = hourly.get("time", [])
        covers = hourly.get("cloud_cover", [])
        now_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:")
        idx = next((i for i, t in enumerate(times) if t.startswith(now_prefix)), 0)
        clouds["now"]        = covers[idx] if idx < len(covers) else 0
        next6 = covers[idx: idx + 6]
        clouds["next6h_avg"] = round(sum(next6) / len(next6)) if next6 else clouds["now"]
        clouds["hourly"]     = covers[idx: idx + 8]
    else:
        log.warning("Cloud fetch failed: %s", cloud_raw)

    planets = _get_planets(lat, lon)
    viewing = _compute_verdict(bortle_int, moon["illumination"], clouds["now"])
    viewing["date"] = date_str
    if best_from:
        viewing["best_from"] = best_from

    return {
        "sky": {
            "bortle":          bortle_int,
            "bortle_label":    bortle_info["label"],
            "nelm":            bortle_info["nelm"],
            "stars":           bortle_info["stars"],
            "stars_formatted": _format_stars(bortle_info["stars"]),
        },
        "sun":     sun,
        "moon":    moon,
        "clouds":  clouds,
        "planets": planets,
        "viewing": viewing,
    }
