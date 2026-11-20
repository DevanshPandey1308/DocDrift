import os

HTTP_PROXY = os.getenv("HTTP_PROXY")


def fetch(timeout=60, retries=3):
    return timeout, retries
