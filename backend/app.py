import asyncio, logging
from quart import Quart, jsonify, request
from modules.utils.ip_whitelist import init_ip_whitelist, require_trmnl_ip
from modules.providers.sky import build_sky_data

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Quart(__name__)


@app.before_serving
async def _startup():
    await init_ip_whitelist()


@app.route('/health')
async def health():
    return jsonify({'ok': True})


@app.route('/data')
@require_trmnl_ip
async def data():
    lat     = request.args.get('lat', '51.5')
    lon     = request.args.get('lon', '-0.1')
    bortle  = request.args.get('bortle', '5')
    tz      = request.args.get('tz', 'UTC')

    try:
        payload = await build_sky_data(lat, lon, bortle, tz)
        return jsonify(payload)
    except Exception as exc:
        log.exception('build_sky_data failed')
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
