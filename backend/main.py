import os
import datetime
import re
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# ========== ENV ==========
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("❌ MONGO_URL environment variable is missing!")

client = AsyncIOMotorClient(MONGO_URL)
db = client["nhoyhub"]

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_TOKEN = "nhoyhub_admin_2025"

app = FastAPI(title="NhoyHub Order API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def require_admin(token: str = Depends(oauth2_scheme)):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ========== MODEL ==========
class OrderOut(BaseModel):
    id: str
    name: str
    udid: str
    image_url: str
    status: str
    download_link: str
    price: str
    created_at: str


# ========== API ==========
@app.get("/", tags=["health"])
async def health():
    return {"ok": True, "db": "MongoDB Connected ✅"}


def safe_filename(name: str):
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)


@app.post("/orders", response_model=OrderOut)
async def create_order(
    name: str = Form(...),
    udid: str = Form(...),
    image: UploadFile = File(...)
):
    price_match = re.search(r"\$(\d+)", name)
    price = price_match.group(1) if price_match else "N/A"

    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    filename = f"{int(datetime.datetime.utcnow().timestamp())}_{safe_filename(name)[:12]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(await image.read())

    data = {
        "name": name,
        "udid": udid,
        "image_url": f"/uploads/{filename}",
        "status": "pending",
        "download_link": "#",
        "price": price,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    result = await db.orders.insert_one(data)
    data["id"] = str(result.inserted_id)
    return data


@app.get("/orders", response_model=List[OrderOut])
async def list_orders():
    rows = await db.orders.find().sort("_id", -1).to_list(500)
    for r in rows:
        r["id"] = str(r["_id"])
    return rows


@app.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: str):
    row = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not row:
        raise HTTPException(404, "Not Found")
    row["id"] = str(row["_id"])
    return row


@app.put("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def update_order(order_id: str, status: str = Form(...)):
    result = await db.orders.update_one(
        {"_id": ObjectId(order_id)}, {"$set": {"status": status}}
    )
    if result.modified_count == 0:
        raise HTTPException(404, "Order not found")
    row = await db.orders.find_one({"_id": ObjectId(order_id)})
    row["id"] = str(row["_id"])
    return row


@app.delete("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def delete_order(order_id: str):
    row = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not row:
        raise HTTPException(404, "Order not found")
    await db.orders.delete_one({"_id": ObjectId(order_id)})
    return {"deleted": True}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
