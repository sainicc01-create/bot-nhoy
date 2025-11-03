import os
import datetime
import re
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from math import ceil

# ================== ENV & DB SETUP (MongoDB) ==================
# Load environment variables from .env if running locally
load_dotenv()

# Use MongoDB URL and Admin Token from the environment
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://nhoyadmin:NhoyAPI%402003!@clusternhoy.6lxwgtj.mongodb.net/?appName=ClusterNhoy")
DB_NAME = os.getenv("DB_NAME", "nhoy")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "nhoyhub_admin_2025")

if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL is not set.")

# UPLOAD_DIR and Defaults
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
DEFAULT_CART_IMAGE = "https://i.pinimg.com/736x/67/74/40/67744063f3ce36103729fb5ed2edc98e.jpg"
DEFAULT_ESIGN_IMAGE = "https://via.placeholder.com/400x200/007bff/ffffff?text=Esign+Image"
DEFAULT_DOWNLOAD_LINK = "#"

# ================== MONGO GLOBALS (Typed for Pylance) ==================
client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None
col_orders: Optional[AsyncIOMotorCollection] = None
col_admins: Optional[AsyncIOMotorCollection] = None
col_config: Optional[AsyncIOMotorCollection] = None
col_counters: Optional[AsyncIOMotorCollection] = None # For generating sequential 'id'


app = FastAPI(title="NhoyHub Order API", version="4.0 - MongoDB")

# CORS Policy: Use your deployment URL for security in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Keeping it open for flexibility during development/testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


# ========== MONGO DB INITIALIZATION LOGIC ==========
async def get_next_seq(name: str) -> int:
    """Atomic counter to keep integer ids like in SQL."""
    # This is MongoDB's way of implementing auto-increment
    doc = await col_counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    if not doc:
        doc = await col_counters.find_one({"_id": name})
        if not doc:
            return 1
    return int(doc["seq"])

@app.on_event("startup")
async def startup_event():
    """Initializes MongoDB connection and collection structure on startup."""
    global client, db, col_orders, col_admins, col_config, col_counters
    
    try:
        client = AsyncIOMotorClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        db = client[DB_NAME]
        
        # Assign collections
        col_orders = db["orders"]
        col_admins = db["admins"]
        col_config = db["config"]        # {_id: key, value: string}
        col_counters = db["counters"]    # {_id: 'orders', seq: int}

        # Create indexes (like SQL primary/unique keys)
        await col_orders.create_index([("id", 1)], unique=True)
        await col_orders.create_index([("udid", 1)])
        await col_admins.create_index([("username", 1)], unique=True)

        # Seed admin if not exists
        admin = await col_admins.find_one({"username": "admin"})
        if not admin:
            await col_admins.insert_one({"username": "admin", "password": "1234"})

        # Seed config defaults if missing
        config_keys = {
            'public_image_url': DEFAULT_CART_IMAGE,
            'esign_image_1': DEFAULT_ESIGN_IMAGE + " 1",
            'esign_image_2': DEFAULT_ESIGN_IMAGE + " 2",
            'esign_image_3': DEFAULT_ESIGN_IMAGE + " 3",
            'esign_image_4': DEFAULT_ESIGN_IMAGE + " 4",
            'esign_image_5': DEFAULT_ESIGN_IMAGE + " 5",
        }
        for key, default_value in config_keys.items():
            if not await col_config.find_one({"_id": key}):
                await col_config.insert_one({"_id": key, "value": default_value})
        
        print("Database setup complete: MongoDB collections and default data checked/created.")

    except Exception as e:
        print(f"FATAL DATABASE ERROR ON STARTUP: {e}")
        raise HTTPException(503, detail="Service Unavailable: Database connection failed on startup.")

@app.on_event("shutdown")
async def shutdown_event():
    if client:
        client.close()

# Dependency (dummy for MongoDB, as we don't need to manage sessions like SQLAlchemy)
async def get_db_session():
    # Placeholder for dependency compatibility; not strictly needed for motor operations
    yield


# ========== MODELS & UTILS ==========
class OrderOut(BaseModel):
    id: int 
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


# ========== AUTH & HELPERS (MongoDB) ==========
async def get_config_value(key: str):
    """Fetches config value from MongoDB."""
    doc = await col_config.find_one({"_id": key})
    if not doc:
        if key == 'public_image_url':
            return DEFAULT_CART_IMAGE
        elif 'esign_image_' in key:
            return DEFAULT_ESIGN_IMAGE
        return "#"
    return doc.get("value", "#")


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    """Handles admin login using MongoDB."""
    admin_doc = await col_admins.find_one({"username": username, "password": password})
    if admin_doc:
        return {"token": ADMIN_TOKEN, "username": username} 
    raise HTTPException(401, "Invalid credentials")

@app.get("/", tags=["health"])
async def health():
    """Checks MongoDB connection status."""
    try:
        # PING the database to ensure connection
        await db.command("ping")
        return {"ok": True, "db": "MongoDB Connected ✅"}
    except Exception as e:
        raise HTTPException(503, detail="Service Unavailable: Database connection failed.")


