import asyncio, io, logging, math, zipfile
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

_CACHE_PATH = Path('/data/viirs_2024.tif')
_VIIRS_URL  = 'https://www2.lightpollutionmap.info/data/v2/viirs_2024.zip'

# rasterio is imported lazily so the rest of the app boots even if it's missing
_dataset  = None
_ready    = False
_lock     = asyncio.Lock()

# Standard Bortle ↔ SQM thresholds
_BORTLE_SQM = [(21.89, 1), (21.69, 2), (21.25, 3), (20.49, 4),
               (19.50, 5), (18.94, 6), (18.38, 7), (17.50, 8)]


def _sqm_to_bortle(sqm: float) -> int:
    for threshold, bortle in _BORTLE_SQM:
        if sqm >= threshold:
            return bortle
    return 9


def _radiance_to_sqm(val: float) -> float:
    """Convert artificial radiance (mcd/m²) to SQM mag/arcsec²."""
    total = max(val, 0.0) + 0.171168465  # add natural sky brightness
    return math.log10(total / 108_000_000) / -0.4


def _rgb_to_bortle(r: float, g: float, b: float) -> int:
    """
    Approximate Bortle from rendered RGB pixel.
    lightpollutionmap.info uses a black→blue→green→yellow→white gradient.
    Luminance maps linearly to SQM 22 (dark) → 17 (bright city).
    This is approximate; calibration can be refined from known sample points.
    """
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    sqm = 22.0 - (lum / 255.0) * 5.0
    return _sqm_to_bortle(sqm)


async def _download():
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info('Downloading VIIRS 2024 light pollution data (~200 MB)…')
    async with aiohttp.ClientSession() as session:
        async with session.get(_VIIRS_URL, timeout=aiohttp.ClientTimeout(total=600)) as r:
            r.raise_for_status()
            zip_bytes = await r.read()
    log.info('Extracting viirs_2024.tif…')
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        with zf.open('viirs_2024.tif') as src, open(_CACHE_PATH, 'wb') as dst:
            dst.write(src.read())
    log.info('VIIRS data saved to %s', _CACHE_PATH)


async def init_light_pollution():
    global _dataset, _ready
    asyncio.create_task(_load())


async def _load():
    global _dataset, _ready
    async with _lock:
        try:
            import rasterio
            if not _CACHE_PATH.exists():
                await _download()
            _dataset = rasterio.open(_CACHE_PATH)
            log.info(
                'VIIRS dataset ready — %d band(s), dtype=%s, crs=%s, size=%dx%d',
                _dataset.count, _dataset.dtypes[0], _dataset.crs,
                _dataset.width, _dataset.height,
            )
            _ready = True
        except Exception as exc:
            log.warning('Light pollution dataset unavailable: %s — Bortle will default to 5', exc)


def lookup_bortle(lat: float, lon: float) -> int | None:
    """Return Bortle 1–9 for the given coordinates, or None if data not ready."""
    if not _ready or _dataset is None:
        return None
    try:
        import rasterio
        from rasterio.transform import rowcol
        from pyproj import Transformer

        # Reproject to the dataset's CRS if needed (likely EPSG:3857)
        if _dataset.crs and _dataset.crs.to_epsg() != 4326:
            tf = Transformer.from_crs('EPSG:4326', _dataset.crs, always_xy=True)
            x, y = tf.transform(lon, lat)
        else:
            x, y = lon, lat

        row, col = rowcol(_dataset.transform, x, y)

        # Clamp to dataset bounds
        row = max(0, min(row, _dataset.height - 1))
        col = max(0, min(col, _dataset.width - 1))

        window = rasterio.windows.Window(col_off=col, row_off=row, width=1, height=1)
        data = _dataset.read(window=window)  # (bands, 1, 1)

        if _dataset.count == 1:
            val = float(data[0, 0, 0])
            sqm = _radiance_to_sqm(val)
            return _sqm_to_bortle(sqm)
        else:
            r, g, b = float(data[0, 0, 0]), float(data[1, 0, 0]), float(data[2, 0, 0])
            return _rgb_to_bortle(r, g, b)

    except Exception as exc:
        log.warning('Bortle pixel lookup failed (%s, %s): %s', lat, lon, exc)
        return None
