"""Shared resource limits used before and after typed reconstruction."""

MAX_EVIDENCE_REFS = 256
MAX_EVIDENCE_INPUT_CHARS = 8_000_000
MAX_EVIDENCE_SOURCE_BLOCK_REFS = 20_000
MAX_EVIDENCE_SOURCE_BLOCK_CHARS = MAX_EVIDENCE_INPUT_CHARS

# Applied as hard POSIX limits by the small exec launcher before Node starts.
# RLIMIT_AS is not useful for Chromium because it deliberately reserves very
# large sparse address ranges. RLIMIT_DATA bounds committed heap/mmap growth
# while still leaving enough headroom for the pinned browser runtime.
MAX_RUNTIME_WORKER_DATA_BYTES = 2 * 1024 * 1024 * 1024
MAX_RUNTIME_WORKER_CPU_SECONDS = 600
