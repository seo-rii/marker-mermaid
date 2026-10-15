import childProcess from "node:child_process";

let launching = false;

export async function launchOwnedChromium(chromium, launchOptions) {
  // Playwright 1.61.1 launches browsers with detached:true on POSIX. Keep the
  // browser in our worker's owned group so its Python owner can account for
  // and terminate Chromium even when Node exits before browser.close(). The
  // pinned Playwright launch contract is covered by real-browser CI fixtures.
  if (launching) throw new Error("Chromium launch is already in progress");
  launching = true;
  const originalSpawn = childProcess.spawn;
  childProcess.spawn = function spawnOwned(command, args, options) {
    if (process.platform === "win32") {
      return originalSpawn.call(this, command, args, options);
    }
    if (Array.isArray(args)) {
      return originalSpawn.call(this, command, args, { ...options, detached: false });
    }
    return originalSpawn.call(this, command, { ...args, detached: false });
  };
  try {
    return await chromium.launch(launchOptions);
  } finally {
    childProcess.spawn = originalSpawn;
    launching = false;
  }
}
