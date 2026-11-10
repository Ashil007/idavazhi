/* The base map: a night-mode map by default - near-black land, quiet grey
 * roads and a few labels - so the trip's own colours stand out. A switch on
 * the map turns on the plain light map instead; the choice is remembered.
 *
 * One style of our own (below), in two palettes. It draws OpenStreetMap
 * vector tiles in the OpenMapTiles layout from two places:
 *   - OpenFreeMap (free, no key) for the whole world - and always for land,
 *     sea and parks, since the coastline needs data an extract lacks;
 *   - the app's own tiles for Kerala (tools/build_map.py builds them from an
 *     OpenStreetMap extract), used for roads, railways and labels whenever
 *     the view is centred inside Kerala, so the common case is served from
 *     this computer.
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
  var ATTR = '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> ' +
             '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors';

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

  // `roads` is "own" (this computer's Kerala tiles) or "base" (OpenFreeMap).
  function style(roads, origin) {
    C = PALETTES[theme];
    var dark = theme === "dark";
    var sources = { base: { type: "vector", url: "https://tiles.openfreemap.org/planet" } };
    if (roads === "own") {
      sources.own = { type: "vector", tiles: [origin + "/tiles/{z}/{x}/{y}.pbf"], minzoom: 0, maxzoom: 14 };
    }
    var R = roads;
    return {
      version: 8,
      glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
      sources: sources,
      layers: [
        { id: "land", type: "background", paint: { "background-color": C.land } },
        { id: "wood", type: "fill", source: "base", "source-layer": "landcover",
          filter: ["match", ["get", "class"], ["wood", "forest", "grass", "wetland"], true, false],
          paint: { "fill-color": C.wood, "fill-opacity": 0.7 } },
        { id: "park", type: "fill", source: "base", "source-layer": "park",
          paint: { "fill-color": C.park } },
        { id: "water", type: "fill", source: "base", "source-layer": "water",
          paint: { "fill-color": C.water } },
        { id: "waterway", type: "line", source: "base", "source-layer": "waterway", minzoom: 10,
          paint: { "line-color": C.water, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 16, 3] } },
        { id: "building", type: "fill", source: R, "source-layer": "building", minzoom: 15,
          paint: { "fill-color": C.building } },
        { id: "boundary", type: "line", source: "base", "source-layer": "boundary",
          filter: ["all", ["<=", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
          paint: { "line-color": C.boundary, "line-width": 1, "line-dasharray": [3, 2] } },
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
        { id: "place-town", type: "symbol", source: "base", "source-layer": "place", minzoom: 3,
          filter: ["match", ["get", "class"], ["city", "town"], true, false],
          layout: { "text-field": NAME, "text-font": FONT_M,
                    "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 12, 15],
                    "text-transform": dark ? ["step", ["zoom"], "uppercase", 10, "none"] : "none",
                    "text-letter-spacing": dark ? ["step", ["zoom"], 0.08, 10, 0.02] : 0 },
          paint: { "text-color": C.text, "text-halo-color": C.halo, "text-halo-width": 1.6 } },
        { id: "place-state", type: "symbol", source: "base", "source-layer": "place", maxzoom: 7,
          filter: ["match", ["get", "class"], ["state", "country"], true, false],
          layout: { "text-field": NAME, "text-font": FONT_R, "text-size": 12, "text-transform": "uppercase",
                    "text-letter-spacing": 0.1 },
          paint: { "text-color": C.textSoft, "text-halo-color": C.halo, "text-halo-width": 1.2 } }
      ]
    };
  }

  var layer = null, ownBounds = null, using = null;

  function pick(map, force) {
    var want = ownBounds && ownBounds.contains(map.getCenter()) && map.getZoom() >= 8 ? "own" : "base";
    if (want === using && !force) return;
    using = want;
    layer.getMaplibreMap().setStyle(style(want, window.location.origin));
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
          pick(map, true);
        });
        return box;
      }
    });
    new Ctl().addTo(map);
  }

  function init(map) {
    if (!L.maplibreGL) {     // the map library failed to load: plain OSM tiles
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
      return;
    }
    using = "base";
    layer = L.maplibreGL({ style: style("base", window.location.origin), attribution: ATTR }).addTo(map);
    themeControl(map);
    fetch("/api/mapinfo").then(function (r) { return r.json(); }).then(function (info) {
      if (!info.available) return;
      var b = info.bounds;   // [west, south, east, north]
      ownBounds = L.latLngBounds([b[1], b[0]], [b[3], b[2]]);
      map.on("moveend", function () { pick(map); });
      pick(map);
    }).catch(function () { /* OpenFreeMap alone, then */ });
  }

  return { init: init, usingOwnTiles: function () { return using === "own"; },
           theme: function () { return theme; } };
})();
