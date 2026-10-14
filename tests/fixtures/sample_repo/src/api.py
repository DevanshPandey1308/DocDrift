import os
import argparse


def create_user(name, email, timeout=30, *, retries=2):
    """Create a user."""
    return name, email, timeout, retries


API_KEY = os.getenv("API_KEY")

parser = argparse.ArgumentParser()
parser.add_argument("--retries", help="number of attempts")
