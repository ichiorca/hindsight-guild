"""Back-compat entry point. Real schema work lives in mongo/schema.py.

Run:
    python mongo/seed.py
    # equivalent to:
    python -m mongo.schema apply
"""
from mongo.schema import apply

if __name__ == "__main__":
    apply()
