function moonPhaseSvg(illumination, phase) {
  var r = 38, c = 40;
  var shape;

  if (illumination <= 2) {
    shape = '<circle cx="' + c + '" cy="' + c + '" r="' + r + '" fill="black" stroke="black" stroke-width="2"/>';
  } else if (illumination >= 98) {
    shape = '<circle cx="' + c + '" cy="' + c + '" r="' + r + '" fill="white" stroke="black" stroke-width="2"/>';
  } else {
    var isWaxing = /Waxing|New Moon|First Quarter/.test(phase);
    var frac = illumination / 100;
    var ex   = Math.round(r * Math.abs(1 - 2 * frac) * 10) / 10;
    var bs   = isWaxing ? 1 : 0;
    var ts   = frac < 0.5 ? bs : (1 - bs);
    var d    = 'M ' + c + ',' + (c-r) + ' A ' + r + ' ' + r + ' 0 0 ' + bs + ' ' + c + ',' + (c+r) +
               ' A ' + ex + ' ' + r + ' 0 0 ' + ts + ' ' + c + ',' + (c-r) + ' Z';
    shape = '<circle cx="' + c + '" cy="' + c + '" r="' + r + '" fill="black" stroke="black" stroke-width="2"/>' +
            '<path d="' + d + '" fill="white"/>';
  }

  var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">' + shape + '</svg>';
  return btoa(svg);
}

function transform(input) {
  var d = input || {};
  var moon = d.moon || {};

  moon.phase_svg = moonPhaseSvg(moon.illumination || 0, moon.phase || '');

  return {
    sky:     d.sky     || {},
    moon:    moon,
    clouds:  d.clouds  || {},
    planets: d.planets || [],
    viewing: d.viewing || {},
  };
}
