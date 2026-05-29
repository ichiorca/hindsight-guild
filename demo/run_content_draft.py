"""Back-compat shim — delegates to run_pipeline.py.

The pipeline IS the content draft path now (Research → Content → Review).
"""
from __future__ import annotations

import asyncio

from demo.run_pipeline import main

if __name__ == "__main__":
    asyncio.run(main())
