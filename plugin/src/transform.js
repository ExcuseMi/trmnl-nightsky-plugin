function transform(input) {
  var d = input || {};
  var out = {
    data: {
      sky:            d.sky            || {},
      sun:            d.sun            || {},
      moon:           d.moon           || {},
      forecast:       d.forecast       || { now: {} },
      planets:        d.planets        || [],
      constellations: d.constellations || [],
      viewing:        d.viewing        || {},
    }
  };
  if (d.TRMNL_SKIP_DISPLAY) out.TRMNL_SKIP_DISPLAY = true;
  return out;
}
