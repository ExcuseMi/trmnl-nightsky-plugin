import math, logging, io, json, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import aiohttp
import ephem
from ephem import AlwaysUpError, NeverUpError, CircumpolarError
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from skyfield.api import Loader, Star, wgs84  # Star/wgs84 kept for test_sky_chart.py
from skyfield.data import hipparcos

_SF_LOADER: "Loader | None" = None
_HIP_DF    = None
_SF_TS     = None
_SF_EARTH  = None

def _skyfield():
    global _SF_LOADER, _HIP_DF, _SF_TS, _SF_EARTH
    if _HIP_DF is None:
        _SF_LOADER = Loader("/data/skyfield")
        _SF_TS     = _SF_LOADER.timescale()
        eph        = _SF_LOADER("de421.bsp")   # ~17 MB, cached in /data/skyfield
        _SF_EARTH  = eph["earth"]
        with _SF_LOADER.open(hipparcos.URL) as f:
            df = hipparcos.load_dataframe(f)
        _HIP_DF = df[df["magnitude"] <= 5.5].copy()
    return _SF_TS, _HIP_DF, _SF_EARTH

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


def _get_planets(lat: str, lon: str, epoch: "datetime | None" = None) -> list[dict]:
    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    ref = epoch or datetime.now(timezone.utc)
    obs.date = ref.strftime("%Y/%m/%d %H:%M:%S")

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
                "mag":           f"{float(body.mag):.1f}",
                "constellation": constellation,
            })
    return sorted(visible, key=lambda x: -x["alt"])


async def geocode(address: str) -> tuple[str | None, str | None, str | None]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "trmnl-nightsky-plugin/1.0"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
            results = await r.json(content_type=None)
            if results:
                return results[0]["lat"], results[0]["lon"], results[0].get("display_name")
            return None, None, None


def _compute_sun(lat: str, lon: str, tz_str: str, epoch: "datetime | None" = None) -> dict:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    ref = epoch or datetime.now(timezone.utc)
    obs.date = ref.strftime("%Y/%m/%d %H:%M:%S")
    sun = ephem.Sun(obs)

    def _to_epoch(ephem_date) -> int | None:
        try:
            return int(ephem.Date(ephem_date).datetime().replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            return None

    is_up = float(sun.alt) > 0
    rises = sets = None
    try:
        sets = _to_epoch(obs.next_setting(sun) if is_up else obs.previous_setting(sun))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass
    try:
        rises = _to_epoch(obs.next_rising(sun))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass

    return {
        "rises": rises,
        "sets":  sets,
        "is_day": is_up,
        "alt":   round(math.degrees(float(sun.alt))),
        "az":    round(math.degrees(float(sun.az))),
    }


def get_astronomical_dusk(lat: str, lon: str, dt_utc: datetime) -> datetime:
    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.pressure = 0
    obs.horizon = '-18'
    obs.date = dt_utc.strftime("%Y/%m/%d %H:%M:%S")
    sun = ephem.Sun()
    try:
        dusk_ephem = obs.next_setting(sun, use_center=True)
        return dusk_ephem.datetime().replace(tzinfo=timezone.utc)
    except Exception:
        return dt_utc


def _moon_day(lat: str, lon: str, tz_str: str, offset_days: int, epoch: "datetime | None" = None) -> dict:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    ref = epoch or datetime.now(timezone.utc)
    target_utc = ref + timedelta(days=offset_days)
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
        "label":        label,
        "phase":        phase,
        "illumination": illumination,
        "waxing":       elong_deg < 180,
    }


