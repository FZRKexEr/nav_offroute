import 'ol/ol.css';
import './style.css';

import OlMap from 'ol/Map.js';
import View from 'ol/View.js';
import Feature from 'ol/Feature.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import Draw from 'ol/interaction/Draw.js';
import Point from 'ol/geom/Point.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import LineString from 'ol/geom/LineString.js';
import { unByKey } from 'ol/Observable.js';
import { getLength as getGeodesicLength } from 'ol/sphere.js';
import { fromLonLat } from 'ol/proj.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import { defaults as defaultControls } from 'ol/control/defaults.js';
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style, Text } from 'ol/style.js';
import { createEmpty, extend as extendExtent, isEmpty } from 'ol/extent.js';

const DATA_PROJECTION = 'EPSG:4326';
const VIEW_PROJECTION = 'EPSG:3857';
const GPS_ON_ROUTE_COLOR = '#0f766e';
const GPS_OFF_ROUTE_COLOR = '#dc2626';
const GPS_TRAIL_ON_ROUTE_COLOR = 'rgba(15, 118, 110, 0.38)';
const GPS_TRAIL_OFF_ROUTE_COLOR = 'rgba(220, 38, 38, 0.44)';
const ALGORITHM_STATE_COLORS = {
  ON_ROUTE: '#2563eb',
  SUSPECT: '#d97706',
  OFF_ROUTE: '#dc2626',
};

const routeSource = new VectorSource();
const routeEndpointSource = new VectorSource();
const gpsTrailSource = new VectorSource();
const gpsSource = new VectorSource();
const algorithmProjectionSource = new VectorSource();
const algorithmConnectorSource = new VectorSource();
const measureSource = new VectorSource();
const gridSource = new VectorSource({ wrapX: false });
const geojson = new GeoJSON();
const routeEndpointStyleCache = new Map();
const gpsPointStyleCache = new Map();
const gpsTrailStyleCache = new Map();
const algorithmProjectionStyleCache = new Map();
const algorithmConnectorStyleCache = new Map();
const measureStyleCache = new Map();

const gridMinorStyle = new Style({
  stroke: new Stroke({
    color: 'rgba(0, 0, 0, 0.08)',
    width: 1,
  }),
});

const gridMajorStyle = new Style({
  stroke: new Stroke({
    color: 'rgba(0, 0, 0, 0.16)',
    width: 1.2,
  }),
});

const routeStyle = new Style({
  stroke: new Stroke({
    color: '#111111',
    width: 3.5,
    lineCap: 'round',
    lineJoin: 'round',
  }),
});

function getRouteEndpointStyle(feature) {
  const label = String(feature.get('endpointLabel') ?? '');

  if (!routeEndpointStyleCache.has(label)) {
    routeEndpointStyleCache.set(
      label,
      new Style({
        image: new CircleStyle({
          radius: 5.5,
          fill: new Fill({ color: '#111111' }),
          stroke: new Stroke({ color: '#ffffff', width: 1.6 }),
        }),
        text: new Text({
          text: label,
          offsetY: -15,
          font: '600 12px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          fill: new Fill({ color: '#111111' }),
          stroke: new Stroke({ color: '#ffffff', width: 3 }),
        }),
        zIndex: 8,
      }),
    );
  }

  return routeEndpointStyleCache.get(label);
}

const measureLineBase = {
  strokeColor: '#f59e0b',
  fillColor: '#111111',
  haloColor: '#ffffff',
};

function isOffRouteValue(value) {
  return value === true || value === 1 || value === 'true';
}

function getGpsPointStyle(feature) {
  const isOffRoute = isOffRouteValue(feature.get('gtOffRoute'));
  const isEndpoint = Boolean(feature.get('__endpoint'));
  const endpointLabel = String(feature.get('__endpointLabel') ?? '');
  const key = [isOffRoute ? 'off' : 'on', isEndpoint ? 'end' : 'mid', endpointLabel].join('|');

  if (!gpsPointStyleCache.has(key)) {
    gpsPointStyleCache.set(
      key,
      new Style({
        image: new CircleStyle({
          radius: isEndpoint ? 6.5 : 5.5,
          fill: new Fill({ color: isOffRoute ? GPS_OFF_ROUTE_COLOR : GPS_ON_ROUTE_COLOR }),
          stroke: new Stroke({
            color: isEndpoint ? '#111111' : '#ffffff',
            width: isEndpoint ? 2 : 1.6,
          }),
        }),
        text: endpointLabel
          ? new Text({
              text: endpointLabel,
              offsetY: -14,
              font: '600 12px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
              fill: new Fill({ color: '#111111' }),
              stroke: new Stroke({ color: '#ffffff', width: 3 }),
            })
          : undefined,
        zIndex: 10,
      }),
    );
  }

  return gpsPointStyleCache.get(key);
}

