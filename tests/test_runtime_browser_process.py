from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("launch_fails", [False, True])
def test_browser_launch_keeps_real_child_in_owned_group_and_restores_spawn(launch_fails):
    if shutil.which("node") is None:
        pytest.skip("Node is required for browser process ownership tests")
    module = Path(__file__).resolve().parents[1] / "src/marker_mermaid/runtime/browser_process.mjs"
    script = f"""
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {{launchOwnedChromium}} from {json.dumps(module.as_uri())};
const originalSpawn = childProcess.spawn;
const literalArgs = ['a b', ') ; $() `', '한글'];
const launchOptions = {{headless: true}};
const spawnOptions = {{detached: true, stdio: ['ignore', 'pipe', 'inherit']}};
let observed;
const browser = {{}};
const chromium = {{async launch(options) {{
  assert.equal(options, launchOptions);
  const probe = `const cp = require('node:child_process');
    console.log(JSON.stringify({{args: process.argv.slice(1),
      group: Number(cp.execFileSync('ps', ['-o', 'pgid=', '-p', String(process.pid)],
        {{encoding: 'utf8'}}).trim())}}));`;
  const child = childProcess.spawn(process.execPath, ['-e', probe, ...literalArgs], spawnOptions);
  let output = '';
  child.stdout.on('data', chunk => output += chunk);
  await new Promise((resolve, reject) => {{
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve() : reject(new Error(`child exit ${{code}}`)));
  }});
  observed = JSON.parse(output);
  if ({json.dumps(launch_fails)}) throw new Error('launch failed after spawn');
  return browser;
}}}};
try {{
  assert.equal(await launchOwnedChromium(chromium, launchOptions), browser);
  assert.equal({json.dumps(launch_fails)}, false);
}} catch (error) {{
  assert.equal({json.dumps(launch_fails)}, true);
  assert.equal(error.message, 'launch failed after spawn');
}}
assert.equal(childProcess.spawn, originalSpawn);
assert.equal(spawnOptions.detached, true);
assert.deepEqual(observed.args, literalArgs);
process.stdout.write(JSON.stringify({{...observed, launcher: process.pid}}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        start_new_session=True,
    )
    observed = json.loads(completed.stdout)
    assert os.name == "posix"
    assert observed["group"] == observed["launcher"]
