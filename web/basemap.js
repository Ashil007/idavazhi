/* The base map: quiet land and roads with few labels, so the trip's own
 * colours stand out. It is light or dark with the rest of the app (theme.js):
 * a warm paper map by day, a warm near-black one at night. The button under
 * the zoom buttons switches the whole app, map and panel together.
 *
 * Everything the map draws comes from this app's own server - no outside
 * map service:
 *   - vector tiles of Kerala in the OpenMapTiles layout, which
 *     tools/build_map.py makes from an OpenStreetMap extract (/tiles/...);
 *   - the sea, which OpenStreetMap keeps only as a coastline, built into
 *     /map/sea.geojson by tools/build_sea.py;
 *   - main roads, cities and state borders around Kerala, from Natural
 *     Earth (public domain), in /map/region.geojson (tools/build_region.py);
 *   - the letters for labels (/fonts/, Noto Sans).
 * The Kerala map data is OpenStreetMap's, so its credit is shown when the
 * map opens; it then folds into an (i) button, as the OpenStreetMap
 * Foundation's attribution guidelines allow.
 */
"use strict";

var BASEMAP = (function () {
  var FONT_R = ["Noto Sans Regular"], FONT_M = ["Noto Sans Medium"];
  // English names first: Kerala's main "name" is often Malayalam, which the
  // fonts here do not carry.
  var NAME = ["coalesce", ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
  var PALETTES = {
    // Tinted toward the app's cream and warm near-black (style.css).
    light: {
      land: "#f6f2e9", water: "#cde2ee", park: "#e1edd8", wood: "#e6eddc",
      building: "#ece6da", road: "#ffffff", casing: "#e3dccb",
      major: "#fde9b0", majorCasing: "#eccf85", rail: "#cbc3b3",
      text: "#3d3a33", textSoft: "#7d776a", halo: "#fffdf8", boundary: "#c4bba6",
      station: "#6c5dd3"
    },
    dark: {
      land: "#15140f", water: "#1c2124", park: "#171b14", wood: "#151812",
      building: "#1e1c17", road: "#35312a", casing: "#1a1813",
      major: "#595243", majorCasing: "#25221b", rail: "#4a443a",
      text: "#a39b8b", textSoft: "#746d5f", halo: "#0f0e0b", boundary: "#3a352b",
      station: "#b4a8ff"
    }
  };
  function pageTheme() { return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light"; }
  var theme = pageTheme();
  var C = PALETTES[theme];
  var ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
  // The area the map covers (tools/build_map.py), as [[south, west], [north, east]].
  var AREA = [[8.15, 74.80], [12.85, 77.45]];
  // How far the view may go: the neighbouring states (region.geojson and
  // the sea reach 8 degrees out, past what any screen shows from here).
  var VIEW = [[AREA[0][0] - 6, AREA[0][1] - 6], [AREA[1][0] + 6, AREA[1][1] + 6]];
  var CREDIT_FOLD_MS = 5000;
  // Every bus stop, bus stand, railway and metro station (tools/build_transit.py),
  // drawn only while the rider has "All stops" switched on.
  var STOPS_KEY = "vazhi-all-stops";
  var stopsOn = false;
  try { stopsOn = localStorage.getItem(STOPS_KEY) === "1"; } catch (e) { /* private window */ }
  var STOP_COLORS = { bus: "#22a06b", stand: "#22a06b", rail: "#e8833a", metro: "#8b5cf6" };
  var EMPTY = { type: "FeatureCollection", features: [] };

  // Dots for every stop, then names as the map zooms in: stations first,
  // bus stands next, bus stops last (there are 14,000 of them).
  function stopLayers() {
    var T = "transit";
    function kindIs(k) { return ["==", ["get", "k"], k]; }
    function dot(id, filter, minzoom, color, r0, r1, ring) {
      return { id: id, type: "circle", source: T, minzoom: minzoom, filter: filter,
               paint: { "circle-color": color,
                        "circle-radius": ["interpolate", ["linear"], ["zoom"], minzoom, r0, 16, r1],
                        "circle-stroke-color": C.halo,
                        "circle-stroke-width": ring } };
    }
    function label(id, filter, minzoom, size, color, below) {
      return { id: id, type: "symbol", source: T, minzoom: minzoom, filter: filter,
               layout: { "text-field": ["coalesce", ["get", "n"], ""], "text-font": FONT_M, "text-size": size,
                         "text-offset": [0, below || 0.9], "text-anchor": "top", "text-optional": true },
               paint: { "text-color": color, "text-halo-color": C.halo, "text-halo-width": 1.5 } };
    }
    var station = ["match", ["get", "k"], ["rail", "metro"], true, false];
    var stationColor = ["match", ["get", "k"], "metro", STOP_COLORS.metro, STOP_COLORS.rail];
    return [
      dot("stops-bus", kindIs("bus"), 10, STOP_COLORS.bus, 1.4, 5,
          ["interpolate", ["linear"], ["zoom"], 12, 0, 14, 1]),
      // Bus stands: a square badge with a bus in it, not a dot.
      { id: "stops-stand", type: "symbol", source: T, minzoom: 8, filter: kindIs("stand"),
        layout: { "icon-image": "bus-stand", "icon-allow-overlap": true,
                  "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 13, 0.8, 16, 1] } },
      dot("stops-station", station, 6, stationColor, 3, 9, 2),
      label("stops-bus-name", kindIs("bus"), 15, 11, C.textSoft),
      label("stops-stand-name", kindIs("stand"), 12, 12, C.text, 1.3),
      label("stops-station-name", station, 9, 12, stationColor)
    ];
  }

  function roadWidth(minor, major) {
    return ["interpolate", ["exponential", 1.6], ["zoom"], 8, minor * 0.3, 12, minor, 16, major, 19, major * 3];
  }
  var MAJOR = ["match", ["get", "class"], ["motorway", "trunk", "primary"], true, false];
  var MID = ["match", ["get", "class"], ["secondary", "tertiary"], true, false];

  // Casing width by road class. MapLibre allows one zoom curve per rule, so
  // the class choice goes inside each zoom stop, not around three curves.
  function casingWidth() {
    var stops = [["interpolate", ["exponential", 1.6], ["zoom"]]][0];
    [[8, 0.3], [12, 1], [16, null], [19, null]].forEach(function (s) {
      var z = s[0];
      var w = function (minor, major) { return z === 8 ? minor * 0.3 : z === 12 ? minor : z === 16 ? major : major * 3; };
      stops.push(z, ["case", MAJOR, w(3.5, 12), MID, w(2.6, 10), w(1.6, 7)]);
    });
    return stops;
  }

  function style(origin) {
    C = PALETTES[theme];
    var dark = theme === "dark";
    var R = "own";
    return {
      version: 8,
      glyphs: origin + "/fonts/{fontstack}/{range}.pbf",
      sources: {
        own: { type: "vector", tiles: [origin + "/tiles/{z}/{x}/{y}.pbf"], minzoom: 0, maxzoom: 14,
               bounds: [AREA[0][1], AREA[0][0], AREA[1][1], AREA[1][0]] },
        sea: { type: "geojson", data: origin + "/map/sea.geojson" },
        region: { type: "geojson", data: origin + "/map/region.geojson" },
        // The stop list is downloaded only once "All stops" is on.
        transit: { type: "geojson", data: stopsOn ? origin + "/map/transit.geojson" : EMPTY }
      },
      layers: withStops([
        { id: "land", type: "background", paint: { "background-color": C.land } },
        { id: "wood", type: "fill", source: R, "source-layer": "landcover",
          filter: ["match", ["get", "class"], ["wood", "forest", "grass", "wetland"], true, false],
          paint: { "fill-color": C.wood, "fill-opacity": 0.7 } },
        { id: "park", type: "fill", source: R, "source-layer": "park",
          paint: { "fill-color": C.park } },
        { id: "sea", type: "fill", source: "sea", paint: { "fill-color": C.water } },
        { id: "water", type: "fill", source: R, "source-layer": "water",
          paint: { "fill-color": C.water } },
        { id: "waterway", type: "line", source: R, "source-layer": "waterway", minzoom: 10,
          paint: { "line-color": C.water, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 16, 3] } },
        { id: "building", type: "fill", source: R, "source-layer": "building", minzoom: 15,
          paint: { "fill-color": C.building } },
        { id: "boundary", type: "line", source: R, "source-layer": "boundary",
          filter: ["all", ["<=", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
          paint: { "line-color": C.boundary, "line-width": 1, "line-dasharray": [3, 2] } },
        // Around Kerala: state borders, highways, cities (Natural Earth).
        { id: "region-border", type: "line", source: "region",
          filter: ["==", ["get", "kind"], "border"],
          paint: { "line-color": C.boundary, "line-width": 1, "line-dasharray": [3, 2] } },
        { id: "region-road", type: "line", source: "region",
          filter: ["==", ["get", "kind"], "road"],
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": ["case", ["==", ["get", "class"], "major"], C.major, C.road],
                   "line-width": ["interpolate", ["linear"], ["zoom"], 6,
                                  ["case", ["==", ["get", "class"], "major"], 1.2, 0.6], 12,
                                  ["case", ["==", ["get", "class"], "major"], 3.5, 2]] } },
        { id: "rail", type: "line", source: R, "source-layer": "transportation", minzoom: 10,
          filter: ["match", ["get", "class"], ["rail", "transit"], true, false],
          paint: { "line-color": C.rail, "line-width": 1.2, "line-dasharray": [4, 3] } },
        // Roads: a casing under the fill (main roads stand out a little).
        { id: "road-casing", type: "line", source: R, "source-layer": "transportation", minzoom: 11,
          filter: ["match", ["get", "class"], ["motorway", "trunk", "primary", "secondary", "tertiary", "minor"], true, false],
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": ["case", MAJOR, C.majorCasing, C.casing],
                   "line-width": casingWidth() } },
        { id: "road-minor", type: "line", source: R, "source-layer": "transportation", minzoom: 12,
          filter: ["match", ["get", "class"], ["minor", "service"], true, false],
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": C.road, "line-width": roadWidth(1, 5.5) } },
        { id: "road-mid", type: "line", source: R, "source-layer": "transportation", minzoom: 9,
          filter: MID, layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": C.road, "line-width": roadWidth(1.8, 8) } },
        { id: "road-major", type: "line", source: R, "source-layer": "transportation", minzoom: 5,
          filter: MAJOR, layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": C.major, "line-width": roadWidth(2.4, 10) } },
        // Labels: road names close in, stations, places - little else.
        { id: "road-name", type: "symbol", source: R, "source-layer": "transportation_name", minzoom: 14,
          layout: { "symbol-placement": "line", "text-field": NAME, "text-font": FONT_R, "text-size": 11 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.4 } },
        { id: "station-name", type: "symbol", source: R, "source-layer": "poi", minzoom: 13,
          // Railway and metro stations and bus stands - not every bus stop,
          // which would cover the city in labels.
          filter: ["any", ["==", ["get", "class"], "railway"],
                   ["==", ["get", "subclass"], "bus_station"]],
          layout: { "text-field": NAME, "text-font": FONT_M, "text-size": 11, "text-offset": [0, 0.6] },
          paint: { "text-color": C.station, "text-halo-color": C.halo, "text-halo-width": 1.4 } },
        { id: "place-small", type: "symbol", source: R, "source-layer": "place", minzoom: 11,
          filter: ["match", ["get", "class"], ["suburb", "neighbourhood", "quarter", "village", "hamlet"], true, false],
          layout: { "text-field": NAME, "text-font": FONT_R, "text-size": 12 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.5 } },
        // Towns: spaced-out capitals on the dark map when zoomed out.
        { id: "place-town", type: "symbol", source: R, "source-layer": "place", minzoom: 3,
          filter: ["match", ["get", "class"], ["city", "town"], true, false],
          layout: { "text-field": NAME, "text-font": FONT_M,
                    "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 12, 15],
                    "text-transform": dark ? ["step", ["zoom"], "uppercase", 10, "none"] : "none",
                    "text-letter-spacing": dark ? ["step", ["zoom"], 0.08, 10, 0.02] : 0 },
          paint: { "text-color": C.text, "text-halo-color": C.halo, "text-halo-width": 1.6 } },
        { id: "place-state", type: "symbol", source: R, "source-layer": "place", maxzoom: 7,
          filter: ["match", ["get", "class"], ["state", "country"], true, false],
          layout: { "text-field": NAME, "text-font": FONT_R, "text-size": 12, "text-transform": "uppercase",
                    "text-letter-spacing": 0.1 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.2 } },
        // Around Kerala: state names zoomed out, then cities, then towns.
        { id: "region-state", type: "symbol", source: "region", maxzoom: 9,
          filter: ["==", ["get", "kind"], "state"],
          layout: { "text-field": ["get", "name"], "text-font": FONT_R, "text-size": 12,
                    "text-transform": "uppercase", "text-letter-spacing": 0.1 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.2 } },
        { id: "region-town", type: "symbol", source: "region", minzoom: 9,
          filter: ["all", ["==", ["get", "kind"], "place"], [">", ["get", "rank"], 6]],
          layout: { "text-field": ["get", "name"], "text-font": FONT_R, "text-size": 12 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.5 } },
        { id: "region-city", type: "symbol", source: "region", minzoom: 5,
          filter: ["all", ["==", ["get", "kind"], "place"], ["<=", ["get", "rank"], 6]],
          layout: { "text-field": ["get", "name"], "text-font": FONT_M,
                    "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 12, 15],
                    "symbol-sort-key": ["-", 0, ["get", "pop"]],
                    "text-transform": dark ? ["step", ["zoom"], "uppercase", 10, "none"] : "none",
                    "text-letter-spacing": dark ? ["step", ["zoom"], 0.08, 10, 0.02] : 0 },
          paint: { "text-color": C.text, "text-halo-color": C.halo, "text-halo-width": 1.6 } }
      ])
    };
  }

  // The bus-stand badge: a green rounded square with a white bus, drawn
  // here so it needs no image file. 26 px, sharp on high-density screens.
  function busStandIcon() {
    var r = 2, s = 26 * r, c = document.createElement("canvas");
    c.width = c.height = s;
    var g = c.getContext("2d");
    function rounded(x, y, w, h, rad) {
      g.beginPath();
      g.moveTo(x + rad, y); g.arcTo(x + w, y, x + w, y + h, rad); g.arcTo(x + w, y + h, x, y + h, rad);
      g.arcTo(x, y + h, x, y, rad); g.arcTo(x, y, x + w, y, rad); g.closePath();
    }
    rounded(1 * r, 1 * r, 24 * r, 24 * r, 6 * r);           // white edge
    g.fillStyle = "#ffffff"; g.fill();
    rounded(3 * r, 3 * r, 20 * r, 20 * r, 4.5 * r);         // green badge
    g.fillStyle = STOP_COLORS.stand; g.fill();
    g.fillStyle = "#ffffff";                                // the bus
    rounded(7.5 * r, 6.5 * r, 11 * r, 11 * r, 2 * r); g.fill();
    g.fillRect(8.5 * r, 17 * r, 2.5 * r, 2.5 * r);           // wheels
    g.fillRect(15 * r, 17 * r, 2.5 * r, 2.5 * r);
    g.fillStyle = STOP_COLORS.stand;
    g.fillRect(9 * r, 8.5 * r, 8 * r, 4 * r);               // windscreen
    g.beginPath(); g.arc(10 * r, 15 * r, 1 * r, 0, 7); g.fill();   // lights
    g.beginPath(); g.arc(16 * r, 15 * r, 1 * r, 0, 7); g.fill();
    return { image: g.getImageData(0, 0, s, s), pixelRatio: r };
  }

  // With "All stops" on, the stop layers name the stations; the base map's
  // own station names would print each one twice.
  function withStops(layers) {
    if (!stopsOn) return layers;
    return layers.filter(function (l) { return l.id !== "station-name"; }).concat(stopLayers());
  }

  var layer = null;

  function restyle() {
    layer.getMaplibreMap().setStyle(style(window.location.origin));
  }

  // The "All stops" switch. The stop list (1.8 MB, about 220 KB gzipped) is
  // downloaded the first time it is switched on.
  function showStops(on) {
    stopsOn = !!on;
    try { localStorage.setItem(STOPS_KEY, stopsOn ? "1" : "0"); } catch (e) { /* not remembered */ }
    if (layer) restyle();
  }

  var SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  var SUN = SVG + '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4' +
            'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var MOON = SVG + '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></svg>';

  // The light / dark switch, under the zoom buttons.
  function themeControl(map) {
    var Ctl = L.Control.extend({
      options: { position: "topleft" },
      onAdd: function () {
        var box = L.DomUtil.create("div", "leaflet-bar map-theme");
        var btn = L.DomUtil.create("a", "", box);
        btn.href = "#";
        btn.setAttribute("role", "button");
        function show() {
          btn.innerHTML = theme === "dark" ? SUN : MOON;
          btn.title = theme === "dark" ? "Light mode" : "Dark mode";
          btn.setAttribute("aria-label", "Switch to " + btn.title.toLowerCase());
          document.documentElement.setAttribute("data-map-theme", theme);
        }
        function follow() {
          if (theme === pageTheme()) return;
          theme = pageTheme();
          show();
          restyle();
        }
        show();
        L.DomEvent.disableClickPropagation(box);
        // The app's one light/dark choice (theme.js): this button changes it,
        // and the map follows wherever it was changed.
        document.addEventListener("vazhi:theme", follow);
        L.DomEvent.on(btn, "click", function (e) {
          L.DomEvent.preventDefault(e);
          if (window.VAZHI_THEME) window.VAZHI_THEME.toggle();
        });
        return box;
      }
    });
    new Ctl().addTo(map);
  }

  // The OpenStreetMap credit: shown when the map opens, folded into an (i)
  // button after five seconds or when the map is first dragged; tapping the
  // (i) opens it again.
  function foldingCredit(map) {
    // On a phone the bottom sheet covers the lower corners and the mode
    // switch sits top right: under the zoom buttons, then.
    var phone = window.matchMedia("(max-width: 860px)");
    function place() { map.attributionControl.setPosition(phone.matches ? "topleft" : "bottomright"); }
    place();
    if (phone.addEventListener) phone.addEventListener("change", place);
    var el = map.attributionControl.getContainer();
    el.classList.add("credit");
    el.setAttribute("title", "Map credits");
    var timer = null;
    function fold() { el.classList.add("credit--folded"); }
    function later() { clearTimeout(timer); timer = setTimeout(fold, CREDIT_FOLD_MS); }
    later();
    map.once("dragstart", fold);
    L.DomEvent.on(el, "click", function (e) {
      if (!el.classList.contains("credit--folded")) return;   // links work as usual
      L.DomEvent.preventDefault(e);
      el.classList.remove("credit--folded");
      later();
    });
  }

  function init(map) {
    // Kerala in detail, the states around it in outline: keep the view near.
    map.setMaxBounds(L.latLngBounds(VIEW));
    map.options.maxBoundsViscosity = 0.8;
    map.setMinZoom(7);
    if (!L.maplibreGL) {     // the map library failed to load: no base map,
      map.attributionControl.addAttribution(ATTR);   // the trip still draws
      return;
    }
    layer = L.maplibreGL({ style: style(window.location.origin), attribution: ATTR }).addTo(map);
    // Each new style (theme or "All stops" switched) starts without images:
    // hand over the bus-stand badge whenever the map asks for it.
    var gl = layer.getMaplibreMap();
    gl.on("styleimagemissing", function (e) {
      if (e.id !== "bus-stand" || gl.hasImage("bus-stand")) return;
      var icon = busStandIcon();
      gl.addImage("bus-stand", icon.image, { pixelRatio: icon.pixelRatio });
    });
    themeControl(map);
    foldingCredit(map);
  }

  return { init: init, theme: function () { return theme; },
           showStops: showStops, stopsOn: function () { return stopsOn; } };
})();
