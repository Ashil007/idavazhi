/* The base map: a night-mode map by default - near-black land, quiet grey
 * roads and a few labels - so the trip's own colours stand out. A switch on
 * the map turns on the plain light map instead; the choice is remembered.
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
    light: {
      land: "#f4f3ef", water: "#cfe3f1", park: "#e2eedb", wood: "#e7eee0",
      building: "#ebe8e3", road: "#ffffff", casing: "#e1ddd5",
      major: "#fdebb8", majorCasing: "#efd58f", rail: "#cbc6bd",
      text: "#3f4247", textSoft: "#80848b", halo: "#ffffff", boundary: "#bdb7cc",
      station: "#6c5dd3"
    },
    dark: {
      land: "#121316", water: "#1f2227", park: "#151a17", wood: "#141816",
      building: "#1c1e22", road: "#30343b", casing: "#191b1f",
      major: "#4f5560", majorCasing: "#22252a", rail: "#46423d",
      text: "#959aa3", textSoft: "#686d76", halo: "#0d0e10", boundary: "#34373e",
      station: "#b4a8ff"
    }
  };
  var THEME_KEY = "vazhi-map-theme";
  var theme = "dark";
  try { if (localStorage.getItem(THEME_KEY) === "light") theme = "light"; } catch (e) { /* private window */ }
  var C = PALETTES[theme];
  var ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
  // The area the map covers (tools/build_map.py), as [[south, west], [north, east]].
  var AREA = [[8.15, 74.80], [12.85, 77.45]];
  // How far the view may go: the neighbouring states (region.geojson and
  // the sea reach 8 degrees out, past what any screen shows from here).
  var VIEW = [[AREA[0][0] - 6, AREA[0][1] - 6], [AREA[1][0] + 6, AREA[1][1] + 6]];
  var CREDIT_FOLD_MS = 5000;

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
        region: { type: "geojson", data: origin + "/map/region.geojson" }
      },
      layers: [
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
      ]
    };
  }

  var layer = null;

  function restyle() {
    layer.getMaplibreMap().setStyle(style(window.location.origin));
  }

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
          btn.textContent = theme === "dark" ? "☀" : "☾";   // sun / moon
          btn.title = theme === "dark" ? "Light map" : "Dark map";
          btn.setAttribute("aria-label", btn.title);
          document.documentElement.setAttribute("data-map-theme", theme);
        }
        show();
        L.DomEvent.disableClickPropagation(box);
        L.DomEvent.on(btn, "click", function (e) {
          L.DomEvent.preventDefault(e);
          theme = theme === "dark" ? "light" : "dark";
          try { localStorage.setItem(THEME_KEY, theme); } catch (err) { /* not remembered */ }
          show();
          restyle();
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
    // On a phone the bottom sheet covers the lower corners: top right, then.
    var phone = window.matchMedia("(max-width: 860px)");
    function place() { map.attributionControl.setPosition(phone.matches ? "topright" : "bottomright"); }
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
    themeControl(map);
    foldingCredit(map);
  }

  return { init: init, theme: function () { return theme; } };
})();
