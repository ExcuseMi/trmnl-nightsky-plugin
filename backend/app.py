import asyncio, logging
from quart import Quart, jsonify, request
from modules.utils.ip_whitelist import init_ip_whitelist, require_trmnl_ip
from modules.providers.sky import build_sky_data, geocode
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


@app.route('/data')
@require_trmnl_ip
async def data():
    location = request.args.get('location', '').strip()
    lat      = request.args.get('lat', '').strip()
    lon      = request.args.get('lon', '').strip()
    tz       = request.args.get('tz', 'UTC')
    w        = int(request.args.get('w', 800))
    h        = int(request.args.get('h', 480))

    try:
        if location:
            lat, lon = await geocode(location)
            if lat is None:
                return jsonify({'error': f'Could not geocode: {location}'}), 400
        elif not lat or not lon:
            lat, lon = '51.5', '-0.1'

        bortle = lookup_bortle(float(lat), float(lon))
        bortle_str = str(bortle) if bortle else '5'

        payload = await build_sky_data(lat, lon, bortle_str, tz, w, h)
        return jsonify(payload)
    except Exception as exc:
        log.exception('build_sky_data failed')
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
