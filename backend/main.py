import os, datetime
import re 
import json 
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query, Request 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, StaticPool
from sqlalchemy.exc import IntegrityError
from urllib.parse import quote_plus
from math import ceil

# ========== ENV & DB SETUP ==========
# ⚠️ ជំនួស [YOUR_PASSWORD] ដោយពាក្យសម្ងាត់ពិតប្រាកដរបស់អ្នក។
NEW_SAFE_PASSWORD = "nhoy@2003?ww" 
SAFE_PASSWORD_QUOTED = quote_plus(NEW_SAFE_PASSWORD) 

# FIX: ប្រើ Project Reference ID ថ្មី wsejiqtuysgbmobertco
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    f"postgresql+asyncpg://postgres:{SAFE_PASSWORD_QUOTED}@db.wsejiqtuysgbmobertco.supabase.co:5432/postgres"
)

# Setup SQLAlchemy Async Engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_recycle=3600
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# UPLOAD_DIR and Admin Token
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "nhoyhub_admin_2025")
DEFAULT_CART_IMAGE = "https://i.pinimg.com/736x/67/74/40/67744063f3ce36103729fb5ed2edc98e.jpg" 
DEFAULT_ESIGN_IMAGE = "https://via.placeholder.com/400x200/007bff/ffffff?text=Esign+Image"
DEFAULT_DOWNLOAD_LINK = "#"


app = FastAPI(title="NhoyHub Order API", version="4.0 - PostgreSQL")

# CORS Policy: Allowing all origins for development flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Dependency to get Async Database Session
async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session

# ========== DB INITIALIZATION (SQL Logic) ==========
async def create_db_tables(conn):
    
    # ORDERS TABLE
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            udid TEXT NOT NULL,
            image_url TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            download_link TEXT DEFAULT '#',
            price VARCHAR(10) DEFAULT 'N/A',
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """))
    # ADMINS TABLE
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(100) NOT NULL
        );
    """))
    # CONFIG TABLE
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS config (
            key VARCHAR(50) PRIMARY KEY,
            value TEXT NOT NULL
        );
    """))

    # Initial data population
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Default Admin
            admin_exists = await session.scalar(text("SELECT 1 FROM admins WHERE username='admin'"))
            if not admin_exists:
                await session.execute(
                    text("INSERT INTO admins(username, password) VALUES('admin', '1234')")
                )
            
            # 2. Default Configs
            config_keys = {
                'public_image_url': DEFAULT_CART_IMAGE,
                'esign_image_1': DEFAULT_ESIGN_IMAGE + " 1",
                'esign_image_2': DEFAULT_ESIGN_IMAGE + " 2",
                'esign_image_3': DEFAULT_ESIGN_IMAGE + " 3",
                'esign_image_4': DEFAULT_ESIGN_IMAGE + " 4",
                'esign_image_5': DEFAULT_ESIGN_IMAGE + " 5",
            }
            
            for key, default_value in config_keys.items():
                config_exists = await session.scalar(text("SELECT 1 FROM config WHERE key=:k"), {"k": key})
                if not config_exists:
                    await session.execute(
                        text("INSERT INTO config(key, value) VALUES(:k, :v)"),
                        {"k": key, "v": default_value}
                    )
        await session.commit()


@app.on_event("startup")
async def startup_event():
    """Initializes database structure on startup."""
    try:
        async with engine.begin() as conn:
            await create_db_tables(conn)
            print("Database setup complete: Tables and default data checked/created.")
    except Exception as e:
        print(f"FATAL DATABASE ERROR ON STARTUP: {e}")
        # raise Exception("Database failed to initialize.")


# ========== MODELS & UTILS (Keep existing models) ==========
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


# ========== AUTH & HELPERS (Keep existing logic) ==========
async def get_config_value(key: str, session: AsyncSession):
    result = await session.scalar(text("SELECT value FROM config WHERE key=:k"), {"k": key})
    if result is None:
        if key == 'public_image_url':
            return DEFAULT_CART_IMAGE
        elif 'esign_image_' in key:
            return DEFAULT_ESIGN_IMAGE
        return "#"
    return result

@app.post("/login")
async def login(session: AsyncSession = Depends(get_db_session), username: str = Form(...), password: str = Form(...)):
    admin_data = await session.execute(
        text("SELECT * FROM admins WHERE username=:u AND password=:p"),
        {"u": username, "p": password}
    )
    if admin_data.first():
        return {"token": ADMIN_TOKEN, "username": username} 
    raise HTTPException(401, "Invalid credentials")

@app.get("/", tags=["health"])
async def health():
    try:
        async with engine.begin() as conn:
             await conn.execute(text("SELECT 1"))
        return {"ok": True, "db": "PostgreSQL Connected ✅"}
    except Exception as e:
        raise HTTPException(503, detail="Service Unavailable: Database connection failed.")


# ========== CONFIG ENDPOINTS (FIXED update_config_esign_image) ==========
@app.get("/config", dependencies=[Depends(require_admin)])
async def get_config(session: AsyncSession = Depends(get_db_session)):
    """Returns current configuration (Admin only)."""
    config_data = {
        "public_image_url": await get_config_value('public_image_url', session),
    }
    for i in range(1, 6):
        config_data[f"esign_image_{i}"] = await get_config_value(f"esign_image_{i}", session)
        
    return config_data

@app.put("/config/public", dependencies=[Depends(require_admin)])
async def update_config_public(session: AsyncSession = Depends(get_db_session), public_image_url: str = Form(...)):
    """Updates the public order list image URL."""
    if not public_image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    await session.execute(
        text("""
            INSERT INTO config(key, value) VALUES('public_image_url', :v)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"v": public_image_url}
    )
    await session.commit()
    return {"message": "Public image URL updated successfully", "public_image_url": public_image_url}

