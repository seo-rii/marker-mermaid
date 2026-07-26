import readline from "node:readline";
import { createRequire } from "node:module";
import { chromium } from "playwright";
import {
  captureBoundedPng,
  geometryOmissionReason,
  staticSvgOmissionReason,
  validateRenderLimits,
} from "./render_limits.mjs";

const require = createRequire(import.meta.url);
const mermaidBundle = require.resolve("mermaid/dist/mermaid.min.js");
const launchOptions = { headless: true };
if (process.env.MARKER_MERMAID_CHROMIUM_EXECUTABLE) {
  launchOptions.executablePath = process.env.MARKER_MERMAID_CHROMIUM_EXECUTABLE;
}

const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({ javaScriptEnabled: true });
await context.route("**/*", (route) => route.abort("blockedbyclient"));
const page = await context.newPage();
await page.setContent("<!doctype html><html><body></body></html>");
await page.addScriptTag({ path: mermaidBundle });

async function handle(request) {
  let syntaxValid = false;
  try {
    const limits = validateRenderLimits(request.limits);
    const parsed = await page.evaluate(async ({ code, id }) => {
      document.body.replaceChildren();
      delete globalThis.__markerMermaidPendingSvg;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
        deterministicIds: true,
        deterministicIDSeed: id,
        maxTextSize: 50_000,
        maxEdges: 500,
        htmlLabels: false,
        flowchart: { htmlLabels: false },
      });
      return await mermaid.parse(code);
    }, request);
    syntaxValid = true;
    const result = await page.evaluate(async ({ code, id, limits }) => {
      const rendered = await mermaid.render(`mmx-${id}`, code);
      const svgByteLength = new TextEncoder().encode(rendered.svg).byteLength;
      if (svgByteLength > limits.maxSvgBytes) {
        return {
          diagramType: rendered.diagramType,
          resourceError: "rendered SVG exceeds the byte limit",
        };
      }

      let parsedSvg = new DOMParser().parseFromString(
        rendered.svg,
        "image/svg+xml",
      );
      let parsedRoot = parsedSvg.documentElement;
      if (parsedSvg.querySelector("parsererror") !== null) {
        // Some Mermaid grammars produce browser-renderable markup that is not
        // XML-well-formed (for example, native C4 foreignObject labels and
        // quoted non-label Flowchart tokens). Match Chromium's inert HTML
        // parsing semantics for preview preflight; Python still inspects the
        // original SVG string as the final publication gate.
        parsedSvg = new DOMParser().parseFromString(rendered.svg, "text/html");
        parsedRoot = parsedSvg.body.querySelector("svg");
      }
      if (
        parsedRoot === null ||
        parsedRoot.localName !== "svg" ||
        parsedRoot.namespaceURI !== "http://www.w3.org/2000/svg"
      ) {
        return {
          diagramType: rendered.diagramType,
          resourceError: "rendered output does not have an SVG root",
        };
      }

      const nodeCount = parsedRoot.querySelectorAll("*").length + 1;
      let textLength = 0;
      let pathCount = 0;
      let pathDataLength = 0;
      let securityFinding = null;
      if (nodeCount <= limits.maxSvgNodes) {
        const textWalker = parsedSvg.createTreeWalker(
          parsedRoot,
          NodeFilter.SHOW_TEXT,
        );
        while (textWalker.nextNode()) {
          textLength += textWalker.currentNode.data.length;
          if (textLength > limits.maxSvgTextChars) break;
        }

        const forbiddenElements = new Set([
          "script",
          "iframe",
          "img",
          "object",
          "embed",
          "link",
          "foreignobject",
        ]);
        const geometryAttributes = new Set([
          "cx",
          "cy",
          "d",
          "height",
          "points",
          "r",
          "rx",
          "ry",
          "transform",
          "viewbox",
          "width",
          "x",
          "x1",
          "x2",
          "y",
          "y1",
          "y2",
        ]);
        const hasExternalCss = (value) => {
          const lowered = value.toLocaleLowerCase("en-US");
          if (lowered.includes("@import")) return true;
          for (const match of lowered.matchAll(/url\s*\(\s*(['"]?)(.*?)\1\s*\)/gu)) {
            if (!match[2].trim().startsWith("#")) return true;
          }
          return false;
        };
        const elementWalker = parsedSvg.createTreeWalker(
          parsedRoot,
          NodeFilter.SHOW_ELEMENT,
        );
        let element = elementWalker.currentNode;
        while (element !== null) {
          const tag = element.localName.toLocaleLowerCase("en-US");
          if (forbiddenElements.has(tag)) {
            securityFinding = `forbidden <${tag}>`;
            break;
          }
          if (tag === "path") {
            pathCount += 1;
            pathDataLength += (element.getAttribute("d") ?? "").length;
          }
          for (const attribute of element.attributes) {
            const name = attribute.localName.toLocaleLowerCase("en-US");
            const value = attribute.value.trim().toLocaleLowerCase("en-US");
            if (name.startsWith("on")) {
              securityFinding = `event handler ${name}`;
              break;
            }
            if (name === "href" && value && !value.startsWith("#")) {
              securityFinding = "external href";
              break;
            }
            if ((name === "src" || name === "srcset") && value) {
              securityFinding = `external ${name}`;
              break;
            }
            if (hasExternalCss(value)) {
              securityFinding = "external CSS";
              break;
            }
            if (
              geometryAttributes.has(name) &&
              (value.includes("nan") || value.includes("infinity"))
            ) {
              securityFinding = `non-finite geometry attribute ${name}`;
              break;
            }
          }
          if (securityFinding !== null) break;
          if (tag === "style" && hasExternalCss(element.textContent ?? "")) {
            securityFinding = "external CSS";
            break;
          }
          if (
            pathCount > limits.maxSvgPaths ||
            pathDataLength > limits.maxSvgPathDataChars
          ) {
            break;
          }
          element = elementWalker.nextNode();
        }
      }

      globalThis.__markerMermaidPendingSvg = document.importNode(parsedRoot, true);
      return {
        diagramType: rendered.diagramType,
        svg: rendered.svg,
        staticStats: {
          nodeCount,
          textLength,
          pathCount,
          pathDataLength,
          securityFinding,
        },
      };
    }, { ...request, limits });
    result.diagramType ??= parsed.diagramType;
    if (result.resourceError) {
      return {
        id: request.id,
        ok: false,
        syntaxValid,
        diagramType: result.diagramType,
        error: result.resourceError,
      };
    }

    let pngOmittedReason = staticSvgOmissionReason(result.staticStats, limits);
    if (pngOmittedReason === null) {
      const geometryStats = await page.evaluate(() => {
        const root = globalThis.__markerMermaidPendingSvg;
        delete globalThis.__markerMermaidPendingSvg;
        if (!(root instanceof SVGSVGElement)) {
          return null;
        }
        document.body.replaceChildren(root);
        const rect = root.getBoundingClientRect();
        const viewBox = root.hasAttribute("viewBox")
          ? {
              x: root.viewBox.baseVal.x,
              y: root.viewBox.baseVal.y,
              width: root.viewBox.baseVal.width,
              height: root.viewBox.baseVal.height,
            }
          : null;
        let contentBounds = null;
        try {
          const bounds = root.getBBox();
          contentBounds = {
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
          };
        } catch {
          return null;
        }
        const intrinsicSize =
          root.width && root.height
            ? {
                x: 0,
                y: 0,
                width: root.width.baseVal.value,
                height: root.height.baseVal.value,
              }
            : null;
        return {
          rect: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
          },
          viewBox,
          contentBounds,
          intrinsicSize,
        };
      });
      pngOmittedReason = geometryOmissionReason(geometryStats, limits);
    } else {
      await page.evaluate(() => {
        delete globalThis.__markerMermaidPendingSvg;
        document.body.replaceChildren();
      });
    }

    const svg = page.locator("svg").first();
    const preview = await captureBoundedPng(svg, pngOmittedReason, limits);
    return {
      id: request.id,
      ok: true,
      syntaxValid,
      diagramType: result.diagramType,
      svg: result.svg,
      png: preview.png,
      pngOmittedReason: preview.pngOmittedReason,
    };
  } catch (error) {
    return {
      id: request.id,
      ok: false,
      syntaxValid,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (!line.trim()) continue;
  let response;
  try {
    response = await handle(JSON.parse(line));
  } catch (error) {
    response = { id: null, ok: false, syntaxValid: false, error: String(error) };
  }
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

await browser.close();
