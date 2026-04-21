function transform(input) {
  var d = (input.data) || {};
  return {
    sky:     d.sky     || {},
    moon:    d.moon    || {},
    clouds:  d.clouds  || {},
    planets: d.planets || [],
    viewing: d.viewing || {},
  };
}
