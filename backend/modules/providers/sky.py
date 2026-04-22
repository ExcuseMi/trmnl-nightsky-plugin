import math, logging, io
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
                "mag":           f"{float(body.mag):.1f}",
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

    return {"rises": rises, "sets": sets, "is_day": is_up}


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
        "label":        label,
        "phase":        phase,
        "illumination": illumination,
        "waxing":       elong_deg < 180,
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
# d3-celestial stores RA as degrees in (-180, 180]; convert with lon % 360.
# fmt: off
_d = lambda lo, la: (lo % 360, la)

_CONST_POLYLINES: dict[str, list[list[tuple[float, float]]]] = {
    "Orion": [
        [_d(91.893,14.7685),_d(88.5958,20.2762),_d(90.9799,20.1385),_d(92.985,14.2088),_d(90.5958,9.6473),_d(88.7929,7.4071),_d(81.2828,6.3497),_d(73.7239,10.1508)],
        [_d(74.6371,1.714),_d(73.5629,2.4407),_d(72.8015,5.6051),_d(72.46,6.9613),_d(72.653,8.9002),_d(73.7239,10.1508),_d(74.0928,13.5145),_d(76.1423,15.4041),_d(77.4248,15.5972)],
        [_d(78.6345,-8.2016),_d(81.1192,-2.3971),_d(83.0017,-0.2991),_d(81.2828,6.3497),_d(83.7845,9.9342),_d(88.7929,7.4071),_d(85.1897,-1.9426),_d(86.9391,-9.6696)],
        [_d(85.1897,-1.9426),_d(84.0534,-1.2019),_d(83.0017,-0.2991)],
    ],
    "Ursa Major": [
        [_d(-176.1435,57.0326),_d(165.932,61.751),_d(165.4603,56.3824),_d(178.4577,53.6948),_d(-176.1435,57.0326),_d(-166.4927,55.9598),_d(-159.0186,54.9254),_d(-153.1148,49.3133)],
        [_d(178.4577,53.6948),_d(176.5126,47.7794),_d(169.6197,33.0943),_d(169.5468,31.5308)],
        [_d(176.5126,47.7794),_d(167.4159,44.4985),_d(155.5823,41.4995)],
        [_d(167.4159,44.4985),_d(154.2741,42.9144)],
        [_d(165.932,61.751),_d(142.8821,63.0619),_d(127.5661,60.7182),_d(147.7473,59.0387),_d(165.4603,56.3824)],
        [_d(165.4603,56.3824),_d(148.0265,54.0643),_d(143.2143,51.6773),_d(134.8019,48.0418)],
        [_d(135.9064,47.1565),_d(143.2143,51.6773)],
    ],
    "Cassiopeia": [
        [_d(28.5989,63.6701),_d(21.454,60.2353),_d(14.1772,60.7167),_d(10.1268,56.5373),_d(2.2945,59.1498)],
    ],
    "Leo": [
        [_d(152.093,11.9672),_d(151.8331,16.7627),_d(154.9931,19.8415),_d(168.5271,20.5237),_d(177.2649,14.5721),_d(168.56,15.4296),_d(152.093,11.9672)],
        [_d(154.9931,19.8415),_d(154.1726,23.4173),_d(148.1909,26.007),_d(146.4628,23.7743)],
    ],
    "Scorpius": [
        [_d(-120.287,-26.1141),_d(-119.9166,-22.6217),_d(-118.6407,-19.8055)],
        [_d(-119.9166,-22.6217),_d(-114.7028,-25.5928),_d(-112.6481,-26.432),_d(-111.0294,-28.216),_d(-107.4591,-34.2932),_d(-107.0324,-38.0474),_d(-106.3541,-42.3613),_d(-101.9617,-43.2392),_d(-95.6703,-42.9978),_d(-93.1038,-40.127),_d(-94.378,-39.03),_d(-96.5978,-37.1038)],
    ],
    "Cygnus": [
        [_d(-41.7659,30.2269),_d(-48.4472,33.9703),_d(-54.4429,40.2567),_d(-63.7563,45.1308),_d(-67.5735,51.7298),_d(-70.7243,53.3685)],
        [_d(-49.642,45.2803),_d(-54.4429,40.2567),_d(-60.9235,35.0834),_d(-67.3197,27.9597)],
    ],
    "Gemini": [
        [_d(93.7194,22.5068),_d(95.7401,22.5136),_d(100.983,25.1311),_d(107.7849,30.2452),_d(113.6494,31.8883),_d(116.329,28.0262),_d(113.9806,26.8957),_d(110.0307,21.9823),_d(106.0272,20.5703),_d(99.4279,16.3993),_d(101.3224,12.8956)],
        [_d(110.0307,21.9823),_d(109.5232,16.5404)],
    ],
    "Perseus": [
        [_d(56.0797,32.2882),_d(58.533,31.8836),_d(59.7413,35.791),_d(59.4635,40.0102),_d(56.2985,42.5785),_d(55.7313,47.7876),_d(54.1224,48.1926),_d(51.0807,49.8612),_d(46.1991,53.5064),_d(42.6742,55.8955),_d(43.5644,52.7625),_d(47.2667,49.6133),_d(47.374,44.8575),_d(47.0422,40.9556),_d(47.8224,39.6116),_d(46.2941,38.8403),_d(44.6903,39.6627),_d(44.9162,41.0329),_d(47.0422,40.9556)],
        [_d(61.646,50.3513),_d(63.7244,48.4093),_d(62.1654,47.7125),_d(55.7313,47.7876)],
        [_d(47.2667,49.6133),_d(41.0499,49.2284),_d(25.9152,50.6887)],
    ],
    "Auriga": [
        [_d(89.8822,44.9474),_d(79.1723,45.998),_d(76.6287,41.2345),_d(74.2484,33.1661),_d(81.573,28.6075),_d(89.9303,37.2126),_d(89.8822,44.9474),_d(89.8818,54.2847),_d(79.1723,45.998),_d(75.4922,43.8233),_d(75.6195,41.0758)],
    ],
    "Boötes": [
        [_d(-153.1844,17.4569),_d(-151.3288,18.3977),_d(-146.0847,19.1824),_d(-142.0425,30.3714),_d(-141.9805,38.3083),_d(-134.5135,40.3906),_d(-131.1243,33.3148),_d(-138.7533,27.0742),_d(-146.0847,19.1824),_d(-139.7127,13.7283)],
        [_d(-141.9805,38.3083),_d(-145.9041,46.0883),_d(-146.6341,51.7879),_d(-143.7008,51.8507),_d(-145.9041,46.0883)],
    ],
    "Virgo": [
        [_d(176.4648,6.5294),_d(177.6738,1.7647),_d(-175.0235,-0.6668),_d(-169.5848,-1.4494),_d(-162.5125,-5.539),_d(-158.7018,-11.1613),_d(-145.9964,-6.0005),_d(-139.2349,-5.6582)],
        [_d(-164.4558,10.9592),_d(-166.0991,3.3975),_d(-169.5848,-1.4494)],
        [_d(-162.5125,-5.539),_d(-156.3267,-0.5958),_d(-149.5884,1.5445),_d(-138.4378,1.8929)],
    ],
    "Taurus": [
        [_d(84.4112,21.1425),_d(68.9802,16.5093),_d(67.1656,15.8709),_d(64.9483,15.6276),_d(65.7337,17.5425),_d(67.1542,19.1804),_d(81.573,28.6075)],
        [_d(64.9483,15.6276),_d(60.1701,12.4903),_d(51.7923,9.7327),_d(60.7891,5.9893)],
        [_d(51.7923,9.7327),_d(51.2033,9.0289),_d(54.2183,0.4017)],
    ],
    "Aquila": [
        [_d(-63.4351,10.6133),_d(-62.3042,8.8683),_d(-61.1717,6.4068),_d(-57.1738,-0.8215),_d(-61.8818,1.0057),_d(-68.6254,3.1148),_d(-73.6475,13.8635),_d(-62.3042,8.8683),_d(-68.6254,3.1148),_d(-73.4378,-4.8826)],
    ],
    "Lyra": [
        [_d(-78.8068,37.6051),_d(-78.9051,39.6127),_d(-80.7653,38.7837),_d(-78.8068,37.6051),_d(-76.3738,36.8986),_d(-75.2641,32.6896),_d(-77.48,33.3627),_d(-78.8068,37.6051)],
    ],
    "Sagittarius": [
        [_d(-85.5932,-36.7617),_d(-83.957,-34.3846),_d(-84.7515,-29.8281),_d(-83.0073,-25.4217),_d(-86.5591,-21.0588)],
        [_d(-69.3404,-44.459),_d(-69.0284,-40.6159),_d(-74.347,-29.8801),_d(-78.5859,-26.9908),_d(-83.0073,-25.4217)],
        [_d(-61.1846,-41.8683),_d(-60.0659,-35.2763),_d(-61.0402,-26.2995),_d(-65.8232,-24.8836),_d(-68.6813,-24.5086),_d(-71.1149,-25.2567),_d(-76.1836,-26.2967),_d(-78.5859,-26.9908),_d(-84.7515,-29.8281),_d(-88.548,-30.4241),_d(-83.957,-34.3846),_d(-74.347,-29.8801),_d(-73.265,-27.6704),_d(-76.1836,-26.2967),_d(-73.8292,-21.7415),_d(-72.559,-21.0236),_d(-70.5913,-18.9529),_d(-69.5818,-17.8472),_d(-69.5682,-15.955)],
        [_d(-73.8292,-21.7415),_d(-75.5675,-21.1067),_d(-76.4576,-22.7448),_d(-76.1836,-26.2967)],
    ],
    "Hercules": [
        [_d(-114.5199,19.1531),_d(-112.445,21.4896),_d(-109.6785,31.6027),_d(-109.276,38.9223),_d(-111.4742,42.437),_d(-115.0648,46.3134),_d(-117.8076,44.9349),_d(-121.8311,42.4515)],
        [_d(-109.6785,31.6027),_d(-104.9276,30.9264)],
        [_d(-109.276,38.9223),_d(-101.2382,36.8092)],
        [_d(-90.9367,37.2505),_d(-99.0794,37.1459),_d(-101.2382,36.8092),_d(-104.9276,30.9264),_d(-101.242,24.8392),_d(-93.3853,27.7207),_d(-90.5588,29.2479),_d(-88.1144,28.7625)],
        [_d(-101.3381,14.3903),_d(-112.445,21.4896)],
    ],
    "Pegasus": [
        [_d(-27.5031,33.1782),_d(-19.2494,30.2212),_d(-14.0564,28.0828),_d(2.0969,29.0904),_d(3.309,15.1836),_d(-13.8098,15.2053),_d(-18.3267,12.1729),_d(-19.6345,10.8314),_d(-27.4501,6.1979),_d(-33.9535,9.875)],
        [_d(-13.8098,15.2053),_d(-14.0564,28.0828),_d(-17.4992,24.6016),_d(-18.3672,23.5657),_d(-28.2472,25.3451),_d(-33.8386,25.645)],
    ],
    "Andromeda": [
        [_d(30.9748,42.3297),_d(17.433,35.6206),_d(9.832,30.861),_d(2.0969,29.0904)],
        [_d(14.3017,23.4176),_d(11.8347,24.2672),_d(9.6389,29.3118),_d(9.832,30.861),_d(9.2202,33.7193),_d(-5.4658,43.2681),_d(-14.5197,42.326)],
        [_d(-5.4658,43.2681),_d(-4.8979,44.3339),_d(-5.609,46.4582)],
        [_d(17.433,35.6206),_d(14.1884,38.4993),_d(12.4535,41.0789),_d(17.3755,47.2418),_d(24.4982,48.6282)],
        [_d(-4.8979,44.3339),_d(-3.4915,46.4203)],
    ],
    "Canis Major": [
        [_d(95.6749,-17.9559),_d(101.2872,-16.7161),_d(105.7561,-23.8333),_d(107.0979,-26.3932),_d(105.4298,-27.9348),_d(104.6565,-28.9721),_d(95.0783,-30.0634)],
        [_d(111.0238,-29.3031),_d(107.0979,-26.3932)],
        [_d(101.2872,-16.7161),_d(104.0343,-17.0542),_d(105.9396,-15.6333),_d(103.5475,-12.0386),_d(104.0343,-17.0542)],
    ],
    "Canis Minor": [
        [_d(114.8255,5.225),_d(111.7877,8.2893)],
    ],
    "Ursa Minor": [
        [_d(-123.9853,77.7945),_d(-115.6238,75.7553),_d(-129.8179,71.834),_d(-137.3236,74.1555),_d(-123.9853,77.7945),_d(-108.5073,82.0373),_d(-96.9458,86.5865),_d(37.9545,89.2641)],
    ],
    "Corona Borealis": [
        [_d(-126.7676,31.3591),_d(-128.0428,29.1057),_d(-126.328,26.7147),_d(-124.3143,26.2956),_d(-122.6015,26.0684),_d(-120.6031,26.8779),_d(-119.6393,29.8511)],
    ],
    "Pisces": [
        [_d(18.4373,24.5837),_d(17.9152,30.0896),_d(19.8666,27.2641),_d(18.4373,24.5837),_d(17.8634,21.0347),_d(22.8709,15.3458),_d(26.3485,9.1577),_d(30.5118,2.7638),_d(28.389,3.1875),_d(25.3579,5.4876),_d(22.5463,6.1438),_d(18.4329,7.5754),_d(15.7359,7.8901),_d(12.1706,7.5851),_d(-0.1721,6.8633),_d(-5.0123,5.6263),_d(-8.0079,6.379),_d(-9.9142,5.3813),_d(-10.7086,3.2823),_d(-8.2669,1.2556),_d(-4.4883,1.78),_d(-3.402,3.4868),_d(-5.0123,5.6263)],
        [_d(-10.7086,3.2823),_d(-14.0308,3.82)],
    ],
    "Aries": [
        [_d(42.496,27.2605),_d(31.7934,23.4624),_d(28.66,20.808),_d(28.3826,19.2939)],
    ],
    "Aquarius": [
        [_d(-48.081,-9.4958),_d(-46.8365,-8.9833),_d(-37.1103,-5.5712),_d(-28.554,-0.3199),_d(-24.5859,-1.3873),_d(-22.792,-0.02),_d(-21.1609,-0.1175),_d(-16.8464,-7.5796),_d(-10.5241,-9.1825),_d(-12.6383,-21.1724)],
        [_d(-37.1103,-5.5712),_d(-28.3907,-13.8697)],
        [_d(-28.554,-0.3199),_d(-25.7915,-7.7833)],
        [_d(-22.792,-0.02),_d(-23.6807,1.3774)],
        [_d(-9.2574,-20.1006),_d(-10.5241,-9.1825),_d(-4.5591,-17.8165)],
    ],
    "Capricornus": [
        [_d(-55.588,-12.5082),_d(-54.7472,-14.7814),_d(-52.7849,-17.8137),_d(-48.4761,-25.2709),_d(-47.0446,-26.9191),_d(-38.3332,-22.4113),_d(-33.2398,-16.1273),_d(-34.9773,-16.6623),_d(-39.4383,-16.8345),_d(-43.5132,-17.2329),_d(-55.588,-12.5082)],
    ],
    "Ophiuchus": [
        [_d(-90.2434,-9.7736),_d(-93.0268,2.7073),_d(-94.1319,4.5673),_d(-96.2664,12.56),_d(-105.5829,9.375),_d(-112.2716,1.9839),_d(-116.4136,-3.6943),_d(-115.4196,-4.6925),_d(-110.7103,-10.5671),_d(-102.4055,-15.7249)],
        [_d(-105.5829,9.375),_d(-110.7103,-10.5671),_d(-112.2151,-16.6127),_d(-113.244,-18.4563),_d(-113.9742,-20.0373),_d(-113.6037,-23.4472)],
        [_d(-94.1319,4.5673),_d(-102.4055,-15.7249),_d(-99.4976,-24.9995),_d(-98.1614,-29.867)],
    ],
    "Draco": [
        [_d(-91.6178,56.8726),_d(-90.8485,51.4889),_d(-97.3918,52.3014),_d(-96.9332,55.173),_d(-91.6178,56.8726),_d(-71.8612,67.6615),_d(-84.8107,71.3378),_d(-102.8034,65.7147),_d(-114.0021,61.5142),_d(-119.5277,58.5653),_d(-128.7676,58.9661),_d(-148.9027,64.3759),_d(-171.6294,69.7882),_d(172.8509,69.3311)],
        [_d(-84.8107,71.3378),_d(-84.7359,72.7328)],
        [_d(-71.8612,67.6615),_d(-62.9569,70.2679)],
    ],
    "Centaurus": [
        [_d(170.2517,-54.491),_d(-177.9104,-50.7224),_d(-172.9901,-50.2306),_d(-169.6207,-48.9599),_d(-155.0281,-53.4664),_d(-151.1151,-47.2884),_d(-152.5959,-42.4737),_d(-152.6238,-41.6877),_d(-148.3294,-36.37),_d(-141.1232,-42.1578),_d(-135.2096,-42.1042)],
        [_d(-152.6238,-41.6877),_d(-159.8508,-36.7123)],
        [_d(-140.1038,-60.8372),_d(-155.0281,-53.4664),_d(-149.0441,-60.373)],
        [_d(-172.9901,-50.2306),_d(-177.087,-52.3685),_d(172.942,-59.4421)],
    ],
    "Piscis Austrinus": [
        [_d(-19.8361,-27.0436),_d(-15.5873,-29.6222),_d(-16.0129,-32.5396),_d(-16.8686,-32.8755),_d(-22.1236,-32.3461),_d(-27.9041,-32.9885),_d(-33.7633,-33.0258),_d(-33.066,-30.8983),_d(-27.9041,-32.9885),_d(-19.8361,-27.0436)],
    ],
}
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
                        epoch: "datetime | None" = None) -> bytes:
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
    for name, polylines in _CONST_POLYLINES.items():
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
                         epoch: "datetime | None" = None) -> dict:
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
    moon.pop("alt", None)
    moon.pop("az", None)
    moon["days"] = [_moon_day(lat, lon, tz_str, i) for i in range(4)]
    sun = _compute_sun(lat, lon, tz_str)
    is_day = sun.pop("is_day", False)

    async with aiohttp.ClientSession() as session:
        weather_raw = await _fetch_weather(session, lat, lon)
        if isinstance(weather_raw, Exception):
            log.warning("Weather fetch failed: %s", weather_raw)
            weather_raw = {}

    forecast = _build_forecast(weather_raw, now_utc)

    planets = _get_planets(lat, lon)
    viewing = _compute_verdict(bortle_int, moon["illumination"], forecast["now"].get("cloud", 0))
    viewing["date"] = date_str
    if best_from:
        viewing["best_from"] = best_from

    return {
        "sky": {
            "bortle":       bortle_int,
            "bortle_label": bortle_info["label"],
            "nelm":         bortle_info["nelm"],
            "is_day":       is_day,
        },
        "sun":            sun,
        "moon":           moon,
        "forecast":       forecast,
        "planets":        planets,
        "viewing":        viewing,
        "constellations": _constellation_svg_data(lat, lon, constellations, epoch),
    }