function getGpsTrailStyle(feature) {
  const isOffRoute = Boolean(feature.get('segmentOffRoute'));
  const key = isOffRoute ? 'off' : 'on';

  if (!gpsTrailStyleCache.has(key)) {
    gpsTrailStyleCache.set(
      key,
      new Style({
        stroke: new Stroke({
          color: isOffRoute ? GPS_TRAIL_OFF_ROUTE_COLOR : GPS_TRAIL_ON_ROUTE_COLOR,
          width: 2.5,
          lineCap: 'round',
          lineJoin: 'round',
        }),
        zIndex: 5,
      }),
    );
  }

  return gpsTrailStyleCache.get(key);
}

function getAlgorithmState(value) {
  const stateValue = String(value ?? '').toUpperCase();
  return stateValue && ALGORITHM_STATE_COLORS[stateValue] ? stateValue : 'ON_ROUTE';
}

function getAlgorithmColor(value) {
  return ALGORITHM_STATE_COLORS[getAlgorithmState(value)];
}

function getAlgorithmProjectionStyle(feature) {
  const stateValue = getAlgorithmState(feature.get('algoState'));
  const key = stateValue;

  if (!algorithmProjectionStyleCache.has(key)) {
    algorithmProjectionStyleCache.set(
      key,
      new Style({
        image: new RegularShape({
          points: 4,
          radius: stateValue === 'OFF_ROUTE' ? 6 : 5,
          angle: Math.PI / 4,
          fill: new Fill({ color: getAlgorithmColor(stateValue) }),
          stroke: new Stroke({ color: '#ffffff', width: 1.4 }),
        }),
        zIndex: 9,
      }),
    );
  }

  return algorithmProjectionStyleCache.get(key);
}

function getAlgorithmConnectorStyle(feature) {
  const stateValue = getAlgorithmState(feature.get('algoState'));
  const key = stateValue;

  if (!algorithmConnectorStyleCache.has(key)) {
    algorithmConnectorStyleCache.set(
      key,
      new Style({
        stroke: new Stroke({
          color:
            stateValue === 'ON_ROUTE'
              ? 'rgba(37, 99, 235, 0.34)'
              : stateValue === 'SUSPECT'
                ? 'rgba(217, 119, 6, 0.56)'
                : 'rgba(220, 38, 38, 0.62)',
          width: stateValue === 'OFF_ROUTE' ? 2.4 : 1.8,
          lineDash: [6, 4],
          lineCap: 'round',
          lineJoin: 'round',
        }),
        zIndex: 6,
      }),
    );
  }

  return algorithmConnectorStyleCache.get(key);
}

function getMeasureStyle(feature) {
  const label = String(feature.get('label') ?? '');
  const key = label || '__empty__';

  if (!measureStyleCache.has(key)) {
    measureStyleCache.set(
      key,
      new Style({
        stroke: new Stroke({
          color: measureLineBase.strokeColor,
          width: 2.5,
          lineCap: 'round',
          lineJoin: 'round',
        }),
        text: new Text({
          text: label,
          placement: 'line',
          overflow: true,
          font: '600 12px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          fill: new Fill({ color: measureLineBase.fillColor }),
          stroke: new Stroke({ color: measureLineBase.haloColor, width: 4 }),
        }),
      }),
    );
  }

  return measureStyleCache.get(key);
}

const gpsTrailLayer = new VectorLayer({
  source: gpsTrailSource,
  style: getGpsTrailStyle,
});

const algorithmConnectorLayer = new VectorLayer({
  source: algorithmConnectorSource,
  style: getAlgorithmConnectorStyle,
});

const algorithmProjectionLayer = new VectorLayer({
  source: algorithmProjectionSource,
  style: getAlgorithmProjectionStyle,
});

const measureLayer = new VectorLayer({
  source: measureSource,
  style: getMeasureStyle,
});

const measureDrawStyle = new Style({
  stroke: new Stroke({
    color: measureLineBase.strokeColor,
    width: 2.5,
    lineCap: 'round',
    lineJoin: 'round',
  }),
  image: new CircleStyle({
    radius: 5,
    fill: new Fill({ color: measureLineBase.strokeColor }),
    stroke: new Stroke({ color: '#ffffff', width: 1.5 }),
  }),
});

const gridLayer = new VectorLayer({
  source: gridSource,
  style: (feature) => (feature.get('major') ? gridMajorStyle : gridMinorStyle),
});

