import json
import uuid
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone, timedelta

import dotenv
from jose import jwt, JWTError

from fastapi import FastAPI, Request, UploadFile, Form, HTTPException, status, Cookie, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --------------------
# init
# --------------------

dotenv.load_dotenv()

# Logging
os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = TimedRotatingFileHandler(
    'logs/info.log', 
    when="midnight", 
    interval=1, 
    backupCount=7,
    encoding='utf-8'
)

handler.suffix = "%Y-%m-%d"

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger.addHandler(handler)

# FastAPI
app = FastAPI()

# Path
IMAGES_DIR = Path("images")
META_PATH = Path("metadata.json")
USER_PATH = Path("user.json")

IMAGES_DIR.mkdir(exist_ok=True)

app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Jinja
templates = Jinja2Templates(directory="templates")

# --------------------
# utils
# --------------------

IMAGES_CACHE = []
IMAGES_MTIME = 0

def load_images_cached():
    global IMAGES_CACHE, IMAGES_MTIME

    try:
        mtime = META_PATH.stat().st_mtime
    except FileNotFoundError:
        return []

    if IMAGES_MTIME != mtime:
        with META_PATH.open("r", encoding="utf-8") as f:
            IMAGES_CACHE = json.load(f)
        IMAGES_MTIME = mtime

    return IMAGES_CACHE

USERS_CACHE = {}
USERS_MTIME = 0

def load_users_cached():
    global USERS_CACHE, USERS_MTIME

    try:
        mtime = USER_PATH.stat().st_mtime
    except FileNotFoundError:
        return {}

    if USERS_MTIME != mtime:
        with USER_PATH.open("r", encoding="utf-8") as f:
            USERS_CACHE = json.load(f)
        USERS_MTIME = mtime

    return USERS_CACHE


def load_images() -> list:
    return load_images_cached()

def save_images(data):
    global IMAGES_CACHE, IMAGES_MTIME

    META_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 캐시 업데이트
    IMAGES_CACHE = data

    # 파일 시스템에서 다시 mtime 가져오기
    try:
        IMAGES_MTIME = META_PATH.stat().st_mtime
    except FileNotFoundError:
        IMAGES_MTIME = 0


def load_users():
    return load_users_cached()

def verify_permission(required_role: str):
    async def _verify(access_token: str = Cookie(None)):
        users: dict = load_users()

        if not access_token:
            raise HTTPException(status_code=401, detail="Missing JWT token")

        try:
            payload = jwt.decode(access_token, os.environ["JWT_SECRET"], algorithms=["HS256"])
            user_token = payload.get("sub")
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid JWT token")

        user = users.get(user_token)

        if not user:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a registered token")

        permissions = user.get("permission", [])

        if required_role in permissions:
            return user.get("name", "Unknown User")
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
            
    return _verify


def normalize(s: str) -> str:
    return s.replace(" ", "").lower()

# --------------------
# routes
# --------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = ""):
    images = load_images()
    query = normalize(q)

    if query:
        images = [
            m for m in images
            if query in normalize(m["title"])
            or any(query in normalize(meta) for meta in m["meta"])
        ]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "images": images,
            "query": q
        }
    )

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/getjwt")
def get_jwt(
    request: Request, 
    token: str = Form(...),
    users: dict = Depends(load_users)
):
    user_info = users.get(token)

    if not user_info:
        return templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Invalid Key!"
        })

    expire = datetime.now(timezone.utc) + timedelta(days=365)
    to_encode = {"sub": token, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, os.environ["JWT_SECRET"], algorithm="HS256")

    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(
        key="access_token", 
        value=encoded_jwt, 
        httponly=True, 
        max_age=31536000,
        samesite="lax"
    )
    
    logger.info(f"User {user_info.get('name')} logged in with token.")
    
    return redirect

@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    user_name: str = Depends(verify_permission("upload"))
):
    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request,
        }
    )


@app.post("/upload")
async def upload_image(
    image: UploadFile,
    title: str = Form(...),
    meta: str = Form(""),
    user_name: str = Depends(verify_permission("upload"))
):
    ext = Path(image.filename).suffix
    filename = f"{uuid.uuid4().hex}{ext}"

    image_path = IMAGES_DIR / filename
    with open(image_path, "wb") as f:
        f.write(await image.read())

    images = load_images()
    images.append({
        "image": filename,
        "title": title,
        "meta": [m.strip() for m in meta.split(",") if m.strip()]
    })
    save_images(images)

    logger.info(f"Upload image ({filename}, {user_name})")

    return RedirectResponse("/", status_code=303)

@app.get("/edit/{image_name}", response_class=HTMLResponse)
def edit_page(
    request: Request,
    image_name: str,
    user_name: str = Depends(verify_permission("edit"))
):
    images = load_images()
    item = next((i for i in images if i["image"] == image_name), None)

    if not item:
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "item": item
        }
    )


@app.post("/edit/{image_name}")
def edit_image(
    image_name: str,
    title: str = Form(...),
    meta: str = Form(""),
    user_name: str = Depends(verify_permission("edit"))
):
    images = load_images()

    for item in images:
        if item["image"] == image_name:
            item["title"] = title
            item["meta"] = [m.strip() for m in meta.split(",") if m.strip()]
            break

    save_images(images)

    logger.info(f"Edit image ({image_name}, {user_name})")

    return RedirectResponse("/", status_code=303)

# --------------------
# 🗑️ 삭제 (파일 + 메타)
# --------------------

@app.post("/delete/{image_name}")
def delete_image(image_name: str, user_name: str = Depends(verify_permission("delete"))):
    images = load_images()

    # metadata에서 제거
    images = [i for i in images if i["image"] != image_name]
    save_images(images)

    # 실제 파일 삭제
    # image_path = IMAGES_DIR / image_name
    # if image_path.exists():
    #     image_path.unlink()
    # 메타데이터에서만 삭제해서 검색만 안되도록

    logger.info(f"Delete image ({image_name}, {user_name})")

    return RedirectResponse("/", status_code=303)
