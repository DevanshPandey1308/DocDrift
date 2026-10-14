from fastapi import APIRouter, FastAPI

app = FastAPI()
route_path = "/dynamic"


@app.get(route_path)
def dynamic_route():
    return None


router = APIRouter(prefix="/v1")


@router.get("/items")
def mounted_route():
    return None
