import asyncio, logging
from quart import Quart, jsonify, request, Response
from modules.utils.ip_whitelist import init_ip_whitelist, require_trmnl_ip
from modules.providers.sky import (
    build_sky_data, geocode,
    _compute_moon, _get_planets, _generate_sky_chart,
)
from modules.providers.light_pollution import init_light_pollution, lookup_bortle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Quart(__name__)


@app.before_serving
async def _startup():
    await init_ip_whitelist()
    await init_light_pollution()


@app.route('/health')
async def health():
    return jsonify({'ok': True})


@app.route('/chart')
async def chart():
    lat           = request.args.get('lat', '51.5')
    lon           = request.args.get('lon', '-0.1')
    tz            = request.args.get('tz', 'UTC')
    w             = int(request.args.get('w', '800').lstrip('#') or 800)
    h             = int(request.args.get('h', '480').lstrip('#') or 480)
    constellations = request.args.get('constellations', 'yes').lstrip('#') != 'no'

    try:
        moon, _ = _compute_moon(lat, lon, tz)
        planets = _get_planets(lat, lon)
        png     = _generate_sky_chart(lat, lon, moon, planets, w, h, constellations)
        return Response(png, mimetype='image/png',
                        headers={'Cache-Control': 'public, max-age=3600'})
    except Exception:
        log.exception('chart generation failed')
        return Response(status=500)


@app.route('/data')
@require_trmnl_ip
async def data():
    location      = request.args.get('location', '').strip()
    lat           = request.args.get('lat', '').strip()
    lon           = request.args.get('lon', '').strip()
    tz            = request.args.get('tz', 'UTC')
    w             = request.args.get('w', '800').lstrip('#') or '800'
    h             = request.args.get('h', '480').lstrip('#') or '480'
    constellations = request.args.get('constellations', 'yes').lstrip('#')

    try:
        if location:
            lat, lon = await geocode(location)
            if lat is None:
                return jsonify({'error': f'Could not geocode: {location}'}), 400
        elif not lat or not lon:
            lat, lon = '51.5', '-0.1'

        bortle     = lookup_bortle(float(lat), float(lon))
        bortle_str = str(bortle) if bortle else '5'

        payload = await build_sky_data(lat, lon, bortle_str, tz)

        # Build chart URL from this request's own base URL
        base_url  = str(request.url).split('?')[0].rsplit('/', 1)[0]
        chart_url = (
            f"{base_url}/chart"
            f"?lat={lat}&lon={lon}&tz={tz}"
            f"&w={w}&h={h}&constellations={constellations}"
        )
        payload['sky']['chart'] = chart_url

        return jsonify(payload)
    except Exception as exc:
        log.exception('build_sky_data failed')
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