# ========== CONFIG ENDPOINTS (MongoDB) ==========
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
        
    # MongoDB upsert operation
    await col_config.update_one(
        {"_id": 'public_image_url'},
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
    
    # MongoDB upsert operation
    await col_config.update_one(
        {"_id": key},
        {"$set": {"value": image_url}},
        upsert=True
    )
    return {"message": f"Esign Image {image_number} updated successfully", "image_url": image_url}


# ========== ORDERS (MongoDB) ==========
@app.post("/orders", response_model=OrderOut)
async def create_order(name: str = Form(...), udid: str = Form(...), image: UploadFile = File(...)):
    """Creates a new order entry and saves the payment proof image."""
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
    
    new_id = await get_next_seq("orders") # Get sequential ID
    
    data = {
        "id": new_id, # Use sequential ID
        "name": name,
        "udid": udid,
        "image_url": f"/uploads/{filename}",
        "status": "pending",
        "download_link": DEFAULT_DOWNLOAD_LINK,
        "price": price,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    
    await col_orders.insert_one(data)
    
    # Return the created document (MongoDB includes _id, which is ignored by Pydantic model)
    return data


@app.get("/orders", response_model=PageOut)
async def list_orders(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
    sort: str = "-id"
):
    """Lists orders with pagination, search, and admin image filtering."""
    is_admin = is_admin_request(request.headers)
    page = max(page, 1)
    page_size = max(min(page_size, 50), 1)
    
    # Build filter
    filt: Dict[str, Any] = {}
    if status:
        filt["status"] = status
    if q:
        # Case-insensitive search on name or udid (MongoDB $regex)
        regex = {"$regex": q, "$options": "i"}
        filt["$or"] = [{"name": regex}, {"udid": regex}]
    
    # Determine sorting (MongoDB format)
    sort_key = ("id", -1) if sort == "-id" else ("id", 1)
    offset = (page - 1) * page_size
    
    # 1. Get Total Count
    total_count = await col_orders.count_documents(filt)
    
    # 2. Fetch data
    cursor = col_orders.find(filt).sort([sort_key]).skip(offset).limit(page_size)
    
    public_image_url = await get_config_value('public_image_url')
    
    items: List[OrderOut] = []
    async for r in cursor:
        item = {
            "id": int(r.get("id")),
            "name": r.get("name", ""),
            "udid": r.get("udid", ""),
            "image_url": r.get("image_url", ""),
            "status": r.get("status", "pending"),
            "download_link": r.get("download_link", DEFAULT_DOWNLOAD_LINK),
            "price": r.get("price", "N/A"),
            "created_at": r.get("created_at", datetime.datetime.utcnow().isoformat()),
        }
        
        # SECURITY/UX: Replace payment image for public users
        if not is_admin:
            item["image_url"] = public_image_url
        
        items.append(OrderOut(**item))
        
    return PageOut(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size
    )


@app.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: int):
    """Retrieves a single order by ID."""
    row = await col_orders.find_one({"id": order_id})
    
    if not row:
        raise HTTPException(404, "Order not found")
        
    # Map MongoDB result to Pydantic model
    row_dict = {
        "id": int(row.get("id")),
        "name": row.get("name", ""),
        "udid": row.get("udid", ""),
        "image_url": row.get("image_url", ""),
        "status": row.get("status", "pending"),
        "download_link": row.get("download_link", DEFAULT_DOWNLOAD_LINK),
        "price": row.get("price", "N/A"),
        "created_at": row.get("created_at", datetime.datetime.utcnow().isoformat()),
    }
    return row_dict


@app.put("/orders/{order_id}", dependencies=[Depends(require_admin)], response_model=OrderOut)
async def update_order(
    order_id: int, 
    name: Optional[str] = Form(None),
    udid: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    download_link: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    """Updates order details (Admin only)."""
    
    # Fetch current order details
    current_order = await col_orders.find_one({"id": order_id})
    if not current_order:
        raise HTTPException(404, "Order not found")
        
    update_data: Dict[str, Any] = {}
    
    # Handle File Upload
    if image is not None:
        ext = os.path.splitext(image.filename or "")[1] or ".jpg"
        filename = f"{int(datetime.datetime.utcnow().timestamp())}_{safe_filename(name or current_order.get('name', ''))[:12]}{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        content = await image.read()
        if not content:
            raise HTTPException(400, "Empty image")
            
        with open(filepath, "wb") as f:
            f.write(content)
        
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

    # Perform Update
    if update_data:
        await col_orders.update_one({"id": order_id}, {"$set": update_data})
    
    # Return Updated Row
    updated_row = await col_orders.find_one({"id": order_id})
    
    row_dict = {
        "id": int(updated_row.get("id")),
        "name": updated_row.get("name", ""),
        "udid": updated_row.get("udid", ""),
        "image_url": updated_row.get("image_url", ""),
        "status": updated_row.get("status", "pending"),
        "download_link": updated_row.get("download_link", DEFAULT_DOWNLOAD_LINK),
        "price": updated_row.get("price", "N/A"),
        "created_at": updated_row.get("created_at", datetime.datetime.utcnow().isoformat()),
    }
    return row_dict


@app.delete("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def delete_order(order_id: int):
    """Deletes an order by ID (Admin only)."""
    await col_orders.delete_one({"id": order_id})
    return {"deleted": True}


# Static mount for uploaded files - MUST be at the end
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")