const routeLayer = new VectorLayer({
  source: routeSource,
  style: routeStyle,
});

const routeEndpointLayer = new VectorLayer({
  source: routeEndpointSource,
  style: getRouteEndpointStyle,
});

const state = {
  records: [],
  currentIndex: 0,
  fileName: '',
  algorithmFile: null,
  algorithmRunning: false,
  algorithmCache: new Map(),
  currentAlgorithmResultMap: new Map(),
  measureActive: false,
  measureLengthM: null,
  lastMeasureSecondaryActionAt: 0,
};

const gpsPointLayer = new VectorLayer({
  source: gpsSource,
  style: getGpsPointStyle,
});

const map = new OlMap({
  target: 'map',
  layers: [
    gridLayer,
    routeLayer,
    routeEndpointLayer,
    algorithmConnectorLayer,
    gpsTrailLayer,
    algorithmProjectionLayer,
    gpsPointLayer,
    measureLayer,
  ],
  controls: defaultControls({
    attribution: false,
    rotate: false,
    zoom: false,
  }).extend([
    new ScaleLine({
      units: 'metric',
      bar: false,
      text: true,
    }),
  ]),
  view: new View({
    projection: VIEW_PROJECTION,
    center: [0, 0],
    zoom: 2,
  }),
});

const measureDraw = new Draw({
  source: measureSource,
  type: 'LineString',
  stopClick: true,
  style: measureDrawStyle,
});

measureDraw.setActive(false);
map.addInteraction(measureDraw);

const prevButton = document.querySelector('#prevButton');
const nextButton = document.querySelector('#nextButton');
const randomButton = document.querySelector('#randomButton');
const fitButton = document.querySelector('#fitButton');
const lineInput = document.querySelector('#lineInput');
const lineCount = document.querySelector('#lineCount');
const routeToggle = document.querySelector('#routeToggle');
const gpsToggle = document.querySelector('#gpsToggle');
const algorithmToggle = document.querySelector('#algorithmToggle');
const algorithmFileInput = document.querySelector('#algorithmFileInput');
const algorithmFileName = document.querySelector('#algorithmFileName');
const runAlgorithmButton = document.querySelector('#runAlgorithmButton');
const measureButton = document.querySelector('#measureButton');
const measureStatus = document.querySelector('#measureStatus');
const algorithmStatus = document.querySelector('#algorithmStatus');
const fileInput = document.querySelector('#fileInput');
const fileName = document.querySelector('#fileName');
const metaPanel = document.querySelector('#metaPanel');
const detailPanel = document.querySelector('#detailPanel');
const banner = document.querySelector('#banner');
let measureSketchListener = null;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatBool(value) {
  if (value === true) return '是';
  if (value === false) return '否';
  return '-';
}

function formatNumber(value, digits = 1) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

function formatDistance(lengthMeters) {
  if (!Number.isFinite(lengthMeters)) return '-';
  if (lengthMeters >= 1000) {
    return `${(lengthMeters / 1000).toFixed(2)} km`;
  }
  return `${lengthMeters.toFixed(1)} m`;
}

function getNiceStep(targetMeters) {
  const magnitude = 10 ** Math.floor(Math.log10(targetMeters));
  const normalized = targetMeters / magnitude;

  if (normalized <= 1) return magnitude;
  if (normalized <= 2) return magnitude * 2;
  if (normalized <= 5) return magnitude * 5;
  return magnitude * 10;
}

function setBanner(message = '', tone = 'info') {
  if (!message) {
    banner.hidden = true;
    banner.textContent = '';
    banner.dataset.tone = '';
    return;
  }
  banner.hidden = false;
  banner.dataset.tone = tone;
  banner.textContent = message;
}

function setMeasureStatus(message) {
  measureStatus.textContent = message;
}

function setAlgorithmStatus(message) {
  algorithmStatus.textContent = message;
}

function formatIndex(value) {
  return Number.isInteger(value) ? String(value + 1) : '-';
}

function getCurrentRecord() {
  return state.records[state.currentIndex] ?? null;
}

function getAlgorithmCacheKey(record) {
  if (!record || !state.algorithmFile) {
    return '';
  }

  return [state.fileName, record.lineNumber, state.algorithmFile.id].join('::');
}

function getCurrentAlgorithmResult() {
  const record = getCurrentRecord();
  const cacheKey = getAlgorithmCacheKey(record);
  return cacheKey ? state.algorithmCache.get(cacheKey) ?? null : null;
}

function formatKeyCoordinate(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(8) : '0.00000000';
}

