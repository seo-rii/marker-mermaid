const LIMIT_NAMES = Object.freeze([
  "maxSvgBytes",
  "maxPngBytes",
  "maxDimension",
  "maxPixels",
  "maxSvgNodes",
  "maxSvgTextChars",
  "maxSvgPaths",
  "maxSvgPathDataChars",
]);

export function validateRenderLimits(rawLimits) {
  if (
    rawLimits === null ||
    typeof rawLimits !== "object" ||
    Array.isArray(rawLimits)
  ) {
    throw new TypeError("render limits must be an object");
  }
  const names = Object.keys(rawLimits).sort();
  const expectedNames = [...LIMIT_NAMES].sort();
  if (
    names.length !== expectedNames.length ||
    names.some((name, index) => name !== expectedNames[index])
  ) {
    throw new TypeError("render limits have an invalid shape");
  }
  const limits = {};
  for (const name of LIMIT_NAMES) {
    const value = rawLimits[name];
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new TypeError(`render limit ${name} must be a positive safe integer`);
    }
    limits[name] = value;
  }
  return Object.freeze(limits);
}

function invalidCount(value) {
  return !Number.isSafeInteger(value) || value < 0;
}

export function staticSvgOmissionReason(stats, limits) {
  if (stats === null || typeof stats !== "object" || Array.isArray(stats)) {
    return "rendered SVG statistics are invalid";
  }
  if (invalidCount(stats.nodeCount)) {
    return "rendered SVG node statistics are invalid";
  }
  if (stats.nodeCount > limits.maxSvgNodes) {
    return "rendered SVG DOM exceeds the node limit";
  }
  if (typeof stats.securityFinding === "string" && stats.securityFinding) {
    return "rendered SVG contains content that cannot be previewed safely";
  }
  if (
    invalidCount(stats.textLength) ||
    invalidCount(stats.pathCount) ||
    invalidCount(stats.pathDataLength)
  ) {
    return "rendered SVG content statistics are invalid";
  }
  if (stats.textLength > limits.maxSvgTextChars) {
    return "rendered SVG text exceeds the character limit";
  }
  if (stats.pathCount > limits.maxSvgPaths) {
    return "rendered SVG exceeds the path limit";
  }
  if (stats.pathDataLength > limits.maxSvgPathDataChars) {
    return "rendered SVG path data exceeds the character limit";
  }
  return null;
}

function geometryIsFinite(geometry, { positiveDimensions }) {
  if (
    geometry === null ||
    typeof geometry !== "object" ||
    Array.isArray(geometry)
  ) {
    return false;
  }
  const values = [geometry.x, geometry.y, geometry.width, geometry.height];
  if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) {
    return false;
  }
  return positiveDimensions
    ? geometry.width > 0 && geometry.height > 0
    : geometry.width >= 0 && geometry.height >= 0;
}

export function geometryOmissionReason(stats, limits) {
  if (
    stats === null ||
    typeof stats !== "object" ||
    Array.isArray(stats) ||
    !geometryIsFinite(stats.rect, { positiveDimensions: true })
  ) {
    return "rendered SVG has invalid layout bounds";
  }
  const optionalGeometry = [stats.viewBox, stats.contentBounds, stats.intrinsicSize];
  for (const geometry of optionalGeometry) {
    if (
      geometry !== null &&
      !geometryIsFinite(geometry, { positiveDimensions: geometry === stats.viewBox })
    ) {
      return "rendered SVG has invalid intrinsic bounds";
    }
  }

  const candidates = [stats.rect, ...optionalGeometry].filter(
    (geometry) => geometry !== null && geometry.width > 0 && geometry.height > 0,
  );
  const width = Math.max(...candidates.map((geometry) => geometry.width));
  const height = Math.max(...candidates.map((geometry) => geometry.height));
  if (!Number.isFinite(width) || !Number.isFinite(height)) {
    return "rendered SVG has non-finite dimensions";
  }
  if (width > limits.maxDimension || height > limits.maxDimension) {
    return "rendered SVG dimensions exceed the preview limit";
  }
  if (height > limits.maxPixels / width) {
    return "rendered SVG pixel area exceeds the preview limit";
  }
  return null;
}

export async function captureBoundedPng(svgLocator, omissionReason, limits) {
  if (omissionReason !== null) {
    return { png: null, pngOmittedReason: omissionReason };
  }
  const png = await svgLocator.screenshot({
    type: "png",
    animations: "disabled",
  });
  if (!Buffer.isBuffer(png)) {
    throw new TypeError("Playwright screenshot did not return a Buffer");
  }
  if (png.byteLength > limits.maxPngBytes) {
    return {
      png: null,
      pngOmittedReason: "rendered PNG exceeds the byte limit",
    };
  }
  return { png: png.toString("base64"), pngOmittedReason: null };
}