# FIX: កែតម្រូវលំដាប់ Arguments
@app.put("/config/esign_image/{image_number}", dependencies=[Depends(require_admin)])
async def update_config_esign_image(
    image_number: int, 
    image_url: str = Form(...),
    session: AsyncSession = Depends(get_db_session)
):
    """Updates a single Esign image URL (1 to 5)."""
    if image_number < 1 or image_number > 5:
        raise HTTPException(400, "Image number must be between 1 and 5.")
    if not image_url:
        raise HTTPException(400, "Image URL cannot be empty.")
        
    key = f"esign_image_{image_number}"
    
    await session.execute(
        text("""
            INSERT INTO config(key, value) VALUES(:k, :v)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"k": key, "v": image_url}
    )
    await session.commit()
    return {"message": f"Esign Image {image_number} updated successfully", "image_url": image_url}


# ========== ORDERS (Keep existing CRUD/Pagination logic) ==========
@app.post("/orders", response_model=OrderOut)
async def create_order(session: AsyncSession = Depends(get_db_session), name: str = Form(...), udid: str = Form(...), image: UploadFile = File(...)):
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
        "download_link": DEFAULT_DOWNLOAD_LINK,
        "price": price,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    
    result = await session.execute(
        text("""
            INSERT INTO orders(name, udid, image_url, status, download_link, price, created_at)
            VALUES (:name, :udid, :img, :status, :dlink, :price, :ts) RETURNING id
        """), {
            "name": data["name"], 
            "udid": data["udid"], 
            "img": data["image_url"], 
            "status": data["status"],
            "dlink": data["download_link"],
            "price": data["price"],
            "ts": data["created_at"]
        }
    )
    order_id = result.scalar_one()
    await session.commit()
    
    # Fetch and return the full created row
    row = await session.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id})
    row_dict = row.mappings().first()
    return row_dict


@app.get("/orders", response_model=PageOut)
async def list_orders(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    status: Optional[str] = Query(None),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
    sort: str = "-id"
):
    is_admin = is_admin_request(request.headers)
    page = max(page, 1)
    page_size = max(min(page_size, 50), 1)
    
    where_clauses = []
    params = {}
    
    if status:
        where_clauses.append("status = :status")
        params["status"] = status
    
    if q:
        # Use ILIKE for case-insensitive search in Postgres
        where_clauses.append("(name ILIKE :q OR udid ILIKE :q)") 
        params["q"] = f"%{q}%"
    
    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    
    # 1. Get Total Count
    count_query = text(f"SELECT COUNT(*) FROM orders {where_sql}")
    total_count = await session.scalar(count_query, params)
    
    # 2. Determine sorting
    sort_column = "id"
    sort_order = "DESC" if sort == "-id" else "ASC"
    offset = (page - 1) * page_size
    
    # 3. Fetch data
    fetch_query = text(f"""
        SELECT * FROM orders {where_sql}
        ORDER BY {sort_column} {sort_order}
        LIMIT :limit OFFSET :offset
    """)
    rows = await session.execute(fetch_query, {**params, "limit": page_size, "offset": offset})
    
    public_image_url = await get_config_value('public_image_url', session)
    
    items = []
    for r in rows.mappings():
        item = dict(r)
        
        # SECURITY/UX: Replace payment image for public users
        if not is_admin:
            item["image_url"] = public_image_url
        
        items.append(item)
        
    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size
    }


@app.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, session: AsyncSession = Depends(get_db_session)):
    row = await session.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id})
    row_dict = row.mappings().first()
    
    if not row_dict:
        raise HTTPException(404, "Order not found")
    return row_dict


@app.put("/orders/{order_id}", dependencies=[Depends(require_admin)], response_model=OrderOut)
async def update_order(
    order_id: int, 
    session: AsyncSession = Depends(get_db_session),
    name: Optional[str] = Form(None),
    udid: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    download_link: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    # Fetch current order details
    current_row = await session.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id})
    current_order = current_row.mappings().first()
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
        update_data["download_link"] = download_link or "#" # Save '#' if link is cleared

    # Perform Update
    set_clauses = [f"{k}=:{k}" for k in update_data.keys()]
    update_query = text(f"""
        UPDATE orders SET {', '.join(set_clauses)} WHERE id = :id
    """)
    
    await session.execute(update_query, {**update_data, "id": order_id})
    await session.commit()
    
    # Return Updated Row
    row = await session.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id})
    return row.mappings().first()


@app.delete("/orders/{order_id}", dependencies=[Depends(require_admin)])
async def delete_order(order_id: int, session: AsyncSession = Depends(get_db_session)):
    
    await session.execute(text("DELETE FROM orders WHERE id=:id"), {"id": order_id})
    await session.commit()
    return {"deleted": True}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")