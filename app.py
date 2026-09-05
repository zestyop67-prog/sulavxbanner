import io
import os
import re
import json
import random
import logging
import urllib.request
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFont
import httpx
import requests

# ================= Logging =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ff_api")

# ================= Configuration =================
# Banner settings
AVATAR_ZOOM = 1.26
AVATAR_SHIFT_Y = 0
AVATAR_SHIFT_X = 0
BANNER_START_X = 0.25
BANNER_START_Y = 0.29
BANNER_END_X = 0.81
BANNER_END_Y = 0.65

# Fonts (optional - place in same directory)
FONT_MAIN = "arial_unicode_bold.otf"
FONT_CHEROKEE = "NotoSansCherokee.ttf"

# Prime badges (0-8)
PRIME_FILES = {i: f"prime{i}.png" for i in range(0, 9)}
PRIME8_FRAME_FILE = "prime8frame.png"

# Custom badges
CUSTOM_BADGE_FILES = {
    "vbadge1": "vbadge1.png",
    "vbadge2": "vbadge2.png",
    "vbadge3": "vbadge3.png",
    "vbadge4": "vbadge4.png",
    "gmbadge": "gmbadge.png",
    "cbadge": "cbadge.png",
    "probadge": "probadge.png",
}

# Custom frames
CUSTOM_FRAME_FILES = {
    "prime8frame": "prime8frame.png",
    "ebadgeframe": "ebadgeframe.png",
}

# Outfit settings
OUTFIT_BACKGROUND = "outfit.png"
ICON_SIZE = (95, 95)
CHARACTER_RENDER_SIZE = (700, 700)
FALLBACK_IDS = ["211000000", "214000000", "208000000", "203000000", "204000000", "205000000", "212000000"]
DEFAULT_AVATAR_ID = "710034057"

# Outfit slot positions (X, Y) on the outfit.png template
HEX_POSITIONS = {
    "mask": (990, 420),      # Top right
    "shirt": (190, 90),      # Top left
    "pants": (40, 420),      # Bottom left
    "shoes": (840, 90),      # Top right
    "emote": (40, 230),      # Middle left (faceprint)
    "armor": (990, 230),     # Middle right
    "weapon": (190, 560),    # Bottom left
    "pet": (840, 560)        # Bottom right
}

# API URLs
INFO_API_URL = "https://info.killersharmabot.online/player-info"
CDN_URL = "https://cdn.jsdelivr.net/gh/ShahGCreator/icon@main/PNG"

# Load data.json for item info
DATA_JSON_PATH = "data.json"
item_db = []
if os.path.exists(DATA_JSON_PATH):
    try:
        with open(DATA_JSON_PATH, 'r', encoding='utf-8') as f:
            item_db = json.load(f)
        logger.info(f"Loaded {len(item_db)} items from data.json")
    except Exception as e:
        logger.warning(f"Failed to load data.json: {e}")

# ================= FastAPI App =================
app = FastAPI(title="FF Banner & Outfit API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ================= Client & State =================
@app.on_event("startup")
async def startup():
    app.state.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    app.state.badges = {}
    app.state.frames = {}
    
    # Load prime badges
    for lvl, path in PRIME_FILES.items():
        if os.path.exists(path):
            try:
                app.state.badges[lvl] = Image.open(path).convert("RGBA")
                logger.info(f"Loaded badge: {path}")
            except Exception as e:
                logger.warning(f"Failed {path}: {e}")
    
    # Load custom badges
    for name, path in CUSTOM_BADGE_FILES.items():
        if os.path.exists(path):
            try:
                app.state.badges[name] = Image.open(path).convert("RGBA")
                logger.info(f"Loaded badge: {path}")
            except Exception as e:
                logger.warning(f"Failed {path}: {e}")
    
    # Load frames
    for name, path in CUSTOM_FRAME_FILES.items():
        if os.path.exists(path):
            try:
                app.state.frames[name] = Image.open(path).convert("RGBA")
                logger.info(f"Loaded frame: {path}")
            except Exception as e:
                logger.warning(f"Failed {path}: {e}")
    
    app.state.outfit_available = os.path.exists(OUTFIT_BACKGROUND)
    if not app.state.outfit_available:
        logger.warning(f"Outfit background missing: {OUTFIT_BACKGROUND}")

@app.on_event("shutdown")
async def shutdown():
    await app.state.client.aclose()

# ================= Helper Functions =================
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b\uFEFF\uf8ff]', '', str(text))
    return ' '.join(text.split())

