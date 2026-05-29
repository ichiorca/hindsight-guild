"""End-to-end driver scripts (relocated from scripts/).

Run individually, e.g. ``python -m tests.e2e.e2e_smoke --boot``. These are
NOT collected by pytest (they are argparse drivers needing a live API /
LLM), which is why they keep the ``e2e_`` prefix rather than ``test_``.
"""
