from fastapi import FastAPI
from flask import Flask

api = FastAPI()
flask_app = Flask(__name__)


@api.get("/health")
def health():
    return {"ok": True}


@flask_app.route("/items", methods=["GET", "PUT"])
def items():
    return "items"