def load_unicode_font(size: int, font_file: str = FONT_MAIN):
    try:
        font_path = os.path.join(os.path.dirname(__file__), font_file)
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    except Exception:
        pass
    return ImageFont.load_default()

def is_cherokee(c: str) -> bool:
    return 0x13A0 <= ord(c) <= 0x13FF or 0xAB70 <= ord(c) <= 0xABBF

def draw_text_stroked(draw, x, y, text, f_main, f_alt, stroke=3):
    if not text:
        return
    cx = x
    for ch in text:
        font = f_alt if is_cherokee(ch) else f_main
        for dx in range(-stroke, stroke+1):
            for dy in range(-stroke, stroke+1):
                draw.text((cx+dx, y+dy), ch, font=font, fill="black")
        draw.text((cx, y), ch, font=font, fill="white")
        cx += font.getlength(ch)

async def fetch_image_bytes(item_id: str) -> Optional[bytes]:
    if not item_id or str(item_id).lower() in ("0", "none", "null"):
        return None
    url = f"{CDN_URL}/{item_id}.png"
    try:
        resp = await app.state.client.get(url)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Fetch error {item_id}: {e}")
    return None

def bytes_to_image(img_bytes: Optional[bytes]) -> Image.Image:
    if img_bytes:
        try:
            return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        except Exception:
            pass
    return Image.new("RGBA", (400, 400), (200, 200, 200, 255))