function buildGpsResultKey(gpsId, timestampMs, lon, lat) {
  return `${gpsId ?? ''}|${Number(timestampMs ?? 0)}|${formatKeyCoordinate(lon)}|${formatKeyCoordinate(lat)}`;
}

function getSourceFeatures(value) {
  if (value?.type === 'FeatureCollection' && Array.isArray(value.features)) {
    return value.features;
  }
  if (value?.type === 'Feature') {
    return [value];
  }
  return [];
}

function updateAlgorithmLayerVisibility() {
  const visible = algorithmToggle.checked && !algorithmToggle.disabled;
  algorithmConnectorLayer.setVisible(visible);
  algorithmProjectionLayer.setVisible(visible);
}

function updateAlgorithmControls() {
  algorithmFileName.textContent = state.algorithmFile?.name ?? '';
  runAlgorithmButton.disabled = !state.algorithmFile || !state.records.length || state.algorithmRunning;
  runAlgorithmButton.textContent = state.algorithmRunning ? '运行中…' : '运行算法';

  const hasCurrentResult = Boolean(getCurrentAlgorithmResult());
  if (hasCurrentResult && algorithmToggle.disabled) {
    algorithmToggle.checked = true;
  }
  algorithmToggle.disabled = !hasCurrentResult;
  if (!hasCurrentResult) {
    algorithmToggle.checked = false;
  }
  updateAlgorithmLayerVisibility();
}

function updateMeasureButtonState() {
  measureButton.classList.toggle('isActive', state.measureActive);
  measureButton.textContent = state.measureActive ? '退出测距' : '测距';
}

function updateMeasureFeature(feature) {
  const geometry = feature.getGeometry();
  if (!geometry) return;

  const lengthMeters = getGeodesicLength(geometry, {
    projection: VIEW_PROJECTION,
  });

  state.measureLengthM = lengthMeters;
  feature.set('label', formatDistance(lengthMeters), true);
  setMeasureStatus(`测距 ${formatDistance(lengthMeters)}`);
}

function clearMeasure() {
  measureDraw.abortDrawing();
  measureSource.clear(true);
  state.measureLengthM = null;

  if (measureSketchListener) {
    unByKey(measureSketchListener);
    measureSketchListener = null;
  }

  setMeasureStatus(state.measureActive ? '左键加点，双击结束，右键清除' : '未测量');
}

function exitMeasureMode() {
  clearMeasure();
  setMeasureMode(false);
}

function setMeasureMode(active) {
  state.measureActive = active;
  measureDraw.setActive(active);
  updateMeasureButtonState();

  if (!active) {
    measureDraw.abortDrawing();
    if (measureSketchListener) {
      unByKey(measureSketchListener);
      measureSketchListener = null;
    }
    setMeasureStatus(state.measureLengthM == null ? '未测量' : `测距 ${formatDistance(state.measureLengthM)}`);
    return;
  }

  setMeasureStatus(state.measureLengthM == null ? '左键加点，双击结束，右键清除' : `测距 ${formatDistance(state.measureLengthM)}`);
}

function hidePointDetails() {
  detailPanel.hidden = true;
  detailPanel.innerHTML = '';
}

function showPointDetails(feature) {
  if (!feature) {
    hidePointDetails();
    return;
  }

  const props = feature.getProperties();
  const algorithmResult = feature.get('__algorithmResult');
  const rows = [
    ['点序号', props.id ?? '-'],
    ['经纬度', props.point ?? '-'],
    ['偏航点', formatBool(isOffRouteValue(props.gtOffRoute))],
    ['可用', formatBool(props.usable)],
    ['置信等级', props.trustedLevel ?? '-'],
    ['速度', formatNumber(Number(props.speed), 2)],
    ['航向', formatNumber(Number(props.heading), 1)],
    ['时间戳', props.timestamp ?? '-'],
  ];

  if (algorithmResult) {
    rows.push(['算法状态', algorithmResult.state ?? '-']);
    rows.push(['算法偏航', formatBool(Boolean(algorithmResult.offRoute))]);
    rows.push(['触发原因', algorithmResult.reason ?? '-']);
    rows.push(['投影距离(m)', formatNumber(Number(algorithmResult.projection?.dist), 2)]);
    rows.push(['投影进度 s(m)', formatNumber(Number(algorithmResult.projection?.s), 2)]);
    rows.push(['端点外推(m)', formatNumber(Number(algorithmResult.projection?.endpointGap), 2)]);
    rows.push(['算法分数', formatNumber(Number(algorithmResult.metrics?.score), 2)]);
  }

  detailPanel.innerHTML = rows
    .map(
      ([label, value]) =>
        `<div class="infoRow"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`,
    )
    .join('');
  detailPanel.hidden = false;
}

