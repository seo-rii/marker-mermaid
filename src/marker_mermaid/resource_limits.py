"""Shared resource limits used before and after typed reconstruction."""

MAX_EVIDENCE_REFS = 256
MAX_EVIDENCE_INPUT_CHARS = 8_000_000
MAX_EVIDENCE_SOURCE_BLOCK_REFS = 20_000
MAX_EVIDENCE_SOURCE_BLOCK_CHARS = MAX_EVIDENCE_INPUT_CHARS

# Linux applies DATA as a hard per-process limit before Node starts. Darwin
# instead samples the SUM of physical footprints in the supervisor's owned
# process group; sparse VM mappings make a small DATA/AS limit unusable there.
# RLIMIT_AS is not useful for Chromium because it deliberately reserves very
# large sparse address ranges. RLIMIT_DATA bounds committed heap/mmap growth
# while still leaving enough headroom for the pinned browser runtime.
MAX_RUNTIME_WORKER_DATA_BYTES = 2 * 1024 * 1024 * 1024
MAX_RUNTIME_WORKER_CPU_SECONDS = 600
MAX_RUNTIME_GROUP_MEMBERS = 256
RUNTIME_MEMORY_POLL_SECONDS = 0.05
