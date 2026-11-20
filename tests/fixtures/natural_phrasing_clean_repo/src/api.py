import os

HTTP_PROXY = os.getenv("HTTP_PROXY")


def fetch(timeout=30, retries=5):
    return timeout, retries