function getSequenceValue(feature, fallbackIndex) {
  const numeric = Number(feature.get('id'));
  return Number.isFinite(numeric) ? numeric : fallbackIndex;
}

function getRouteEndpoints(features) {
  for (const feature of features) {
    const geometry = feature.getGeometry();
    if (!geometry) continue;

    if (geometry.getType() === 'LineString') {
      const coordinates = geometry.getCoordinates();
      if (coordinates.length >= 2) {
        return {
          start: coordinates[0],
          end: coordinates[coordinates.length - 1],
        };
      }
    }

    if (geometry.getType() === 'MultiLineString') {
      const lineCoordinates = geometry.getCoordinates().filter((line) => line.length >= 2);
      if (lineCoordinates.length) {
        const firstLine = lineCoordinates[0];
        const lastLine = lineCoordinates[lineCoordinates.length - 1];
        return {
          start: firstLine[0],
          end: lastLine[lastLine.length - 1],
        };
      }
    }
  }

  return null;
}

function getRandomIndex(total, currentIndex) {
  if (total <= 1) return 0;

  let nextIndex = Math.floor(Math.random() * total);
  if (nextIndex === currentIndex) {
    nextIndex = (nextIndex + 1 + Math.floor(Math.random() * (total - 1))) % total;
  }
  return nextIndex;
}

function parseJsonl(text) {
  const records = [];
  const lines = text.split(/\r?\n/);

  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i].trim();
    if (!raw) continue;

    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      throw new Error(`第 ${i + 1} 行不是合法 JSON`);
    }

    if (!parsed.meta || !parsed.route_geojson || !parsed.gps_geojson) {
      throw new Error(`第 ${i + 1} 行缺少必要字段`);
    }

    records.push({
      ...parsed,
      lineNumber: i + 1,
    });
  }

  if (!records.length) {
    throw new Error('文件为空');
  }

  return records;
}

function readGeoJsonFeatures(value) {
  return geojson.readFeatures(value, {
    dataProjection: DATA_PROJECTION,
    featureProjection: VIEW_PROJECTION,
  });
}

function updateUrl(index) {
  const url = new URL(window.location.href);
  url.searchParams.set('line', String(index + 1));
  window.history.replaceState(null, '', url);
}

function applyAlgorithmResultsToGpsFeatures() {
  for (const feature of gpsSource.getFeatures()) {
    const resultKey = feature.get('__resultKey');
    const algorithmResult = resultKey ? state.currentAlgorithmResultMap.get(resultKey) ?? null : null;
    feature.set('__algorithmResult', algorithmResult, true);
  }
}

function renderAlgorithmOverlay(result) {
  algorithmProjectionSource.clear(true);
  algorithmConnectorSource.clear(true);
  state.currentAlgorithmResultMap = new Map();

  if (!result?.outputs?.length) {
    applyAlgorithmResultsToGpsFeatures();
    updateAlgorithmControls();
    return;
  }

  for (const output of result.outputs) {
    state.currentAlgorithmResultMap.set(output.resultKey, output);

    if (!Array.isArray(output.gpsPoint) || !Array.isArray(output.projection?.point)) {
      continue;
    }

    const gpsCoordinate = fromLonLat(output.gpsPoint);
    const projectionCoordinate = fromLonLat(output.projection.point);
    const algoState = getAlgorithmState(output.state);

    algorithmConnectorSource.addFeature(
      new Feature({
        geometry: new LineString([gpsCoordinate, projectionCoordinate]),
        algoState,
        resultKey: output.resultKey,
      }),
    );

    algorithmProjectionSource.addFeature(
      new Feature({
        geometry: new Point(projectionCoordinate),
        algoState,
        resultKey: output.resultKey,
      }),
    );
  }

  applyAlgorithmResultsToGpsFeatures();
  updateAlgorithmControls();
}

function renderMeta(record) {
  if (!record) {
    metaPanel.innerHTML = '';
    return;
  }

  const meta = record.meta ?? {};
  const rows = [
    ['用例', meta.case_id],
    ['分类', meta.category],
    ['设定偏航', formatBool(meta.should_off_route)],
    ['真实偏航点', meta.true_off_idx ?? '-'],
    ['最晚检出点', meta.latest_detect_idx ?? '-'],
    ['采样点数', meta.point_count ?? '-'],
    ['路线长度(m)', formatNumber(meta.route_length_m)],
    ['轨迹长度(m)', formatNumber(meta.actual_length_m)],
  ];

  if (meta.note) {
    rows.push(['备注', meta.note]);
  }

  const algorithmResult = getCurrentAlgorithmResult();
  if (algorithmResult?.summary) {
    rows.push(['算法脚本', algorithmResult.algorithmName ?? state.algorithmFile?.name ?? '-']);
    rows.push(['首次可疑点', formatIndex(algorithmResult.summary.firstSuspectIndex)]);
    rows.push(['首次偏航点', formatIndex(algorithmResult.summary.firstOffIndex)]);
    rows.push(['算法点数', algorithmResult.summary.pointCount ?? '-']);
  }

  metaPanel.innerHTML = rows
    .map(
      ([label, value]) =>
        `<div class="infoRow"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`,
    )
    .join('');
}