def _compute_moon(lat: str, lon: str, tz_str: str, epoch: "datetime | None" = None) -> tuple[dict, str | None]:
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = timezone.utc

    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = 0
    obs.pressure = 0
    ref = epoch or datetime.now(timezone.utc)
    obs.date = ref.strftime("%Y/%m/%d %H:%M:%S")

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

    def _to_epoch(ephem_date) -> int | None:
        try:
            return int(ephem.Date(ephem_date).datetime().replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            return None

    rises = sets = None
    try:
        rises = _to_epoch(obs.next_rising(moon))
    except (AlwaysUpError, NeverUpError, CircumpolarError):
        pass
    try:
        sets = _to_epoch(obs.next_setting(moon))
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
        best_from = _to_epoch(obs_twi.next_setting(sun, use_center=True))
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


def _build_forecast(weather_raw: dict, now_utc: datetime) -> dict:
    if not (isinstance(weather_raw, dict) and "hourly" in weather_raw):
        return {"now": {}}

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

    return {"now": {
        "cloud":      _v(clouds, idx),
        "cloud_low":  _v(cl_low, idx),
        "cloud_mid":  _v(cl_mid, idx),
        "cloud_high": _v(cl_high, idx),
        "temp":       round(_v(temps, idx, 0)),
        "dew_point":  round(_v(dews, idx, 0)),
        "humidity":   round(_v(rhs, idx, 0)),
        "wind_speed": round(_v(winds, idx, 0)),
        "wind_dir":   _wind_dir(_v(wdirs, idx, 0)),
    }}


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

    if score >= 8:   verdict = "Excellent"
    elif score >= 6: verdict = "Good"
    elif score >= 4: verdict = "Fair"
    else:            verdict = "Poor"

    return {"verdict": verdict}


# Constellation stick figures — authoritative RA/Dec polylines from d3-celestial.
# Each entry is a list of polylines; each polyline is a list of (ra_deg, dec_deg) points.
_CONST_LINES_URL = "https://raw.githubusercontent.com/ofrohn/d3-celestial/master/data/constellations.lines.json"
_CONST_NAMES_URL = "https://raw.githubusercontent.com/ofrohn/d3-celestial/master/data/constellations.json"
_CONST_DATA_DIR  = Path(__file__).parent.parent.parent / "data"

_CONST_POLYLINES_CACHE: "dict | None" = None


def _fetch_json_cached(url: str, cache_path: Path) -> dict:
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    logging.getLogger(__name__).info("Downloading %s", url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    with open(cache_path, "w") as f:
        json.dump(data, f)
    return data


def _get_const_polylines() -> "dict[str, list[list[tuple[float, float]]]]":
    global _CONST_POLYLINES_CACHE
    if _CONST_POLYLINES_CACHE is not None:
        return _CONST_POLYLINES_CACHE

    lines_data = _fetch_json_cached(_CONST_LINES_URL, _CONST_DATA_DIR / "constellations.lines.json")
    names_data = _fetch_json_cached(_CONST_NAMES_URL, _CONST_DATA_DIR / "constellations.json")

    name_map = {
        f["id"]: f["properties"]["name"]
        for f in names_data.get("features", [])
        if "name" in f.get("properties", {})
    }

    result: dict[str, list[list[tuple[float, float]]]] = {}
    for feature in lines_data.get("features", []):
        abbr = feature["id"]
        coords = feature["geometry"]["coordinates"]
        # d3-celestial stores RA in (-180, 180]; normalise to [0, 360)
        result[name_map.get(abbr, abbr)] = [[(ra % 360, dec) for ra, dec in line] for line in coords]

    _CONST_POLYLINES_CACHE = result
    return result



# fmt: on


def _radec_altaz(ra_deg: float, dec_deg: float, lat_rad: float, lst: float) -> tuple[float, float]:
    dec_r   = math.radians(dec_deg)
    ha_r    = math.radians((lst - ra_deg) % 360)
    sin_alt = math.sin(dec_r) * math.sin(lat_rad) + math.cos(dec_r) * math.cos(lat_rad) * math.cos(ha_r)
    alt_r   = math.asin(max(-1.0, min(1.0, sin_alt)))
    cos_alt = math.cos(alt_r)
    if cos_alt > 1e-10:
        cos_az = (math.sin(dec_r) - math.sin(alt_r) * math.sin(lat_rad)) / (cos_alt * math.cos(lat_rad))
        az_r   = math.acos(max(-1.0, min(1.0, cos_az)))
        if math.sin(ha_r) > 0:
            az_r = 2 * math.pi - az_r
    else:
        az_r = 0.0
    return math.degrees(alt_r), math.degrees(az_r)


def _generate_sky_chart(lat: str, lon: str, moon_data: dict,
                        w_px: int = 800, h_px: int = 480,
                        constellations: str = 'names',
                        epoch: "datetime | None" = None,
                        sun_data: "dict | None" = None) -> bytes:
    ts, hip, _earth = _skyfield()
    lat_f, lon_f = float(lat), float(lon)

    # Local Sidereal Time via GMST (accurate to ~1 arcmin — fine for a display chart)
    # Use caller-supplied epoch (utc_hr) so chart and constellation SVG share the same LST.
    if epoch is not None:
        jd = 2440587.5 + epoch.timestamp() / 86400
    else:
        jd = ts.now().ut1
    T    = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T ** 2) % 360
    lst  = (gmst + lon_f) % 360  # degrees

    # Vectorised equatorial → horizontal
    ra_deg  = hip["ra_hours"].values * 15.0
    dec_rad = np.radians(hip["dec_degrees"].values)
    ha_rad  = np.radians(lst - ra_deg)
    lat_rad = math.radians(lat_f)

    sin_alt = (np.sin(dec_rad) * math.sin(lat_rad)
               + np.cos(dec_rad) * math.cos(lat_rad) * np.cos(ha_rad))
    alt_rad = np.arcsin(np.clip(sin_alt, -1.0, 1.0))

    cos_alt = np.cos(alt_rad)
    safe    = cos_alt > 1e-10
    cos_az  = np.where(
        safe,
        (np.sin(dec_rad) - np.sin(alt_rad) * math.sin(lat_rad))
        / np.where(safe, cos_alt * math.cos(lat_rad), 1.0),
        0.0,
    )
    az_rad = np.arccos(np.clip(cos_az, -1.0, 1.0))
    az_rad = np.where(np.sin(ha_rad) > 0, 2 * np.pi - az_rad, az_rad)

    alt_deg = np.degrees(alt_rad)
    az_deg  = np.degrees(az_rad)
    above   = alt_deg > 0
    alt_v, az_v = alt_deg[above], az_deg[above]
    mag_v       = hip["magnitude"].values[above]

    # ── matplotlib rectangular panoramic chart ──────────────────────────────────
    W_PX, H_PX, DPI = w_px, h_px, 100
    fig, ax = plt.subplots(figsize=(W_PX / DPI, H_PX / DPI), dpi=DPI)
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.axis("off")

    # Stars
    sizes  = np.clip((5.5 - mag_v) ** 2.2 * 0.8, 0.5, 60)
    colors = np.zeros((len(alt_v), 4))
    colors[:, :3] = 1.0
    colors[:, 3]  = np.clip((5.5 - mag_v) / 6.0, 0.2, 1.0)
    ax.scatter(az_v, alt_v, s=sizes, c=colors, linewidths=0, zorder=2)

    # Moon
    moon_alt = moon_data.get("alt", -1)
    if moon_alt > 0:
        moon_az  = moon_data.get("az", 0)
        illum    = moon_data.get("illumination", 50)
        is_waxing = ("Waxing" in moon_data.get("phase", "")
                     or moon_data.get("phase") in ("New Moon", "First Quarter"))
        ax.plot(moon_az, moon_alt, "o", markersize=16, color="#ddd",
                markeredgecolor="#888", markeredgewidth=0.8, zorder=4)
        if 2 < illum < 98:
            ax.plot(moon_az, moon_alt, "o", markersize=16, color="black",
                    alpha=abs(1 - illum / 50) * 0.85, zorder=5)
        ax.text(moon_az, moon_alt + 3.5, "Moon", ha="center", va="bottom",
                fontsize=8, color="#aaa", zorder=6)

    # Sun
    if sun_data:
        s_alt = sun_data.get("alt", -90)
        s_az  = sun_data.get("az", 0)
        if s_alt > -5:
            s_alt = max(0.5, s_alt)
            ax.plot(s_az, s_alt, "o", markersize=26, color="white", alpha=0.12, zorder=3)
            ax.plot(s_az, s_alt, "o", markersize=16, color="white",
                    markeredgecolor="#bbb", markeredgewidth=0.5, zorder=5)
            ax.text(s_az, s_alt + 4.5, "Sun", ha="center", va="bottom",
                    fontsize=8, color="#ccc", zorder=6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor="black",
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _constellation_svg_data(lat: str, lon: str, constellations: str,
                            epoch: "datetime | None" = None) -> list[dict]:
    if constellations == 'hide':
        return []
    lat_r = math.radians(float(lat))
    ref   = epoch or datetime.now(timezone.utc)
    jd    = 2440587.5 + ref.timestamp() / 86400
    T    = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T ** 2) % 360
    lst  = (gmst + float(lon)) % 360

    show_names = constellations == 'names'
    result = []
    items = _get_const_polylines().items()
    for name, polylines in items:
        segs: list[list[float]] = []
        label_pts: list[tuple[float, float]] = []
        for polyline in polylines:
            altaz = [_radec_altaz(ra, dec, lat_r, lst) for ra, dec in polyline]
            for i in range(len(altaz) - 1):
                alt1, az1 = altaz[i]
                alt2, az2 = altaz[i + 1]
                if alt1 > 0 and alt2 > 0 and abs(az1 - az2) < 180:
                    segs.append([round(az1, 1), round(alt1, 1), round(az2, 1), round(alt2, 1)])
            label_pts.extend((az, alt) for alt, az in altaz if alt > 2)
        if not segs:
            continue
        entry: dict = {"n": name, "ls": segs}
        if show_names and label_pts:
            entry["laz"]  = round(sum(p[0] for p in label_pts) / len(label_pts), 1)
            entry["lalt"] = round(sum(p[1] for p in label_pts) / len(label_pts), 1)
        result.append(entry)
    return result



async def build_sky_data(lat: str, lon: str, bortle_str: str, tz_str: str,
                         constellations: str = 'hide',
                         epoch: "datetime | None" = None,
                         location_name: str | None = None) -> dict:
    bortle_str = bortle_str if bortle_str in BORTLE_MAP else "5"
    bortle_info = BORTLE_MAP[bortle_str]
    bortle_int = int(bortle_str)

    try:
        local_tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        local_tz = timezone.utc

    ref_utc  = epoch or datetime.now(timezone.utc)
    date_str = ref_utc.astimezone(local_tz).strftime("%-d %b %Y")
    time_str = ref_utc.astimezone(local_tz).strftime("%H:%M")

    moon, best_from = _compute_moon(lat, lon, tz_str, epoch=ref_utc)
    moon.pop("alt", None)
    moon.pop("az", None)
    moon["days"] = [_moon_day(lat, lon, tz_str, i, epoch=ref_utc) for i in range(4)]
    sun = _compute_sun(lat, lon, tz_str, epoch=ref_utc)
    is_day = sun.pop("is_day", False)

    async with aiohttp.ClientSession() as session:
        weather_raw = await _fetch_weather(session, lat, lon)
        if isinstance(weather_raw, Exception):
            log.warning("Weather fetch failed: %s", weather_raw)
            weather_raw = {}

    forecast = _build_forecast(weather_raw, ref_utc)

    planets = _get_planets(lat, lon, epoch=ref_utc)
    viewing = _compute_verdict(bortle_int, moon["illumination"], forecast["now"].get("cloud", 0))
    viewing["date"] = date_str
    viewing["chart_time"] = time_str
    viewing["chart_epoch"] = int(ref_utc.timestamp())
    if best_from:
        viewing["best_from"] = best_from

    return {
        "sky": {
            "bortle":       bortle_int,
            "bortle_label": bortle_info["label"],
            "nelm":         bortle_info["nelm"],
            "is_day":       is_day,
            "location":     location_name,
        },
        "sun":            sun,
        "moon":           moon,
        "forecast":       forecast,
        "planets":        planets,
        "viewing":        viewing,
        "constellations": _constellation_svg_data(lat, lon, constellations, ref_utc),
    }
