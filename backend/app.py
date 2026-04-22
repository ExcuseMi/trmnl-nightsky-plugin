import asyncio, io, logging, os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import redis.asyncio as aioredis
from quart import Quart, jsonify, request, Response
from modules.utils.ip_whitelist import init_ip_whitelist, require_trmnl_ip, trmnl_ip_allowed
from modules.providers.sky import (
    build_sky_data, geocode,
    _compute_moon, _generate_sky_chart, _compute_sun,
    get_astronomical_dusk,
)
from modules.providers.light_pollution import init_light_pollution, lookup_bortle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Quart(__name__)

REDIS_URL      = os.getenv('REDIS_URL', 'redis://redis:6379')
CHART_CACHE_TTL = 300  # seconds — matches the 5-minute snap interval
_redis: "aioredis.Redis | None" = None


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
    global _redis
    await init_ip_whitelist()
    await init_light_pollution()
    try:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=False)
        await _redis.ping()
        log.info('Redis connected at %s', REDIS_URL)
    except Exception:
        log.warning('Redis unavailable — chart caching disabled')


@app.route('/health')
async def health():
    return jsonify({'ok': True})


@app.route('/chart')
async def chart():
    lat = request.args.get('lat', '51.5')
    lon = request.args.get('lon', '-0.1')
    tz  = request.args.get('tz', 'UTC')
    w   = int(request.args.get('w', '800').lstrip('#') or 800)
    h   = int(request.args.get('h', '480').lstrip('#') or 480)
    hide_sun = request.args.get('hide_sun', 'false').lower() == 'true'

    if not await trmnl_ip_allowed():
        return _black_png(w, h)

    # Use the epoch timestamp supplied by /data so chart and constellation SVG
    # share the exact same LST reference. Fall back to 5-minute snap if absent.
    t_param = request.args.get('t')
    if t_param:
        epoch = datetime.fromtimestamp(int(t_param), timezone.utc)
    else:
        now   = datetime.now(timezone.utc)
        epoch = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)

    cache_key = f"{lat}|{lon}|{tz}|{w}|{h}|{int(epoch.timestamp())}|{hide_sun}"

    png = None
    if _redis:
        try:
            png = await _redis.get(f'chart:{cache_key}')
            if png:
                log.info('chart cache hit for %s', cache_key[:40])
        except Exception:
            log.warning('Redis get failed', exc_info=True)

    if png is None:
        try:
            moon, _ = _compute_moon(lat, lon, tz, epoch=epoch)
            sun     = _compute_sun(lat, lon, tz, epoch=epoch)
            sun_data = {'alt': sun['alt'], 'az': sun['az']} if not hide_sun else None
            png      = _generate_sky_chart(lat, lon, moon, w, h, epoch=epoch,
                                           sun_data=sun_data)
        except Exception:
            log.exception('chart generation failed')
            return Response(status=500)
        if _redis:
            try:
                await _redis.setex(f'chart:{cache_key}', CHART_CACHE_TTL, png)
                log.info('chart cached for %s', cache_key[:40])
            except Exception:
                log.warning('Redis set failed', exc_info=True)

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
    constellations  = request.args.get('constellations', 'names').lstrip('#').lower()
    if constellations in ('yes', 'true'):  constellations = 'names'
    if constellations in ('no',  'false'): constellations = 'hide'
    if constellations not in ('names', 'lines', 'hide'): constellations = 'names'
    daytime_mode = request.args.get('daytime_mode', 'ignore').lstrip('#').lower()

    try:
        location_name = None
        if location:
            lat, lon, full_name = await geocode(location)
            if lat is None:
                return jsonify({'error': f'Could not geocode: {location}'}), 400
            # Simplify display name: take first part (usually city) or first two if first is very short
            if full_name:
                parts = [p.strip() for p in full_name.split(',')]
                location_name = parts[0]
        elif not lat or not lon:
            lat, lon = '51.5', '-0.1'

        bortle     = lookup_bortle(float(lat), float(lon))
        bortle_str = str(bortle) if bortle else '5'

        now  = datetime.now(timezone.utc)
        snap = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)

        # Daytime handling
        sun_now = _compute_sun(lat, lon, tz)
        if sun_now['is_day']:
            if daytime_mode == 'skip':
                return jsonify({'TRMNL_SKIP_DISPLAY': True})
            if daytime_mode == 'earliest_night':
                dusk = get_astronomical_dusk(lat, lon, now)
                # Snap dusk to nearest 5 mins for better caching
                snap = dusk.replace(minute=(dusk.minute // 5) * 5, second=0, microsecond=0)

        payload = await build_sky_data(lat, lon, bortle_str, tz, constellations, snap, location_name, w=w, h=h)

        # Build chart URL — prefer BASE_URL env var (proxy strips path prefix)
        base_url  = os.getenv('BASE_URL', '').rstrip('/') or \
                    str(request.url).split('?')[0].rsplit('/', 1)[0]
        
        # 1:1 scale: 90 degrees altitude = H height => 360 degrees azimuth = H * 4 width
        ch_val = int(h)
        cw_val = ch_val * 4
        
        chart_params = {
            'lat': lat, 'lon': lon, 'tz': tz, 'w': cw_val, 'h': ch_val,
            't': int(snap.timestamp()),
        }
        if daytime_mode == 'ignore':
            chart_params['hide_sun'] = 'true'

        chart_url = base_url + '/chart?' + urlencode(chart_params)
        payload['sky']['chart']   = chart_url
        payload['sky']['chart_w'] = cw_val
        payload['sky']['chart_h'] = ch_val

        return jsonify(payload)
    except Exception as exc:
        log.exception('build_sky_data failed')
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
