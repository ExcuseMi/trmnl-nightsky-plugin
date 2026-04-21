function transform(input) {
  var d = input || {};
  return {
    data: {
      sky:      d.sky      || {},
      sun:      d.sun      || {},
      moon:     d.moon     || {},
      forecast: d.forecast || { now: {}, hourly: [] },
      planets:  d.planets  || [],
      viewing:  d.viewing  || {},
    }
  };
}
