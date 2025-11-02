import os, datetime
import re 
import json 
from typing import List, Optional
# Ensure all necessary classes are imported from fastapi and pydantic
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query, Request 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel # Ensure BaseModel is imported
from sqlalchemy import create_engine, text

# ========== MODELS (MOVED TO TOP) ==========
# We remove the unused field_validator import dependency from this module to simplify

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
    # Note: Query is usually used outside BaseModel, but Pydantic's definition allows validation here.
    status: str = Query(..., pattern="^(pending|approved|rejected)$") 
    name: Optional[str] = None
    
class ConfigUpdateImage(BaseModel):
    image_url: str


# ========== CONFIG ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "orders.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Default values
DEFAULT_CART_IMAGE = "https://i.pinimg.com/1200x/65/70/26/65702663ce714c284be130806e84f3dd.jpg" 
DEFAULT_ESIGN_IMAGE = ""
DEFAULT_DOWNLOAD_LINK = "#"

engine = create_engine(DATABASE_URL, future=True)

# Initialize tables
with engine.begin() as conn:
    # Ensure all columns exist before running INSERT/UPDATE logic
    try: conn.execute(text("ALTER TABLE orders ADD COLUMN download_link TEXT DEFAULT :d"), {"d": DEFAULT_DOWNLOAD_LINK})
    except: pass
    try: conn.execute(text("ALTER TABLE orders ADD COLUMN price TEXT DEFAULT 'N/A'"))
    except: pass
    
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        udid TEXT NOT NULL,
        image_url TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        download_link TEXT DEFAULT '#',
        price TEXT DEFAULT 'N/A',
        created_at TEXT NOT NULL
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS admins(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    );""")
    # Table for global configuration
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    # Default admin insertion
    exists = conn.execute(text("SELECT 1 FROM admins WHERE username='admin'")).first()
    if not exists:
        conn.execute(
            text("INSERT INTO admins(username, password) VALUES(:u,:p)"),
            {"u": "admin", "p": "1"}
        )
        
    # --- Default config insertion ---
    config_keys = {
        'public_image_url': DEFAULT_CART_IMAGE,
        'esign_image_1': DEFAULT_ESIGN_IMAGE + " 1",
        'esign_image_2': DEFAULT_ESIGN_IMAGE + " 2",
        'esign_image_3': DEFAULT_ESIGN_IMAGE + " 3",
        'esign_image_4': DEFAULT_ESIGN_IMAGE + " 4",
        'esign_image_5': DEFAULT_ESIGN_IMAGE + " 5",
    }
    
    for key, default_value in config_keys.items():
        exists_config = conn.execute(text(f"SELECT 1 FROM config WHERE key='{key}'")).first()
        if not exists_config:
            conn.execute(
                text("INSERT INTO config(key, value) VALUES(:k, :v)"),
                {"k": key, "v": default_value}
            )


# ========== APP ==========
app = FastAPI(title="NhoyHub Order API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
ADMIN_TOKEN = "admin_token"

# ========== AUTH & HELPERS ==========
def require_admin(token: str = Depends(oauth2_scheme)):
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "Unauthorized")
    return True

def is_admin_request(headers):
    token = headers.get("authorization", "")
    return token == f"Bearer {ADMIN_TOKEN}"

# Helper function to get config values
def get_config_value(key: str):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT value FROM config WHERE key=:k"), {"k": key}).scalar_one_or_none()
        if result is None:
            if 'esign_image_' in key:
                return DEFAULT_ESIGN_IMAGE
            return DEFAULT_CART_IMAGE
        return result

# ========== CONFIG ENDPOINTS ==========
@app.get("/config", dependencies=[Depends(require_admin)])
def get_config():
    """Returns current configuration (Admin only)."""
    config_data = {
        "public_image_url": get_config_value('public_image_url'),
    }
    for i in range(1, 6):
        config_data[f"esign_image_{i}"] = get_config_value(f"esign_image_{i}")
        
    return config_data

@app.put("/config/public", dependencies=[Depends(require_admin)])
def update_config_public(public_image_url: str = Form(...)):
    """Updates the public order list image URL."""
    if not public_image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE config SET value=:v WHERE key='public_image_url'"),
            {"v": public_image_url}
        )
    return {"message": "Public image URL updated successfully", "public_image_url": public_image_url}


@app.put("/config/esign_image/{image_number}", dependencies=[Depends(require_admin)])
def update_config_esign_image(image_number: int, image_url: str = Form(...)):
    """Updates a single Esign image URL (1 to 5)."""
    if image_number < 1 or image_number > 5:
        raise HTTPException(400, "Image number must be between 1 and 5.")
    if not image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    key = f"esign_image_{image_number}"
    
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE config SET value=:v WHERE key=:k"),
            {"k": key, "v": image_url}
        )
    return {"message": f"Esign Image {image_number} updated successfully", "image_url": image_url}


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM admins WHERE username=:u AND password=:p"),
            {"u": username, "p": password}
        ).mappings().first()
        if row:
            return {"token": ADMIN_TOKEN, "username": username}
    raise HTTPException(401, "Invalid credentials")

@app.get("/", tags=["health"])
def health():
    return {"ok": True, "service": "Order API"}

# ========== ORDERS (MODIFIED) ==========
@app.get("/orders", response_model=PageOut)
def list_orders(
    request: Request, 
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
    sort: str = "-id"
):
    is_admin = is_admin_request(request.headers)
    public_image_url = get_config_value('public_image_url')
    
    where = []
    params = {}
    if status:
        where.append("status=:st")
        params["st"] = status
    if q:
        where.append("(name LIKE :kw OR udid LIKE :kw)")
        params["kw"] = f"%{q}%"
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    order_sql = " ORDER BY id DESC" if sort == "-id" else " ORDER BY id ASC"
    offset = (page - 1) * page_size

    with engine.begin() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) AS c FROM orders{where_sql}"), params).scalar_one()
        rows = conn.execute(
            text(f"SELECT id, name, udid, image_url, status, download_link, price, created_at FROM orders{where_sql}{order_sql} LIMIT :limit OFFSET :offset"),
            {**params, "limit": page_size, "offset": offset}
        ).mappings().all()
        
        items = []
        for r in rows:
            item = dict(r)
            
            # UX/SECURITY: Replace actual image URL with dynamic public URL
            if not is_admin:
                item["image_url"] = public_image_url
            
            items.append(item)
            
        return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int):
    # Used by Admin Panel and Public Details Page
    with engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        if not row:
            raise HTTPException(404, "Order not found")
        return dict(row)

@app.post("/orders", response_model=OrderOut, tags=["public"])
async def create_order(
    name: str = Form(...),
    udid: str = Form(...),
    image: UploadFile = File(...)
):
    # Extract Price from Name field
    price_match = re.search(r'\$(\d+)', name)
    price = price_match.group(1) if price_match else 'N/A'
    
    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    name_safe = name.replace(' ', '_')
    fname = f"{int(datetime.datetime.utcnow().timestamp())}_{name_safe[:10]}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    
    content = await image.read()
    if not content:
        raise HTTPException(400, "Empty image")
    
    with open(fpath, "wb") as f:
        f.write(content)

    created_at = datetime.datetime.utcnow().isoformat()
    with engine.begin() as conn:
        res = conn.execute(text("""
            INSERT INTO orders(name, udid, image_url, price, download_link, status, created_at)
            VALUES (:name,:udid,:img,:price,:dlink,'pending',:ts)
        """), {
            "name": name, 
            "udid": udid, 
            "img": f"/uploads/{fname}", 
            "price": price, 
            "dlink": DEFAULT_DOWNLOAD_LINK,
            "ts": created_at
        })
        order_id = res.lastrowid
        row = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        return dict(row)

@app.put("/orders/{order_id}", response_model=OrderOut, dependencies=[Depends(require_admin)])
async def update_order(
    order_id: int,
    name: Optional[str] = Form(None),
    udid: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    download_link: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    with engine.begin() as conn:
        cur = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        if not cur:
            raise HTTPException(404, "Order not found")
        cur = dict(cur)

    img_url = cur["image_url"]
    if image is not None:
        ext = os.path.splitext(image.filename or "")[1] or ".jpg"
        fname = f"{int(datetime.datetime.utcnow().timestamp())}_{(name or cur['name']).replace(' ','_')}{ext}"
        fpath = os.path.join(UPLOAD_DIR, fname)
        data = await image.read()
        with open(fpath, "wb") as f:
            f.write(data)
        old = os.path.join(UPLOAD_DIR, os.path.basename(img_url))
        if os.path.exists(old):
            try: os.remove(old)
            except: pass
        img_url = f"/uploads/{fname}"

    new_name = name or cur["name"]
    new_udid = udid or cur["udid"]
    new_status = status or cur["status"]
    
    new_download_link = download_link if download_link is not None else cur["download_link"] 
    if new_download_link == '':
        new_download_link = '#'
    elif new_download_link is None:
         new_download_link = cur["download_link"] 
    
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE orders SET name=:n, udid=:u, image_url=:img, status=:st, download_link=:dl 
            WHERE id=:id
        """), {
            "n": new_name, 
            "u": new_udid, 
            "img": img_url, 
            "st": new_status, 
            "dl": new_download_link,
            "id": order_id
        })
        row = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        return dict(row)

@app.put("/orders/{order_id}/status", response_model=OrderOut, dependencies=[Depends(require_admin)])
async def update_order_status_by_bot(
    order_id: int,
    data: OrderUpdateStatus
):
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM orders WHERE id=:id"), {"id": order_id}).first()
        if not exists:
            raise HTTPException(404, "Order not found")

        conn.execute(text("""
            UPDATE orders SET status=:st WHERE id=:id
        """), {"st": data.status, "id": order_id})
        
        row = conn.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        if not row:
            raise HTTPException(404, "Order not found after update")
        return dict(row)

@app.delete("/orders/{order_id}", dependencies=[Depends(require_admin)])
def delete_order(order_id: int):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT image_url FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
        if not row:
            raise HTTPException(404, "Order not found")
        conn.execute(text("DELETE FROM orders WHERE id=:id"), {"id": order_id})
    try:
        f = os.path.join(UPLOAD_DIR, os.path.basename(row["image_url"]))
        if os.path.exists(f): os.remove(f)
    except: pass
    return {"status": "deleted"}