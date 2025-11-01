import os, datetime
import re 
import json 
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query, Request 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from math import ceil

# ========== ENV & DB SETUP ==========
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("❌ MONGO_URL environment variable is missing!")

client = AsyncIOMotorClient(MONGO_URL)
db = client["nhoyhub"]

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "nhoyhub_admin_2025")

# Default values (Used for config initialization and fallbacks)
DEFAULT_CART_IMAGE = "https://i.pinimg.com/736x/67/74/40/67744063f3ce36103729fb5ed2edc98e.jpg" 
DEFAULT_ESIGN_IMAGE = "https://via.placeholder.com/400x200/007bff/ffffff?text=Esign+Image"
DEFAULT_DOWNLOAD_LINK = "#"
DEFAULT_ADMIN_EMAIL = "admin@nhoyhub.com"


app = FastAPI(title="NhoyHub Order API", version="3.0 - MongoDB")

# FIX: Relax CORS for testing (assuming you use Render or Vercel)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:8000", "https://bot-nhoy.vercel.app", "*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# ========== MODELS & UTILS ==========
class OrderOut(BaseModel):
    id: str
    name: str
    udid: str
    image_url: str
    status: str
    download_link: str
    price: str
    created_at: str

class PageOut(BaseModel):
    items: List[OrderOut]
    total: int
    page: int
    page_size: int

class OrderUpdateStatus(BaseModel):
    status: str = Query(..., pattern="^(pending|approved|rejected)$")
    name: Optional[str] = None
    
class ConfigUpdateImage(BaseModel):
    image_url: str

def safe_filename(name: str):
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)

# Dependency injection for admin check
def require_admin(token: str = Depends(oauth2_scheme)):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def is_admin_request(headers):
    token = headers.get("authorization", "")
    return token == f"Bearer {ADMIN_TOKEN}"


# ========== DB INITIALIZATION (Async Startup) ==========
@app.on_event("startup")
async def startup_db_init():
    """Ensures default config values exist in MongoDB."""
    
    config_keys = {
        'public_image_url': DEFAULT_CART_IMAGE,
        'esign_image_1': DEFAULT_ESIGN_IMAGE + " 1",
        'esign_image_2': DEFAULT_ESIGN_IMAGE + " 2",
        'esign_image_3': DEFAULT_ESIGN_IMAGE + " 3",
        'esign_image_4': DEFAULT_ESIGN_IMAGE + " 4",
        'esign_image_5': DEFAULT_ESIGN_IMAGE + " 5",
    }
    
    # Initialize Admin account (for simplicity, only check/set token)
    # NOTE: Real world apps would hash passwords.
    await db.admins.update_one(
        {"username": "admin"},
        {"$set": {"password": "1234", "token": ADMIN_TOKEN}},
        upsert=True
    )
    
    # Initialize Configs
    for key, default_value in config_keys.items():
        await db.config.update_one(
            {"key": key},
            {"$set": {"value": default_value}},
            upsert=True
        )

# ========== AUTH & HELPERS ==========
# Helper function to get config values
async def get_config_value(key: str):
    config = await db.config.find_one({"key": key})
    if config:
        return config['value']
    # Fallback to default if not found
    if key == 'public_image_url':
        return DEFAULT_CART_IMAGE
    elif 'esign_image_' in key:
        return DEFAULT_ESIGN_IMAGE
    return "#"

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    admin_data = await db.admins.find_one({"username": username, "password": password})
    if admin_data:
        # NOTE: Using the hardcoded token for simplicity as before
        return {"token": ADMIN_TOKEN, "username": username} 
    raise HTTPException(401, "Invalid credentials")

@app.get("/", tags=["health"])
async def health():
    try:
        await client.admin.command('ping')
        return {"ok": True, "db": "MongoDB Connected ✅"}
    except Exception:
        raise HTTPException(503, "Service Unavailable: MongoDB connection failed.")


# ========== CONFIG ENDPOINTS ==========
@app.get("/config", dependencies=[Depends(require_admin)])
async def get_config():
    """Returns current configuration (Admin only)."""
    config_data = {
        "public_image_url": await get_config_value('public_image_url'),
    }
    for i in range(1, 6):
        config_data[f"esign_image_{i}"] = await get_config_value(f"esign_image_{i}")
        
    return config_data

