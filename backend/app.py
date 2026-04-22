import asyncio, io, logging, os
from datetime import datetime, timezone
from urllib.parse import urlencode
from quart import Quart, jsonify, request, Response
from modules.utils.ip_whitelist import init_ip_whitelist, require_trmnl_ip, trmnl_ip_allowed
from modules.providers.sky import (
    build_sky_data, geocode,
    _compute_moon, _generate_sky_chart,
)
from modules.providers.light_pollution import init_light_pollution, lookup_bortle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Quart(__name__)

# In-process chart cache: key → png_bytes
_chart_cache: dict = {}


def _black_png(w: int, h: int) -> Response:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor="black")
    plt.close(fig)
    buf.seek(0)
    return Response(buf.read(), mimetype='image/png',
                    headers={'Cache-Control': 'no-store'})


@app.before_serving
async def _startup():
    await init_ip_whitelist()
    await init_light_pollution()


@app.route('/health')
async def health():
    return jsonify({'ok': True})


@app.route('/chart')
async def chart():
    lat            = request.args.get('lat', '51.5')
    lon            = request.args.get('lon', '-0.1')
    tz             = request.args.get('tz', 'UTC')
    w              = int(request.args.get('w', '800').lstrip('#') or 800)
    h              = int(request.args.get('h', '480').lstrip('#') or 480)
    constellations = request.args.get('constellations', 'names').lstrip('#').lower()
    # normalise legacy boolean values from old plugin versions
    if constellations in ('yes', 'true'):   constellations = 'names'
    if constellations in ('no',  'false'):  constellations = 'hide'
    if constellations not in ('names', 'lines', 'hide'): constellations = 'names'

    if not await trmnl_ip_allowed():
        return _black_png(w, h)

    now       = datetime.now(timezone.utc)
    utc_hr    = now.replace(minute=0, second=0, microsecond=0)
    cache_key = f"{lat}|{lon}|{tz}|{w}|{h}|{utc_hr.isoformat()}"

    if cache_key in _chart_cache:
        png = _chart_cache[cache_key]
        log.info('chart cache hit for %s', cache_key[:40])
    else:
        try:
            moon, _ = _compute_moon(lat, lon, tz)
            png     = _generate_sky_chart(lat, lon, moon, w, h, epoch=utc_hr)
            stale = [k for k in _chart_cache if not k.endswith(utc_hr.isoformat())]
            for k in stale:
                del _chart_cache[k]
            _chart_cache[cache_key] = png
        except Exception:
            log.exception('chart generation failed')
            return Response(status=500)

    return Response(png, mimetype='image/png', headers={
        'Cache-Control': 'no-cache',
    })


@app.route('/data')
@require_trmnl_ip
async def data():
    location      = request.args.get('location', '').strip()
    lat           = request.args.get('lat', '').strip()
    lon           = request.args.get('lon', '').strip()
    tz            = request.args.get('tz', 'UTC')
    w             = request.args.get('w', '800').lstrip('#') or '800'
    h             = request.args.get('h', '480').lstrip('#') or '480'
    constellations = request.args.get('constellations', 'names').lstrip('#').lower()
    if constellations in ('yes', 'true'):  constellations = 'names'
    if constellations in ('no',  'false'): constellations = 'hide'
    if constellations not in ('names', 'lines', 'hide'): constellations = 'names'

    try:
        if location:
            lat, lon = await geocode(location)
            if lat is None:
                return jsonify({'error': f'Could not geocode: {location}'}), 400
        elif not lat or not lon:
            lat, lon = '51.5', '-0.1'

        bortle     = lookup_bortle(float(lat), float(lon))
        bortle_str = str(bortle) if bortle else '5'

        utc_hr  = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        payload = await build_sky_data(lat, lon, bortle_str, tz, constellations, utc_hr)

        # Build chart URL — prefer BASE_URL env var (proxy strips path prefix)
        base_url  = os.getenv('BASE_URL', '').rstrip('/') or \
                    str(request.url).split('?')[0].rsplit('/', 1)[0]
        chart_url = base_url + '/chart?' + urlencode({
            'lat': lat, 'lon': lon, 'tz': tz, 'w': w, 'h': h,
        })
        payload['sky']['chart'] = chart_url

        return jsonify(payload)
    except Exception as exc:
        log.exception('build_sky_data failed')
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