function updateGrid() {
  const size = map.getSize();
  const resolution = map.getView().getResolution();

  if (!size || !resolution) {
    return;
  }

  const extent = map.getView().calculateExtent(size);
  const step = getNiceStep(resolution * 96);
  const features = [];

  const startX = Math.floor(extent[0] / step) * step;
  const endX = Math.ceil(extent[2] / step) * step;
  const startY = Math.floor(extent[1] / step) * step;
  const endY = Math.ceil(extent[3] / step) * step;

  for (let x = startX; x <= endX; x += step) {
    const gridIndex = Math.round(x / step);
    features.push(
      new Feature({
        geometry: new LineString([
          [x, startY],
          [x, endY],
        ]),
        major: Math.abs(gridIndex) % 5 === 0,
      }),
    );
  }

  for (let y = startY; y <= endY; y += step) {
    const gridIndex = Math.round(y / step);
    features.push(
      new Feature({
        geometry: new LineString([
          [startX, y],
          [endX, y],
        ]),
        major: Math.abs(gridIndex) % 5 === 0,
      }),
    );
  }

  gridSource.clear(true);
  gridSource.addFeatures(features);
}

function fitToData() {
  const extent = createEmpty();

  if (!routeSource.isEmpty()) {
    extendExtent(extent, routeSource.getExtent());
  }

  if (!gpsSource.isEmpty()) {
    extendExtent(extent, gpsSource.getExtent());
  }

  if (isEmpty(extent)) {
    return;
  }

  map.getView().fit(extent, {
    padding: [64, 64, 64, 64],
    duration: 0,
    maxZoom: 19,
  });
  updateGrid();
}

function updateNavigation() {
  const total = state.records.length;
  const current = total ? state.currentIndex + 1 : 0;

  lineInput.value = current ? String(current) : '';
  lineInput.min = total ? '1' : '0';
  lineInput.max = String(total);
  lineCount.textContent = `/ ${total}`;
  prevButton.disabled = current <= 1;
  nextButton.disabled = current >= total;
  randomButton.disabled = total <= 1;
}

function renderRecord(index) {
  const record = state.records[index];
  if (!record) return;

  state.currentIndex = index;
  exitMeasureMode();

  routeSource.clear(true);
  routeEndpointSource.clear(true);
  gpsTrailSource.clear(true);
  gpsSource.clear(true);
  algorithmProjectionSource.clear(true);
  algorithmConnectorSource.clear(true);
  state.currentAlgorithmResultMap = new Map();
  hidePointDetails();

  const routeFeatures = readGeoJsonFeatures(record.route_geojson);
  const gpsFeatures = readGeoJsonFeatures(record.gps_geojson);
  const sourceGpsFeatures = getSourceFeatures(record.gps_geojson);

  gpsFeatures.forEach((feature, featureIndex) => {
    const sourceFeature = sourceGpsFeatures[featureIndex];
    const sourceProps = sourceFeature?.properties ?? {};
    const sourceCoordinates = Array.isArray(sourceFeature?.geometry?.coordinates) ? sourceFeature.geometry.coordinates : [];
    feature.set(
      '__resultKey',
      buildGpsResultKey(sourceProps.id, sourceProps.timestamp, sourceCoordinates[0], sourceCoordinates[1]),
      true,
    );
  });

  const orderedGps = gpsFeatures
    .map((feature, originalIndex) => ({
      feature,
      order: getSequenceValue(feature, originalIndex),
      isOffRoute: isOffRouteValue(feature.get('gtOffRoute')),
    }))
    .sort((left, right) => left.order - right.order);

  orderedGps.forEach(({ feature }, orderedIndex) => {
    const isStart = orderedIndex === 0;
    const isEnd = orderedIndex === orderedGps.length - 1;
    feature.set('__endpoint', isStart || isEnd, true);
    feature.set('__endpointLabel', isStart && isEnd ? '起终' : isStart ? '起' : isEnd ? '终' : '', true);
  });

  routeSource.addFeatures(routeFeatures);
  const routeEndpoints = getRouteEndpoints(routeFeatures);
  if (routeEndpoints) {
    routeEndpointSource.addFeatures([
      new Feature({
        geometry: new Point(routeEndpoints.start),
        endpointLabel: '路起',
      }),
      new Feature({
        geometry: new Point(routeEndpoints.end),
        endpointLabel: '路终',
      }),
    ]);
  }
  gpsSource.addFeatures(gpsFeatures);

  for (let index = 1; index < orderedGps.length; index += 1) {
    const previous = orderedGps[index - 1];
    const current = orderedGps[index];
    const previousCoordinate = previous.feature.getGeometry()?.getCoordinates();
    const currentCoordinate = current.feature.getGeometry()?.getCoordinates();

    if (!Array.isArray(previousCoordinate) || !Array.isArray(currentCoordinate)) {
      continue;
    }

    gpsTrailSource.addFeature(
      new Feature({
        geometry: new LineString([previousCoordinate, currentCoordinate]),
        segmentOffRoute: previous.isOffRoute || current.isOffRoute,
      }),
    );
  }

  routeLayer.setVisible(routeToggle.checked);
  routeEndpointLayer.setVisible(routeToggle.checked);
  gpsPointLayer.setVisible(gpsToggle.checked);
  gpsTrailLayer.setVisible(gpsToggle.checked);
  renderAlgorithmOverlay(getCurrentAlgorithmResult());
  if (getCurrentAlgorithmResult()?.summary) {
    setAlgorithmStatus(
      `已运行 ${getCurrentAlgorithmResult().summary.pointCount} 点，首偏航 ${formatIndex(getCurrentAlgorithmResult().summary.firstOffIndex)}`,
    );
  } else if (state.algorithmFile) {
    setAlgorithmStatus('当前记录未运行');
  } else {
    setAlgorithmStatus('算法未运行');
  }
  renderMeta(record);
  updateNavigation();
  updateUrl(index);
  updateAlgorithmControls();
  fitToData();
}

