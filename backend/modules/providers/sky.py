import asyncio, math, logging, base64, io
from datetime import datetime, timezone, timedelta
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


_PLANET_ABBR = {
    "Mercury": "Mer", "Venus": "Ven", "Mars": "Mar",
    "Jupiter": "Jup", "Saturn": "Sat", "Uranus": "Ura", "Neptune": "Nep",
}


def _generate_sky_chart(lat: str, lon: str, moon_data: dict, planets: list) -> str:
    ts, hip, _earth = _skyfield()
    lat_f, lon_f = float(lat), float(lon)

    # Local Sidereal Time via GMST (accurate to ~1 arcmin — fine for a display chart)
    jd   = ts.now().ut1
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

    # ── matplotlib polar chart ──────────────────────────────────────────────
    PX = 220
    fig = plt.figure(figsize=(PX / 100, PX / 100), dpi=100, facecolor="black")
    ax  = fig.add_axes([0.08, 0.08, 0.84, 0.84], projection="polar", facecolor="black")

    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.grid(False)
    ax.spines["polar"].set_color("#555")
    ax.spines["polar"].set_linewidth(0.8)

    # Altitude rings at 30 and 60 degrees
    ring_theta = np.linspace(0, 2 * np.pi, 360)
    for alt_ring in (30, 60):
        ax.plot(ring_theta, np.full(360, 1 - alt_ring / 90),
                color="#333", linewidth=0.5, linestyle="--", zorder=1)

    # Stars — size and alpha scaled by magnitude
    r_s     = 1 - alt_v / 90
    theta_s = np.radians(az_v)
    sizes   = np.clip((5.5 - mag_v) ** 2.2 * 0.4, 0.2, 30)
    colors  = np.zeros((len(r_s), 4))
    colors[:, :3] = 1.0
    colors[:, 3]  = np.clip((5.5 - mag_v) / 6.0, 0.25, 1.0)
    ax.scatter(theta_s, r_s, s=sizes, c=colors, linewidths=0, zorder=2)

    # Moon
    moon_alt = moon_data.get("alt", -1)
    if moon_alt > 0:
        r_m   = 1 - moon_alt / 90
        th_m  = math.radians(moon_data.get("az", 0))
        illum = moon_data.get("illumination", 50)
        is_waxing = ("Waxing" in moon_data.get("phase", "")
                     or moon_data.get("phase") in ("New Moon", "First Quarter"))
        ax.plot(th_m, r_m, "o", markersize=10, color="#ccc",
                markeredgecolor="#666", markeredgewidth=0.5, zorder=4)
        if 2 < illum < 98:
            shadow_alpha = abs(1 - illum / 50) * 0.9
            ax.plot(th_m + (0 if is_waxing else math.pi), r_m,
                    "o", markersize=10, color="black",
                    alpha=shadow_alpha, zorder=5)
        ax.text(th_m, r_m - 0.1, "Moon", ha="center", va="top",
                fontsize=5.5, color="#bbb", zorder=6)

    # Planets
    for pl in planets:
        r_p  = 1 - pl["alt"] / 90
        th_p = math.radians(pl["az"])
        abbr = _PLANET_ABBR.get(pl["name"], pl["name"][:3])
        ax.plot(th_p, r_p, "o", markersize=5, color="white",
                markeredgecolor="#aaa", markeredgewidth=0.3, zorder=4)
        ax.text(th_p, r_p - 0.08, abbr, ha="center", va="top",
                fontsize=5.5, color="white", fontweight="bold", zorder=5)

    # Cardinal direction labels
    for label, az_deg in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        ax.text(math.radians(az_deg), 1.13, label,
                ha="center", va="center", fontsize=8,
                color="white", fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100,
                facecolor="black", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode()


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
