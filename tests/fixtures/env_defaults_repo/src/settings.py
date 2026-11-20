import os

MATCH = os.getenv("DOCDRIFT_MATCH", "20")
WRONG = os.environ.get("DOCDRIFT_WRONG", 30)
NO_DEFAULT = os.getenv("DOCDRIFT_NO_DEFAULT")
DYNAMIC = os.getenv("DOCDRIFT_DYNAMIC", configured_default)