function showIndex(index) {
  if (!state.records.length) return;
  renderRecord(clamp(index, 0, state.records.length - 1));
}

function loadRecords(records, sourceName) {
  state.records = records;
  state.fileName = sourceName;
  fileName.textContent = sourceName;
  const params = new URLSearchParams(window.location.search);
  const line = Number(params.get('line'));
  const startIndex = Number.isFinite(line) && line >= 1 ? clamp(line - 1, 0, records.length - 1) : 0;
  renderRecord(startIndex);
  setBanner('');
  updateAlgorithmControls();
}

async function loadText(text, sourceName) {
  setBanner(`正在读取 ${sourceName}`);
  try {
    const records = parseJsonl(text);
    loadRecords(records, sourceName);
  } catch (error) {
    setBanner(error instanceof Error ? error.message : '读取失败', 'error');
  }
}

async function runCurrentAlgorithm() {
  const record = getCurrentRecord();
  if (!record) {
    setBanner('请先打开 JSONL 文件', 'error');
    return;
  }

  if (!state.algorithmFile) {
    setBanner('请先手动选择 Python 算法文件', 'error');
    return;
  }

  state.algorithmRunning = true;
  setAlgorithmStatus(`正在运行 ${state.algorithmFile.name}`);
  updateAlgorithmControls();
  setBanner(`正在用 ${state.algorithmFile.name} 运行当前记录`);

  try {
    const response = await fetch('/api/run-detector', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        algorithmName: state.algorithmFile.name,
        algorithmSource: state.algorithmFile.source,
        routeGeojson: record.route_geojson,
        gpsGeojson: record.gps_geojson,
      }),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || '算法执行失败');
    }

    state.algorithmCache.set(getAlgorithmCacheKey(record), payload);

    if (getCurrentRecord() === record) {
      renderAlgorithmOverlay(payload);
      renderMeta(record);
      const firstOffIndex = formatIndex(payload.summary?.firstOffIndex);
      setAlgorithmStatus(`已运行 ${payload.summary?.pointCount ?? 0} 点，首偏航 ${firstOffIndex}`);
    }

    setBanner(
      `算法完成：${payload.summary?.pointCount ?? 0} 点，首次可疑 ${formatIndex(payload.summary?.firstSuspectIndex)}，首次偏航 ${formatIndex(payload.summary?.firstOffIndex)}`,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : '算法执行失败';
    setAlgorithmStatus('算法运行失败');
    setBanner(message, 'error');
  } finally {
    state.algorithmRunning = false;
    updateAlgorithmControls();
  }
}

