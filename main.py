import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

IMAGES_DIR = Path("images")
META_PATH = Path("metadata.json")

IMAGES_DIR.mkdir(exist_ok=True)

app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# --------------------
# utils
# --------------------

def load_images():
    if not META_PATH.exists():
        return []
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def save_images(data):
    META_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(
        "upload.html",
        {"request": request}
    )


@app.post("/upload")
async def upload_image(
    image: UploadFile,
    title: str = Form(...),
    meta: str = Form("")
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

    return RedirectResponse("/", status_code=303)

# --------------------
# ✏️ 수정 (title / meta만)
# --------------------

@app.get("/edit/{image_name}", response_class=HTMLResponse)
def edit_page(request: Request, image_name: str):
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
    meta: str = Form("")
):
    images = load_images()

    for item in images:
        if item["image"] == image_name:
            item["title"] = title
            item["meta"] = [m.strip() for m in meta.split(",") if m.strip()]
            break

    save_images(images)
    return RedirectResponse("/", status_code=303)

# --------------------
# 🗑️ 삭제 (파일 + 메타)
# --------------------

@app.post("/delete/{image_name}")
def delete_image(image_name: str):
    images = load_images()

    # metadata에서 제거
    images = [i for i in images if i["image"] != image_name]
    save_images(images)

    # 실제 파일 삭제
    image_path = IMAGES_DIR / image_name
    if image_path.exists():
        image_path.unlink()

    return RedirectResponse("/", status_code=303)
