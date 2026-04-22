"""
Run inside the container:
  docker exec trmnl-nightsky-backend python test_sky_chart.py
"""
import sys, math
sys.path.insert(0, "/app")

from modules.providers.sky import _skyfield, _generate_sky_chart
import numpy as np


def _section(name):
    print(f"\n{'─'*50}")
    print(f"  {name}")
    print('─'*50)


def test_catalog_load():
    _section("1. Catalog load")
    ts, hip, earth = _skyfield()
    print(f"  timescale   : {ts}")
    print(f"  earth body  : {earth}")
    print(f"  stars loaded: {len(hip):,}")
    print(f"  columns     : {list(hip.columns)}")
    bright = hip[hip["magnitude"] < 1]
    print(f"  mag < 1     : {len(bright)} stars")
    assert len(hip) > 50_000, "catalog too small"
    print("  PASS")


def test_altaz_math():
    _section("2. Pure-math alt/az (London, now)")
    ts, hip, _ = _skyfield()
    lat_f, lon_f = 51.5074, -0.1278

    t   = ts.now()
    jd  = t.ut1
    T   = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T**2) % 360
    lst  = (gmst + lon_f) % 360

    ra_deg  = hip["ra_hours"].values * 15.0
    dec_rad = np.radians(hip["dec_degrees"].values)
    ha_rad  = np.radians(lst - ra_deg)
    lat_rad = math.radians(lat_f)

    sin_alt = (np.sin(dec_rad) * math.sin(lat_rad)
               + np.cos(dec_rad) * math.cos(lat_rad) * np.cos(ha_rad))
    alt_deg = np.degrees(np.arcsin(np.clip(sin_alt, -1, 1)))

    above = alt_deg > 0
    print(f"  above horizon: {above.sum():,} of {len(hip):,}")
    assert above.sum() > 1000, "too few stars above horizon"
    print("  PASS")


def test_skyfield_observer():
    _section("3. Skyfield observer + apparent() — expect crash or success")
    from skyfield.api import Star, wgs84
    ts, hip, earth = _skyfield()
    t        = ts.now()
    observer = earth + wgs84.latlon(51.5, -0.12)
    sample   = hip[hip["magnitude"] < 4].head(50)
    try:
        astr = observer.at(t).observe(Star.from_dataframe(sample)).apparent()
        alt, az, _ = astr.altaz()
        print(f"  .apparent() worked: {(alt.degrees > 0).sum()} above horizon")
    except Exception as e:
        print(f"  .apparent() failed (expected): {type(e).__name__}: {e}")
    print("  DONE")


def test_chart_generation():
    _section("4. Full chart generation")
    moon = {"alt": 30, "az": 195, "illumination": 26, "phase": "Waxing Crescent"}
    # _generate_sky_chart no longer takes planets; it takes w_px, h_px, constellations, epoch, sun_data
    chart = _generate_sky_chart("51.5074", "-0.1278", moon, 800, 480)
    assert isinstance(chart, bytes), "should return bytes"
    assert chart.startswith(b"\x89PNG"), "should be a PNG"
    size_kb = len(chart) / 1024
    print(f"  chart size: {size_kb:.1f} KB")
    print("  PASS")


if __name__ == "__main__":
    test_catalog_load()
    test_altaz_math()
    test_skyfield_observer()
    test_chart_generation()
    print("\n✓ All done\n")