prevButton.addEventListener('click', () => showIndex(state.currentIndex - 1));
nextButton.addEventListener('click', () => showIndex(state.currentIndex + 1));
randomButton.addEventListener('click', () => showIndex(getRandomIndex(state.records.length, state.currentIndex)));
fitButton.addEventListener('click', () => fitToData());

lineInput.addEventListener('change', () => {
  const next = Number(lineInput.value);
  if (!Number.isFinite(next)) {
    updateNavigation();
    return;
  }
  showIndex(next - 1);
});

routeToggle.addEventListener('change', () => {
  routeLayer.setVisible(routeToggle.checked);
  routeEndpointLayer.setVisible(routeToggle.checked);
});

gpsToggle.addEventListener('change', () => {
  gpsPointLayer.setVisible(gpsToggle.checked);
  gpsTrailLayer.setVisible(gpsToggle.checked);
  if (!gpsToggle.checked) {
    hidePointDetails();
  }
});

algorithmToggle.addEventListener('change', () => {
  updateAlgorithmLayerVisibility();
});

measureButton.addEventListener('click', () => {
  if (state.measureActive) {
    exitMeasureMode();
    return;
  }
  setMeasureMode(true);
});

fileInput.addEventListener('change', async (event) => {
  const target = event.target;
  const [file] = target.files ?? [];
  if (!file) return;
  const text = await file.text();
  await loadText(text, file.name);
  target.value = '';
});

algorithmFileInput.addEventListener('change', async (event) => {
  const target = event.target;
  const [file] = target.files ?? [];
  if (!file) return;

  state.algorithmFile = {
    id: `${file.name}:${file.size}:${file.lastModified}`,
    name: file.name,
    source: await file.text(),
  };

  const cachedResult = getCurrentAlgorithmResult();
  if (cachedResult) {
    renderAlgorithmOverlay(cachedResult);
    renderMeta(getCurrentRecord());
    setAlgorithmStatus(`已载入 ${file.name}`);
  } else {
    renderAlgorithmOverlay(null);
    renderMeta(getCurrentRecord());
    setAlgorithmStatus(`已选择 ${file.name}`);
  }

  updateAlgorithmControls();
  setBanner(`已选择算法脚本 ${file.name}，不会默认执行，请手动点击“运行算法”`);
  target.value = '';
});

runAlgorithmButton.addEventListener('click', () => {
  runCurrentAlgorithm();
});

window.addEventListener('keydown', (event) => {
  if (event.target === lineInput) return;
  if (event.key === 'ArrowLeft') {
    event.preventDefault();
    showIndex(state.currentIndex - 1);
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault();
    showIndex(state.currentIndex + 1);
  }
  if (event.key === 'Escape' && state.measureActive) {
    event.preventDefault();
    exitMeasureMode();
  }
});

measureDraw.on('drawstart', (event) => {
  if (measureSource.getFeatures().length) {
    measureSource.clear(true);
  }
  state.measureLengthM = null;
  setMeasureStatus('左键加点，双击结束，右键清除');

  if (measureSketchListener) {
    unByKey(measureSketchListener);
  }

  measureSketchListener = event.feature.getGeometry().on('change', () => {
    updateMeasureFeature(event.feature);
  });
});

measureDraw.on('drawend', (event) => {
  updateMeasureFeature(event.feature);

  if (measureSketchListener) {
    unByKey(measureSketchListener);
    measureSketchListener = null;
  }
});

map.on('moveend', updateGrid);

map.on('singleclick', (event) => {
  if (state.measureActive) {
    return;
  }

  let selectedFeature = null;
  map.forEachFeatureAtPixel(event.pixel, (feature, layer) => {
    if (layer === gpsPointLayer) {
      selectedFeature = feature;
      return true;
    }
    return false;
  });

  showPointDetails(selectedFeature);
});

function handleMeasureSecondaryAction(event) {
  if (!state.measureActive && measureSource.isEmpty()) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (measureSource.isEmpty()) {
    setMeasureMode(false);
    return;
  }
  clearMeasure();
}

map.getViewport().addEventListener('pointerdown', (event) => {
  if (event.button !== 2) {
    return;
  }

  state.lastMeasureSecondaryActionAt = Date.now();
  handleMeasureSecondaryAction(event);
});

map.getViewport().addEventListener('contextmenu', (event) => {
  if (Date.now() - state.lastMeasureSecondaryActionAt < 250) {
    return;
  }

  handleMeasureSecondaryAction(event);
});

updateGrid();
updateMeasureButtonState();
updateNavigation();
setAlgorithmStatus('算法未运行');
updateAlgorithmControls();
setBanner('请先手动选择 JSONL；如果要叠加偏航/投影结果，还需要再手动选择 Python 算法文件');
