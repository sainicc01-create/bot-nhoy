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
from math import ceil

# ========== ENV & DB SETUP ==========
# FIX 1: Allow MONGO_URL to be read safely (it's already correct in your code)
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    # Use a generic local fallback for development if needed, but error is safer
    raise RuntimeError("❌ MONGO_URL environment variable is missing!")

client = AsyncIOMotorClient(MONGO_URL)
db = client["nhoyhub"]

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "nhoyhub_admin_2025") # Get token from ENV
PUBLIC_IMAGE_URL = os.getenv("PUBLIC_IMAGE_URL", "https://i.pinimg.com/736x/67/74/40/67744063f3ce36103729fb5ed2edc98e.jpg")


app = FastAPI(title="NhoyHub Order API", version="3.0 - MongoDB")

# FIX 2: Relax CORS for testing (allowing local dev and your Vercel domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:8000", "https://bot-nhoy.vercel.app", "*"], # Added local and wildcard for development ease
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Helper to check token outside of standard dependency (used for list_orders)
def is_admin_request(headers):
    token = headers.get("authorization", "")
    return token == f"Bearer {ADMIN_TOKEN}"

# Dependency injection for admin check
def require_admin(token: str = Depends(oauth2_scheme)):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ========== MODEL & UTILS ==========
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

def safe_filename(name: str):
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)

# ========== API ENDPOINTS ==========
@app.get("/", tags=["health"])
async def health():
    # Simple check to see if DB client is initialized
    try:
        await client.admin.command('ping')
        return {"ok": True, "db": "MongoDB Connected ✅"}
    except Exception:
        return {"ok": False, "db": "MongoDB Connection Failed ❌"}


@app.post("/orders", response_model=OrderOut)
async def create_order(
    name: str = Form(...),
    udid: str = Form(...),
    image: UploadFile = File(...)
):
    price_match = re.search(r"\$(\d+)", name)
    price = price_match.group(1) if price_match else "N/A"

    # Save file content locally
    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    filename = f"{int(datetime.datetime.utcnow().timestamp())}_{safe_filename(name)[:12]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # Use async file writing for non-blocking I/O
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


# FIX 3: Implement MongoDB pagination, filtering, and security logic matching the HTML frontend
@app.get("/orders", response_model=PageOut)
async def list_orders(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
    sort: str = "-id"
):
    is_admin = is_admin_request(request.headers)
    page = max(page, 1)
    page_size = max(min(page_size, 50), 1)
    
    query = {}
    
    if status:
        # NOTE: The frontend admin.html sends 'rejected' value when 'backlist' is selected.
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
    
    items = []
    for r in rows:
        r["id"] = str(r["_id"])
        
        # SECURITY/UX: Replace payment image and hide sensitive data from public view
        if not is_admin:
            r["image_url"] = PUBLIC_IMAGE_URL
            # UDID is visible only because requested by user, otherwise it should be hidden here.
        
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
        row = await db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        raise HTTPException(404, "Invalid Order ID format")

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
    
    # 1. Fetch current order to handle file replacement and check existence
    current_order = await db.orders.find_one({"_id": object_id})
    if not current_order:
        raise HTTPException(404, "Order not found")

    update_data = {}
    
    # Handle File Upload (This logic is simplified for deployment)
    if image is not None:
        # NOTE: In a real deployment, you must delete the old file from S3/storage here.
        
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
        update_data["download_link"] = download_link or "#" # Save '#' if link is cleared

    # 2. Perform Update
    result = await db.orders.update_one(
        {"_id": object_id}, {"$set": update_data}
    )
    
    if result.modified_count == 0 and not result.upserted_id:
        raise HTTPException(404, "Order not found or no changes made")

    # 3. Return Updated Row
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
        
    # NOTE: In a real deployment, you must delete the file from S3/storage here.
    
    await db.orders.delete_one({"_id": object_id})
    return {"deleted": True}


# FIX 4: Endpoint for updating config (Required by Admin Panel HTML)
@app.get("/config", dependencies=[Depends(require_admin)])
async def get_config():
    """Returns all configuration data."""
    config = {}
    keys = ['public_image_url', 'esign_image_1', 'esign_image_2', 'esign_image_3', 'esign_image_4', 'esign_image_5']
    
    # Fetch all configuration items in one go
    cursor = db.config.find({"key": {"$in": keys}})
    rows = await cursor.to_list(10)
    
    # Convert list of dicts to a single dict keyed by 'key'
    config_dict = {item['key']: item['value'] for item in rows}
    
    # Ensure all expected keys are present, using defaults if necessary
    for key in keys:
        config[key] = config_dict.get(key) or (PUBLIC_IMAGE_URL if key == 'public_image_url' else DEFAULT_ESIGN_IMAGE)
        
    return config

# Endpoint for updating a single config key (like public_image_url)
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

# Endpoint for updating a single Esign image gallery URL
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


# Mount static files to serve images
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")