@app.put("/config/public", dependencies=[Depends(require_admin)])
async def update_config_public(public_image_url: str = Form(...)):
    """Updates the public order list image URL."""
    if not public_image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    await db.config.update_one(
        {"key": "public_image_url"},
        {"$set": {"value": public_image_url}},
        upsert=True
    )
    return {"message": "Public image URL updated successfully", "public_image_url": public_image_url}

@app.put("/config/esign_image/{image_number}", dependencies=[Depends(require_admin)])
async def update_config_esign_image(image_number: int, image_url: str = Form(...)):
    """Updates a single Esign image URL (1 to 5)."""
    if image_number < 1 or image_number > 5:
        raise HTTPException(400, "Image number must be between 1 and 5.")
    if not image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    key = f"esign_image_{image_number}"
    
    await db.config.update_one(
        {"key": key},
        {"$set": {"value": image_url}},
        upsert=True
    )
    return {"message": f"Esign Image {image_number} updated successfully", "image_url": image_url}


# ========== ORDERS (MODIFIED FOR PAGINATION & FILTERING) ==========
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

    content = await image.read()
    if not content:
        raise HTTPException(400, "Empty image")
        
    with open(filepath, "wb") as f:
        f.write(content)

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

@app.get("/orders", response_model=PageOut)
async def list_orders(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
    sort: str = "-id" # "-id" for descending, "id" for ascending
):
    is_admin = is_admin_request(request.headers)
    page = max(page, 1)
    page_size = max(min(page_size, 50), 1)
    
    query = {}
    
    if status:
        query["status"] = status
    
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"udid": {"$regex": q, "$options": "i"}}
        ]
    
    # 1. Get Total Count
    total_count = await db.orders.count_documents(query)
    
    # 2. Determine sorting
    sort_field = "_id"
    sort_order = -1 if sort == "-id" else 1 # -1 for DESC, 1 for ASC
    skip = (page - 1) * page_size
    
    # 3. Fetch data
    cursor = db.orders.find(query).sort(sort_field, sort_order).skip(skip).limit(page_size)
    rows = await cursor.to_list(page_size)
    
    public_image_url = await get_config_value('public_image_url')
    
    items = []
    for r in rows:
        r["id"] = str(r["_id"])
        
        # SECURITY/UX: Replace payment image and hide sensitive data from public view
        if not is_admin:
            r["image_url"] = public_image_url
            # UDID remains visible based on current user requirement
        
        items.append(r)
        
    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size
    }


@app.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: str):
    try:
        object_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(404, "Invalid Order ID format")
        
    row = await db.orders.find_one({"_id": object_id})
    if not row:
        raise HTTPException(404, "Order not found")
    row["id"] = str(row["_id"])
    return row


@app.put("/orders/{order_id}", dependencies=[Depends(require_admin)], response_model=OrderOut)
async def update_order(
    order_id: str, 
    name: Optional[str] = Form(None),
    udid: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    download_link: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    try:
        object_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(404, "Invalid Order ID format")
    
    current_order = await db.orders.find_one({"_id": object_id})
    if not current_order:
        raise HTTPException(404, "Order not found")

    update_data = {}
    
    # Handle File Upload
    if image is not None:
        ext = os.path.splitext(image.filename or "")[1] or ".jpg"
        filename = f"{int(datetime.datetime.utcnow().timestamp())}_{safe_filename(name or current_order['name'])[:12]}{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(await image.read())
        
        update_data["image_url"] = f"/uploads/{filename}"

    # Handle Form Data updates
    if name is not None:
        update_data["name"] = name
    if udid is not None:
        update_data["udid"] = udid
    if status is not None:
        update_data["status"] = status
    if download_link is not None:
        update_data["download_link"] = download_link or "#"

    # Perform Update
    result = await db.orders.update_one(
        {"_id": object_id}, {"$set": update_data}
    )
    
    row = await db.orders.find_one({"_id": object_id})
    row["id"] = str(row["_id"])
    return row


@app.delete("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def delete_order(order_id: str):
    try:
        object_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(404, "Invalid Order ID format")

    row = await db.orders.find_one({"_id": object_id})
    if not row:
        raise HTTPException(404, "Order not found")
        
    await db.orders.delete_one({"_id": object_id})
    return {"deleted": True}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")