def sync_fetch_url(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as e:
        logger.warning(f"Sync fetch error {url}: {e}")
        return None

def fetch_icon(icon_id, size=ICON_SIZE, is_character=False):
    try:
        if is_character:
            url = f"https://raw.githubusercontent.com/danggerr88-alt/danger-character-api/main/pngs/{icon_id}.png"
            data = sync_fetch_url(url)
            if data:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                w, h = img.size
                ratio = min(size[0] / w, size[1] / h)
                new_size = (int(w * ratio), int(h * ratio))
                return img.resize(new_size, Image.Resampling.LANCZOS)
        ids_to_try = [str(icon_id)] if icon_id and str(icon_id) != "0" else []
        for fid in FALLBACK_IDS:
            if fid not in ids_to_try:
                ids_to_try.append(fid)
        for i in ids_to_try:
            url = f"https://iconapi.wasmer.app/{i}"
            data = sync_fetch_url(url)
            if data:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img.resize(size, Image.Resampling.LANCZOS)
    except Exception as e:
        logger.warning(f"Icon fetch error: {e}")
    return None

async def fetch_player_data(uid: str) -> Dict[str, Any]:
    """Fetch player data from API."""
    try:
        resp = await app.state.client.get(f"{INFO_API_URL}?uid={uid}")
        if resp.status_code != 200:
            raise HTTPException(502, f"API error: {resp.status_code}")
        return resp.json()
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch player data: {e}")

def extract_player_data(data: Dict[str, Any]) -> Dict[str, Any]:
    profile = data.get("profileInfo", {})
    clan = data.get("clanBasicInfo", {})
    basic = data.get("basicInfo", {})
    prime_info = data.get("primeInfo", {})

    name = clean_text(profile.get("nickname") or basic.get("nickname") or "Unknown")
    level = str(profile.get("level") or basic.get("level") or 0)
    guild = clean_text(clan.get("clanName", ""))
    headPic = str(profile.get("headPic") or basic.get("headPic") or "")
    banner_id = str(profile.get("bannerId") or basic.get("bannerId") or "")

    # Prime level extraction
    prime_level = None
    if "primeLevel" in prime_info:
        prime_level = prime_info.get("primeLevel")
    elif "primeLevel" in profile:
        prime_level = profile.get("primeLevel")
    elif "primeLevel" in basic:
        prime_level = basic.get("primeLevel")
    elif "primeLevel" in data:
        prime_level = data.get("primeLevel")
    if prime_level is None:
        prime_level = 0
    try:
        prime_level = max(0, min(8, int(prime_level)))
    except:
        prime_level = 0

    clothes = profile.get("clothes") or []
    weapon_skins = basic.get("weaponSkinShows") or []
    weapon = weapon_skins[0] if weapon_skins else None
    pet = data.get("petInfo", {}).get("skinId")
    character = profile.get("avatarId") or DEFAULT_AVATAR_ID

    return {
        "name": name,
        "level": level,
        "guild": guild,
        "headPic": headPic,
        "banner_id": banner_id,
        "prime_level": prime_level,
        "clothes": clothes,
        "weapon": weapon,
        "pet": pet,
        "character": character,
        "exp": basic.get("exp", 0)
    }

def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    if len(words) <= 1:
        return [text]
    for i in range(1, len(words)):
        line1 = ' '.join(words[:i])
        line2 = ' '.join(words[i:])
        try:
            if font.getlength(line1) <= max_width and font.getlength(line2) <= max_width:
                return [line1, line2]
        except:
            pass
    return [text]

# ================= Banner Generation =================
def generate_banner_image(avatar_bytes: Optional[bytes], banner_bytes: Optional[bytes],
                          player: Dict[str, Any], badge_name: Optional[str] = None,
                          frame_name: Optional[str] = None) -> io.BytesIO:
    TARGET = 400
    
    avatar = bytes_to_image(avatar_bytes)
    try:
        zoom = int(TARGET * AVATAR_ZOOM)
        avatar = avatar.resize((zoom, zoom), Image.LANCZOS)
        left = (zoom - TARGET) // 2 - AVATAR_SHIFT_X
        top = (zoom - TARGET) // 2 - AVATAR_SHIFT_Y
        avatar = avatar.crop((left, top, left + TARGET, top + TARGET))
    except:
        avatar = Image.new("RGBA", (TARGET, TARGET), (100, 100, 100, 255))

    # ---- Frame ----
    used_frame = None
    if frame_name and frame_name in app.state.frames:
        used_frame = app.state.frames[frame_name]
    elif player.get("prime_level") == 8 and "prime8frame" in app.state.frames:
        used_frame = app.state.frames["prime8frame"]
    if used_frame:
        try:
            frame = used_frame.resize(avatar.size, Image.LANCZOS)
            avatar = Image.alpha_composite(avatar, frame)
        except Exception as e:
            logger.warning(f"Frame overlay failed: {e}")

    # ---- Badge ----
    used_badge = None
    if badge_name and badge_name in app.state.badges:
        used_badge = app.state.badges[badge_name]
    else:
        prime_lvl = player.get("prime_level", 0)
        if prime_lvl in app.state.badges:
            used_badge = app.state.badges[prime_lvl]
    if used_badge:
        try:
            badge_size = 70
            badge = used_badge.resize((badge_size, badge_size), Image.LANCZOS)
            x_pos = avatar.width - badge_size - 10
            y_pos = 10
            avatar.paste(badge, (x_pos, y_pos), badge)
        except Exception as e:
            logger.warning(f"Badge overlay failed: {e}")

    banner = bytes_to_image(banner_bytes)
    try:
        w, h = banner.size
        if w > 100 and h > 100:
            banner = banner.rotate(3, expand=True)
            w, h = banner.size
            l = w * BANNER_START_X
            t = h * BANNER_START_Y
            r = w * BANNER_END_X
            b = h * BANNER_END_Y
            banner = banner.crop((l, t, r, b))
        w, h = banner.size
        new_w = int(TARGET * (w / h) * 2) if h else 800
        banner = banner.resize((new_w, TARGET), Image.LANCZOS)
    except:
        banner = Image.new("RGBA", (800, TARGET), (100, 100, 100, 255))

    final_w = TARGET + banner.width
    combined = Image.new("RGBA", (final_w, TARGET), (0, 0, 0, 255))
    combined.paste(avatar, (0, 0))
    combined.paste(banner, (TARGET, 0))
    draw = ImageDraw.Draw(combined)

    name_x = TARGET + 65
    max_width = banner.width - 100
    if max_width < 100:
        max_width = 300

    font_name = load_unicode_font(110)
    font_name_che = load_unicode_font(110, FONT_CHEROKEE)
    font_guild = load_unicode_font(80)
    font_guild_che = load_unicode_font(80, FONT_CHEROKEE)
    font_level = load_unicode_font(50)

    y = 40
    for line in wrap_text(player.get("name", "Unknown"), font_name, max_width):
        draw_text_stroked(draw, name_x, y, line, font_name, font_name_che, 4)
        y += 85
    y += 60
    if player.get("guild"):
        draw_text_stroked(draw, name_x, y, player["guild"], font_guild, font_guild_che, 3)

    lvl_text = f"Lvl.{player.get('level', '0')}"
    try:
        bbox = draw.textbbox((0, 0), lvl_text, font=font_level)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rectangle([final_w - w - 60, TARGET - h - 50, final_w, TARGET], fill="black")
        draw.text((final_w - w - 30, TARGET - h - 40), lvl_text, font=font_level, fill="white")
    except:
        pass

    img_io = io.BytesIO()
    combined.save(img_io, "PNG")
    img_io.seek(0)
    return img_io

def generate_outfit_image(outfit_data: Dict[str, Any]) -> io.BytesIO:
    if not os.path.exists(OUTFIT_BACKGROUND):
        raise FileNotFoundError(f"Missing {OUTFIT_BACKGROUND}")
    
    canvas = Image.open(OUTFIT_BACKGROUND).convert("RGBA")
    
    slots = {
        "mask": outfit_data.get("mask"),
        "shirt": outfit_data.get("shirt"),
        "pants": outfit_data.get("pants"),
        "shoes": outfit_data.get("shoes"),
        "emote": outfit_data.get("emote"),
        "armor": outfit_data.get("armor"),
        "weapon": outfit_data.get("weapon"),
        "pet": outfit_data.get("pet"),
        "character": outfit_data.get("character", DEFAULT_AVATAR_ID)
    }
    
    for slot, item_id in slots.items():
        if not item_id:
            continue
            
        if slot == "character":
            img = fetch_icon(item_id, size=CHARACTER_RENDER_SIZE, is_character=True)
            if img:
                w, h = img.size
                cx = canvas.width // 2
                by = canvas.height - 20
                pos = (cx - w // 2, by - h)
        else:
            img = fetch_icon(item_id)
            if img:
                pos = HEX_POSITIONS.get(slot)
                
        if img and pos:
            canvas.paste(img, pos, img)
    
    img_io = io.BytesIO()
    canvas.save(img_io, "PNG")
    img_io.seek(0)
    return img_io

# ================= Item Info & Image =================
def get_item_info(item_id: str) -> Optional[Dict]:
    """Get item info from data.json by ID."""
    for item in item_db:
        if str(item.get("itemID")) == str(item_id):
            return item
    return None

async def get_item_image_bytes(item_id: str) -> Optional[bytes]:
    """Fetch item image from CDN."""
    return await fetch_image_bytes(item_id)

# ================= API Endpoints =================
@app.get("/")
async def root():
    return {
        "name": "FF Banner & Outfit API",
        "version": "2.0",
        "endpoints": {
            "/": "This help",
            "/health": "Health check",
            "/player-info": "Get raw player data",
            "/banner": "Generate player banner",
            "/outfit": "Generate outfit image",
            "/item-info": "Get item info from data.json",
            "/item-image": "Get item image from CDN"
        },
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/player-info")
async def player_info(uid: str = Query(...)):
    """Get raw player data from the info API."""
    data = await fetch_player_data(uid)
    return JSONResponse(content=data)

@app.get("/banner")
async def generate_banner(
    uid: str = Query(..., description="Player UID"),
    badge: Optional[str] = Query(None, description="Custom badge (probadge, gmbadge, vbadge1, prime0..prime8)"),
    frame: Optional[str] = Query(None, description="Custom frame (prime8frame, ebadgeframe)")
):
    """Generate a player profile banner."""
    try:
        data = await fetch_player_data(uid)
        player = extract_player_data(data)
        
        avatar_bytes, banner_bytes = await asyncio.gather(
            fetch_image_bytes(player["headPic"]),
            fetch_image_bytes(player["banner_id"])
        )
        
        img_io = generate_banner_image(avatar_bytes, banner_bytes, player, badge, frame)
        return Response(content=img_io.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=300"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Banner generation failed for {uid}")
        raise HTTPException(500, str(e))

@app.get("/outfit")
async def generate_outfit(
    uid: str = Query(..., description="Player UID"),
    head: Optional[str] = Query(None, description="Override character ID"),
    mask: Optional[str] = Query(None, description="Override mask ID"),
    top: Optional[str] = Query(None, description="Override shirt/top ID"),
    pants: Optional[str] = Query(None, description="Override pants ID"),
    shoes: Optional[str] = Query(None, description="Override shoes ID"),
    faceprint: Optional[str] = Query(None, description="Override emote/faceprint ID"),
    paint: Optional[str] = Query(None, description="Override armor/paint ID"),
    weapon: Optional[str] = Query(None, description="Override weapon ID"),
    pet: Optional[str] = Query(None, description="Override pet ID")
):
    """Generate an outfit image with optional custom overrides."""
    try:
        data = await fetch_player_data(uid)
        player = extract_player_data(data)
        clothes = player.get("clothes", [])
        
        outfit_data = {
            "character": head or player.get("character"),
            "mask": mask or (clothes[0] if len(clothes) > 0 else None),
            "shirt": top or (clothes[1] if len(clothes) > 1 else None),
            "pants": pants or (clothes[2] if len(clothes) > 2 else None),
            "shoes": shoes or (clothes[3] if len(clothes) > 3 else None),
            "emote": faceprint or (clothes[4] if len(clothes) > 4 else None),
            "armor": paint or (clothes[5] if len(clothes) > 5 else None),
            "weapon": weapon or player.get("weapon"),
            "pet": pet or player.get("pet")
        }
        
        img_io = generate_outfit_image(outfit_data)
        return Response(content=img_io.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=300"})
    except FileNotFoundError as e:
        raise HTTPException(503, f"Missing background file: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Outfit generation failed for {uid}")
        raise HTTPException(500, str(e))

@app.get("/item-info")
async def item_info(item_id: str = Query(..., description="Item ID")):
    """Get item information from data.json."""
    item = get_item_info(item_id)
    if item:
        return JSONResponse(content={"success": True, "item": item})
    else:
        raise HTTPException(404, f"Item {item_id} not found in data.json")

@app.get("/item-image")
async def item_image(
    item_id: str = Query(..., description="Item ID"),
    download: bool = Query(False, description="Download the image")
):
    """Get item image from CDN."""
    img_bytes = await get_item_image_bytes(item_id)
    if img_bytes:
        if download:
            return Response(content=img_bytes, media_type="image/png",
                            headers={"Content-Disposition": f"attachment; filename=item_{item_id}.png"})
        else:
            return Response(content=img_bytes, media_type="image/png")
    else:
        raise HTTPException(404, f"Image for item {item_id} not found")

@app.get("/badges")
async def list_badges():
    """List all available badges."""
    prime_list = [{"name": f"prime{i}", "file": f"prime{i}.png", "type": "prime"} for i in range(9)]
    custom_list = [{"name": name, "file": fname, "type": "custom"} for name, fname in CUSTOM_BADGE_FILES.items()]
    available = []
    for b in prime_list + custom_list:
        if os.path.exists(b["file"]):
            available.append(b)
    return {"badges": available}

@app.get("/frames")
async def list_frames():
    """List all available frames."""
    available = []
    for name, fname in CUSTOM_FRAME_FILES.items():
        if os.path.exists(fname):
            available.append({"name": name, "file": fname})
    return {"frames": available}

@app.get("/prime-levels")
async def prime_levels():
    """List prime levels."""
    return {"levels": [{"level": i, "badge": f"prime{i}.png", "frame": "prime8frame.png" if i == 8 else None} for i in range(9)]}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)