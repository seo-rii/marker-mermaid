import json
import os
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from marker_mermaid.validation import default_runtime_dir


@pytest.mark.integration
def test_worker_context_blocks_service_workers_and_websocket_connections(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Upgrade")))
            if self.path == "/sw.js":
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.end_headers()
                self.wfile.write(b'self.addEventListener("install", () => self.skipWaiting());')
            else:
                self.send_error(400)

        def log_message(self, *_args):
            pass

    packaged = Path(__file__).resolve().parents[1] / "src/marker_mermaid/runtime"
    dependencies = default_runtime_dir() / "node_modules"
    assert dependencies.is_dir(), "browser CI requires installed runtime dependencies"
    (tmp_path / "node_modules").symlink_to(dependencies, target_is_directory=True)
    (tmp_path / "render_limits.mjs").write_bytes((packaged / "render_limits.mjs").read_bytes())
    source = (packaged / "worker.mjs").read_text(encoding="utf-8")
    bootstrap, separator, _handler = source.partition("async function handle(request) {")
    assert separator, "probe must execute the production worker context setup"

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        probe = bootstrap + "\nconst origin = " + json.dumps(origin) + ";\n" + r"""
try {
  await page.route(origin + "/", route => route.fulfill({
    contentType: "text/html", body: "<!doctype html><title>Network policy probe</title>",
  }));
  // Isolate the Service Worker policy from the existing HTTP route policy:
  // without serviceWorkers:block this local script successfully registers.
  await context.route(origin + "/sw.js", route => route.continue());
  await page.goto(origin + "/");
  const result = await page.evaluate(async origin => {
    const websocket = await new Promise(resolve => {
      const socket = new WebSocket(origin.replace("http:", "ws:") + "/socket");
      const timer = setTimeout(() => { socket.close(); resolve("timeout"); }, 2000);
      const finish = state => { clearTimeout(timer); resolve(state); };
      socket.onopen = () => finish("opened");
      socket.onclose = () => finish("closed");
      socket.onerror = () => finish("error");
    });
    const registration = await Promise.race([
      navigator.serviceWorker.register("/sw.js").then(() => "resolved", () => "rejected"),
      new Promise(resolve => setTimeout(() => resolve("timeout"), 2000)),
    ]);
    return {
      websocket, registration,
      serviceWorkers: (await navigator.serviceWorker.getRegistrations()).length,
    };
  }, origin);
  process.stdout.write(JSON.stringify(result));
} finally {
  await browser.close();
}
"""
        script = tmp_path / "network-probe.mjs"
        script.write_text(probe, encoding="utf-8")
        try:
            with subprocess.Popen(
                ["node", str(script)], cwd=tmp_path, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True,
            ) as process:
                try:
                    stdout, stderr = process.communicate(timeout=40)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise
                assert process.returncode == 0, stderr
        finally:
            server.shutdown()
            thread.join(timeout=5)

    result = json.loads(stdout)
    assert result["websocket"] == "closed", result
    assert result["registration"] != "timeout"
    assert result["serviceWorkers"] == 0
    assert requests == [], (
        "neither WebSocket handshakes nor Service Worker scripts may reach the server"
    )
