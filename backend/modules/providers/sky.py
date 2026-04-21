import asyncio, math, logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import aiohttp
import ephem

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
            visible.append({
                "name": name,
                "dir": _az_to_dir(float(body.az)),
                "alt": round(alt_deg),
            })
    return sorted(visible, key=lambda x: -x["alt"])


async def _fetch_moon(session: aiohttp.ClientSession, lat: str, lon: str, tz_offset: float) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"https://aa.usno.navy.mil/api/rstt/oneday?date={today}&coords={lat},{lon}&tz={tz_offset}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        return await r.json(content_type=None)


async def _fetch_clouds(session: aiohttp.ClientSession, lat: str, lon: str) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=cloud_cover&forecast_days=1&timezone=UTC"
    )
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        return await r.json(content_type=None)


def _tz_offset(tz_str: str) -> float:
    try:
        tz = ZoneInfo(tz_str)
        return datetime.now(tz).utcoffset().total_seconds() / 3600
    except (ZoneInfoNotFoundError, Exception):
        return 0.0


def _parse_phen(items: list, code: str) -> str | None:
    for item in items:
        if item.get("phen") == code:
            t = item.get("time", "")
            return t if t and t != "**:**" else None
    return None


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
    tz_offset = _tz_offset(tz_str)

    async with aiohttp.ClientSession() as session:
        moon_raw, cloud_raw = await asyncio.gather(
            _fetch_moon(session, lat, lon, tz_offset),
            _fetch_clouds(session, lat, lon),
            return_exceptions=True,
        )

    # Moon
    moon = {"phase": "Unknown", "illumination": 0, "sets": None, "rises": None}
    if isinstance(moon_raw, dict) and not moon_raw.get("error"):
        illum = moon_raw.get("fracillum", "0%").replace("%", "").strip()
        moon["illumination"] = int(illum) if illum.isdigit() else 0
        moon["phase"] = moon_raw.get("curphase", "Unknown")
        moon["rises"] = _parse_phen(moon_raw.get("moondata", []), "R")
        moon["sets"]  = _parse_phen(moon_raw.get("moondata", []), "S")
    else:
        log.warning("Moon fetch failed: %s", moon_raw)

    # Civil twilight end → default best_from
    best_from = None
    if isinstance(moon_raw, dict):
        best_from = _parse_phen(moon_raw.get("sundata", []), "EC")
    if moon["illumination"] > 50 and moon["sets"]:
        best_from = moon["sets"]

    # Clouds
    clouds = {"now": 0, "next6h_avg": 0}
    if isinstance(cloud_raw, dict) and "hourly" in cloud_raw:
        hourly = cloud_raw["hourly"]
        times  = hourly.get("time", [])
        covers = hourly.get("cloud_cover", [])
        now_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:")
        idx = next((i for i, t in enumerate(times) if t.startswith(now_prefix)), 0)
        clouds["now"]       = covers[idx] if idx < len(covers) else 0
        next6 = covers[idx: idx + 6]
        clouds["next6h_avg"] = round(sum(next6) / len(next6)) if next6 else clouds["now"]
    else:
        log.warning("Cloud fetch failed: %s", cloud_raw)

    planets = _get_planets(lat, lon)
    viewing = _compute_verdict(bortle_int, moon["illumination"], clouds["now"])
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
        "moon":    moon,
        "clouds":  clouds,
        "planets": planets,
        "viewing": viewing,
    }
