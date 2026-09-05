"""`make smoke` entry point — human-only, it spends real API calls.

The smoke itself lives in `lang_ai_agent.smoke` (importable, and its console
loop is tested with a scripted model); this wrapper only runs it. Same as
`lang-ai-agent smoke [--mcp]`. See docs/TESTING.md §5.
"""

import sys

from lang_ai_agent.smoke import run_smoke

if __name__ == "__main__":
    sys.exit(run_smoke(mcp="--mcp" in sys.argv[1:]))
