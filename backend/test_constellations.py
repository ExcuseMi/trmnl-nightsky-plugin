"""
Constellation rendering regression tests.

Run locally (requires backend deps):
  cd backend && python test_constellations.py

Run in container:
  docker exec trmnl-nightsky-backend python test_constellations.py

Covers:
  - No segment endpoint at or above alt=88° (near-zenith az singularity)
  - No high-altitude (avg>65°) segment with az-span >30° (projection distortion)
  - No segment crossing az=0/360 without being split (wrap segments land at 0 or 360)
  - Label azimuth is the circular mean — constellations straddling 0/360 don't jump to ~180°
  - Known shapes: Perseus, Cassiopeia, Ursa Minor present and segment counts sane
"""
import sys, math
sys.path.insert(0, "/app")

from datetime import datetime, timezone
from modules.providers.sky import _constellation_svg_data


# ---------------------------------------------------------------------------
# Fixture: Deerlijk, Belgium — time that exposed all three bugs
# ---------------------------------------------------------------------------
LAT, LON = "51.03", "3.43"
EPOCH = datetime(2026, 4, 22, 11, 30, tzinfo=timezone.utc)


def _get_data():
    return _constellation_svg_data(LAT, LON, "names", EPOCH)


def _section(title):
    print(f"\n{'─'*55}\n  {title}\n{'─'*55}")


# ---------------------------------------------------------------------------

def test_no_near_zenith_endpoints():
    _section("1. No near-zenith endpoints (alt >= 88°)")
    data = _get_data()
    bad = []
    for c in data:
        for s in c["ls"]:
            if s[1] >= 88 or s[3] >= 88:
                bad.append((c["n"], s))
    if bad:
        for name, s in bad:
            print(f"  FAIL  {name}: {s}")
        raise AssertionError(f"{len(bad)} segments with near-zenith endpoint")
    print(f"  checked {sum(len(c['ls']) for c in data)} segments — PASS")


def test_no_high_alt_wide_az_segments():
    _section("2. No high-alt (avg>65°) segments with az-span >30° (projection distortion)")
    data = _get_data()
    bad = []
    for c in data:
        for s in c["ls"]:
            az_span = abs(s[0] - s[2])
            if az_span > 180:
                continue  # split wrap segments are handled separately
            if (s[1] + s[3]) > 130 and az_span > 30:
                bad.append((c["n"], s))
    if bad:
        for name, s in bad:
            print(f"  FAIL  {name}: {s}  span={abs(s[0]-s[2]):.1f}°")
        raise AssertionError(f"{len(bad)} distorted high-altitude segments")
    print(f"  PASS")


def test_no_raw_wrap_segments():
    _section("3. No segments crossing az=0/360 without being split")
    data = _get_data()
    bad = []
    for c in data:
        for s in c["ls"]:
            az_span = abs(s[0] - s[2])
            # A raw crossing would have az_span > 180 AND neither endpoint at 0 or 360
            if az_span > 180 and s[0] not in (0.0, 360.0) and s[2] not in (0.0, 360.0):
                bad.append((c["n"], s))
    if bad:
        for name, s in bad:
            print(f"  FAIL  {name}: {s}")
        raise AssertionError(f"{len(bad)} unsplit wrap segments")
    print(f"  PASS")


def test_label_circular_mean():
    _section("4. Label azimuths use circular mean (no ~180° jump for polar constellations)")
    data = _get_data()
    # Cassiopeia and Ursa Minor straddle az=0 from Belgium — their labels
    # must NOT be near 180° (which would be the naive linear-mean artifact).
    polar = {c["n"]: c for c in data if c["n"] in ("Cassiopeia", "Ursa Minor")}
    for name, entry in polar.items():
        laz = entry.get("laz")
        assert laz is not None, f"{name} has no label"
        # Label should be in the northern sky, NOT near 180° (south)
        assert not (150 < laz < 210), (
            f"{name} label az={laz:.1f}° looks like a linear-mean wrap artifact "
            f"(expected near 0°/360°, got near 180°)"
        )
        print(f"  {name}: laz={laz:.1f}° — OK")
    print(f"  PASS")


def test_known_constellations_present():
    _section("5. Known constellations present and segment counts sane")
    data = _get_data()
    by_name = {c["n"]: c for c in data}

    checks = {
        "Perseus":    (18, 30),   # main body + algol loop + branches
        "Cassiopeia": (4,  8),    # W-shape (some segs may be split at wrap)
        "Orion":      (20, 30),   # d3-celestial has 24 segments
        "Auriga":     (8,  16),
        "Taurus":     (8,  16),
    }
    for name, (lo, hi) in checks.items():
        assert name in by_name, f"{name} missing from output"
        n = len(by_name[name]["ls"])
        assert lo <= n <= hi, f"{name}: expected {lo}–{hi} segments, got {n}"
        print(f"  {name}: {n} segments — OK")
    print(f"  PASS")


def test_perseus_no_stray_line():
    _section("6. Perseus: no segment with az-span >25° (the phi-Per zenith bug)")
    data = _get_data()
    per = next((c for c in data if c["n"] == "Perseus"), None)
    assert per is not None, "Perseus not found"
    bad = [s for s in per["ls"] if abs(s[0] - s[2]) > 25]
    if bad:
        for s in bad:
            print(f"  FAIL: {s}  span={abs(s[0]-s[2]):.1f}°")
        raise AssertionError(f"Perseus has {len(bad)} wide-span segment(s)")
    max_span = max(abs(s[0] - s[2]) for s in per["ls"])
    print(f"  max az-span in Perseus: {max_span:.1f}° — PASS")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_no_near_zenith_endpoints()
    test_no_high_alt_wide_az_segments()
    test_no_raw_wrap_segments()
    test_label_circular_mean()
    test_known_constellations_present()
    test_perseus_no_stray_line()
    print("\n✓ All constellation tests passed\n")
