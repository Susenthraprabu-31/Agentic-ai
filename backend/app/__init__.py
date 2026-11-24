"""app package."""

import asyncio
import sys

# Playwright launches its driver via asyncio subprocesses; on Windows only
# ProactorEventLoop supports that. (Selector raises NotImplementedError.)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())