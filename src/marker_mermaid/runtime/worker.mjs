import readline from "node:readline";
import { createRequire } from "node:module";
import { chromium } from "playwright";

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
    const parsed = await page.evaluate(async ({ code, id }) => {
      document.body.replaceChildren();
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
    const result = await page.evaluate(async ({ code, id }) => {
      const rendered = await mermaid.render(`mmx-${id}`, code);
      document.body.innerHTML = rendered.svg;
      return {
        diagramType: rendered.diagramType,
        svg: rendered.svg,
      };
    }, request);
    result.diagramType ??= parsed.diagramType;
    const svg = page.locator("svg").first();
    const png = await svg.screenshot({ type: "png", animations: "disabled" });
    return { id: request.id, ok: true, syntaxValid, ...result, png: png.toString("base64") };
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
