#!/usr/bin/env python3
"""Build 州 hex fills from purple boundaries on Tan-262 分幅 sheets.

Per sheet (georeferenced by printed degree grid):
  1. Detect purple 州 walls.
  2. Flood from that sheet's primary 州/郡 seats through sheet land until
     purple OR a max distance — never the whole sheet rectangle.
  3. Splat onto the 500×500 hex grid only on real land (coast ∧ ¬underlay-sea).

Does NOT paint ocean, 羌胡/鲜卑, or empty west outside purple reach.
No sheet JPEG texture in the repo.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

LON_MIN, LON_MAX = 94.662, 130.0
LAT_MIN, LAT_MAX = 15.55, 42.8
MAP_W = MAP_H = 500

CANON = {
    "交州",
    "扬州",
    "荆州",
    "益州",
    "司州",
    "兖州",
    "豫州",
    "青州",
    "徐州",
    "幽州",
    "凉州",
    "雍州",
    "冀州",
    "并州",
}

ALIAS = {
    "司隶": "司州",
    "蜀郡益州": "益州",
    "燕国幽州": "幽州",
    "扶柳冀州": "冀州",
    "南郡荆州": "荆州",
    "湖北荆州": "荆州",
    "丹阳郡扬州": "扬州",
    "下邳郡徐州": "徐州",
    "齐国州": "青州",
}

SHEET_PRIMARY = {
    "03-jiaozhou.jpg": ("交州",),
    "04-yangzhou-wu.jpg": ("扬州",),
    "05-jingzhou-wu.jpg": ("荆州",),
    "06-yizhou-north.jpg": ("益州", "雍州"),
    "07-yizhou-south.jpg": ("益州",),
    "09-sizhou.jpg": ("司州",),
    "10-yan-yu-yang.jpg": ("兖州", "豫州"),
    "11-qing-xu.jpg": ("青州", "徐州"),
    "12-youzhou.jpg": ("幽州",),
    "13-liangzhou.jpg": ("凉州",),
    "14-jingzhou-wei.jpg": ("荆州", "豫州"),
    "15-yongzhou.jpg": ("雍州",),
    "16-ji-bing.jpg": ("冀州", "并州"),
}

SKIP = {"01-overview.jpg", "02-xiyu.jpg", "08-xianbei.jpg"}


def pix_to_lonlat(g: dict, x: float, y: float) -> tuple[float, float]:
    return float(g["lon_a"] * x + g["lon_b"]), float(g["lat_c"] * y + g["lat_d"])


def lonlat_to_pix(g: dict, lon: float, lat: float, scale: float) -> tuple[float, float]:
    x = (lon - g["lon_b"]) / g["lon_a"] * scale
    y = (lat - g["lat_d"]) / g["lat_c"] * scale
    return x, y


def lonlat_to_hex(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
    row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
    return col, row


def purple_mask(im: np.ndarray) -> np.ndarray:
    r = im[:, :, 0].astype(np.float32)
    g = im[:, :, 1].astype(np.float32)
    b = im[:, :, 2].astype(np.float32)
    purple = (r > 90) & (b > 90) & (g < r * 0.85) & (g < b * 0.9) & ((r + b) > 1.6 * g + 40)
    violet = (r > 70) & (b > 100) & (b > g + 25) & (r > g + 10) & (r + g + b < 520)
    return purple | violet


def sheet_land_and_wall(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pur = purple_mask(arr)
    wall = ndimage.binary_dilation(pur, iterations=2)
    wall = ndimage.binary_closing(wall, iterations=3)
    lum = arr.mean(axis=2)
    sea = (
        ((arr[:, :, 2] > arr[:, :, 0] + 12) & (arr[:, :, 2] > arr[:, :, 1] + 8) & (lum < 210))
        | ((arr[:, :, 2] > 90) & (arr[:, :, 2] > arr[:, :, 0]) & (arr[:, :, 2] > arr[:, :, 1]) & (lum < 180))
    )
    paper = lum > 235
    ink_black = lum < 40
    land = (~wall) & (~sea) & (~paper) & (~ink_black)
    return land, wall


def underlay_land_mask(underlay: Path, coast: np.ndarray) -> np.ndarray:
    """Beige Sinica land only — never ocean / dark water."""
    img = np.array(Image.open(underlay).convert("RGB").resize((MAP_W, MAP_H), Image.Resampling.BILINEAR))
    r = img[:, :, 0].astype(np.float32)
    g = img[:, :, 1].astype(np.float32)
    b = img[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    # Sinica land plate is warm beige; sea is dark navy
    beige = (lum > 125) & (lum < 235) & (r > 145) & (g > 135) & (r + g > b * 1.55)
    sea = (lum < 110) | ((b > r + 15) & (b > g + 10) & (lum < 160))
    land = (coast > 0) & beige & (~sea)
    return land.astype(np.uint8)


def flood_from_seeds(
    land: np.ndarray,
    seeds: list[tuple[int, int, str]],
    max_dist: int,
) -> dict[str, np.ndarray]:
    """Flood each seed through land; stop at purple (removed from land) or max_dist."""
    h, w = land.shape
    owner = np.full((h, w), -1, dtype=np.int16)
    dist = np.full((h, w), 10**9, dtype=np.int32)
    names: list[str] = []
    name_to_i: dict[str, int] = {}
    q: deque[tuple[int, int, int, int]] = deque()

    def nid(name: str) -> int:
        if name not in name_to_i:
            name_to_i[name] = len(names)
            names.append(name)
        return name_to_i[name]

    for sx, sy, name in seeds:
        if not (0 <= sx < w and 0 <= sy < h):
            continue
        if not land[sy, sx]:
            found = False
            for rad in range(1, 35):
                for dy in range(-rad, rad + 1):
                    for dx in range(-rad, rad + 1):
                        nx, ny = sx + dx, sy + dy
                        if 0 <= nx < w and 0 <= ny < h and land[ny, nx]:
                            sx, sy = nx, ny
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                continue
        i = nid(name)
        if dist[sy, sx] <= 0:
            continue
        owner[sy, sx] = i
        dist[sy, sx] = 0
        q.append((sx, sy, i, 0))

    while q:
        x, y, i, d = q.popleft()
        if d >= max_dist:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            if not land[ny, nx]:
                continue
            nd = d + 1
            if nd >= dist[ny, nx] or nd > max_dist:
                continue
            owner[ny, nx] = i
            dist[ny, nx] = nd
            q.append((nx, ny, i, nd))

    return {name: owner == i for name, i in name_to_i.items()}


def sheet_zhou_masks(
    path: Path,
    g: dict,
    seeds_ll: list[tuple[str, float, float]],
    scale: float,
    max_dist: int,
) -> dict[str, np.ndarray]:
    im_full = Image.open(path).convert("RGB")
    im = im_full.resize(
        (max(1, int(im_full.width * scale)), max(1, int(im_full.height * scale))),
        Image.Resampling.BILINEAR,
    )
    arr = np.asarray(im)
    h, w, _ = arr.shape
    land, _wall = sheet_land_and_wall(arr)
    primary = SHEET_PRIMARY.get(path.name, ())

    seeds_xy: list[tuple[int, int, str]] = []
    for name, lon, lat in seeds_ll:
        if name not in CANON:
            continue
        if primary and name not in primary:
            continue
        x, y = lonlat_to_pix(g, lon, lat, scale)
        if 0 <= x < w and 0 <= y < h:
            seeds_xy.append((int(x), int(y), name))

    have = {n for _, _, n in seeds_xy}
    for pname in primary:
        if pname not in have:
            # plant at centroid of on-sheet seeds of any kind, else center
            seeds_xy.append((w // 2, h // 2, pname))

    return flood_from_seeds(land, seeds_xy, max_dist=max_dist)


def splat_to_hex(
    masks: dict[str, np.ndarray],
    g: dict,
    scale: float,
    votes: dict[str, np.ndarray],
    land_ok: np.ndarray,
    weight: int = 1,
) -> None:
    for name, mask in masks.items():
        ys, xs = np.where(mask)
        for y, x in zip(ys[::2], xs[::2]):
            lon, lat = pix_to_lonlat(g, x / scale, y / scale)
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue
            col, row = lonlat_to_hex(lon, lat)
            if 0 <= col < MAP_W and 0 <= row < MAP_H and land_ok[row, col] > 0:
                votes[name][row, col] += weight


def fill_small_interior_holes(hex_zhou: np.ndarray, land_ok: np.ndarray, max_hole: int = 40) -> np.ndarray:
    out = hex_zhou.copy()
    empty = (out == 0) & (land_ok > 0)
    labeled, n = ndimage.label(empty)
    for cid in range(1, n + 1):
        ys, xs = np.where(labeled == cid)
        if len(ys) == 0 or len(ys) > max_hole:
            continue
        neigh: set[int] = set()
        edge = False
        for y, x in zip(ys, xs):
            if y in (0, MAP_H - 1) or x in (0, MAP_W - 1):
                edge = True
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if not (0 <= nx < MAP_W and 0 <= ny < MAP_H):
                    edge = True
                    continue
                v = int(out[ny, nx])
                if v:
                    neigh.add(v)
        if (not edge) and len(neigh) == 1:
            out[labeled == cid] = next(iter(neigh))
    return out


def collect_seeds(points_path: Path, labels_path: Path) -> list[tuple[str, float, float]]:
    seeds: list[tuple[str, float, float]] = []
    pts = json.loads(points_path.read_text())
    for z in pts["zhou"]:
        if z["zhou"] in CANON:
            seeds.append((z["zhou"], z["lon"], z["lat"]))
    for j in pts["jun"]:
        z = j.get("zhou", "")
        if z in CANON:
            seeds.append((z, j["lon"], j["lat"]))
    if labels_path.is_file():
        for l in json.loads(labels_path.read_text()).get("labels", []):
            if l.get("kind") != "zhou" or l.get("conf", 0) < 0.7:
                continue
            raw = l["text"]
            name = ALIAS.get(raw, raw)
            for c in CANON:
                if raw.endswith(c) or c in raw:
                    name = c
                    break
            if name in CANON:
                seeds.append((name, l["lon"], l["lat"]))
    return seeds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--georef", type=Path, default=Path("/tmp/tan262/georef/all_georef.json"))
    ap.add_argument("--labels", type=Path, default=Path("/tmp/tan262/labels_all.json"))
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--underlay", type=Path, default=Path("/tmp/sinica_ad0262_z8.jpg"))
    ap.add_argument("--fallback-underlay", type=Path, default=Path("data/sinica_ad0262_underlay.jpg"))
    ap.add_argument("--out-hex", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.json"))
    ap.add_argument("--scale", type=float, default=0.45)
    # stops sheet-rectangle floods when purple is leaky; seats still bound extent
    ap.add_argument("--max-dist", type=int, default=360)
    args = ap.parse_args()

    georef = json.loads(args.georef.read_text())
    coast = np.frombuffer(args.coast.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)
    under = args.underlay if args.underlay.is_file() else args.fallback_underlay
    land_ok = underlay_land_mask(under, coast)
    print(f"land_ok={int(land_ok.sum())} (coast={int(coast.sum())})", flush=True)

    seeds = collect_seeds(args.points, args.labels)
    print(f"州/郡 seeds: {len(seeds)} max_dist={args.max_dist}", flush=True)

    votes: dict[str, np.ndarray] = defaultdict(lambda: np.zeros((MAP_H, MAP_W), dtype=np.uint16))

    for path in sorted(args.sheets.glob("*.jpg")):
        if path.name in SKIP:
            continue
        g = georef.get(path.name)
        if not g or not g.get("ok"):
            print(f"skip {path.name}: no georef", flush=True)
            continue
        print(f"purple-flood {path.name}", flush=True)
        masks = sheet_zhou_masks(path, g, seeds, scale=args.scale, max_dist=args.max_dist)
        print(
            f"  pockets: {{{', '.join(f'{k}:{int(v.sum())}' for k,v in sorted(masks.items()))}}}",
            flush=True,
        )
        splat_to_hex(masks, g, args.scale, votes, land_ok, weight=3)

    names = sorted(votes.keys())
    name_to_id = {n: i + 1 for i, n in enumerate(names)}
    id_to_name = {i: n for n, i in name_to_id.items()}
    hex_zhou = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    if names:
        stack = np.stack([votes[n] for n in names], axis=0)
        best = stack.argmax(axis=0)
        mx = stack.max(axis=0)
        hex_zhou = np.where(mx > 0, best + 1, 0).astype(np.uint8)

    hex_zhou[land_ok == 0] = 0
    # Fill only tiny pockets fully enclosed by one 州 (not 羌胡 / sea / empty west)
    hex_zhou = fill_small_interior_holes(hex_zhou, land_ok, max_hole=120)
    hex_zhou[land_ok == 0] = 0

    # Drop 州 hexes far from any seat of that 州 (belt-and-suspenders vs sheet rect)
    pts = json.loads(args.points.read_text())
    seats_by: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for z in pts["zhou"]:
        seats_by[z["zhou"]].append((z["lon"], z["lat"]))
    for j in pts["jun"]:
        seats_by[j["zhou"]].append((j["lon"], j["lat"]))
    # Per-州 seat radius (degrees²). Large western 州 get a bit more slack.
    RAD2 = {
        "益州": 3.2**2,
        "凉州": 3.5**2,
        "交州": 3.0**2,
        "幽州": 3.0**2,
        "荆州": 3.2**2,
        "扬州": 3.0**2,
    }
    DEFAULT_RAD2 = 2.8**2
    for name, idx in name_to_id.items():
        seats = seats_by.get(name, [])
        if not seats:
            continue
        rad2 = RAD2.get(name, DEFAULT_RAD2)
        ys, xs = np.where(hex_zhou == idx)
        for row, col in zip(ys, xs):
            lon = LON_MIN + (col + 0.5) / MAP_W * (LON_MAX - LON_MIN)
            lat = LAT_MAX - (row + 0.5) / MAP_H * (LAT_MAX - LAT_MIN)
            if min((lon - sl) ** 2 + (lat - st) ** 2 for sl, st in seats) > rad2:
                hex_zhou[row, col] = 0

    counts = Counter(id_to_name[int(i)] for i in hex_zhou.ravel() if i)
    bin_path = args.out_hex.with_suffix(".bin")
    bin_path.write_bytes(hex_zhou.astype(np.uint8).tobytes())
    # persist land_ok for bake
    Path("/tmp/tan262/land_ok.bin").write_bytes(land_ok.astype(np.uint8).tobytes())
    meta = {
        "zhou_names": [""] + names,
        "counts": dict(counts),
        "n_filled": int((hex_zhou > 0).sum()),
        "n_land_ok": int(land_ok.sum()),
        "bin": str(bin_path),
        "layout": "uint8 zhou_idx row-major 500x500",
        "authority": "Tan-262 purple 州 boundaries",
        "method": "per-sheet purple+distance-limited seat flood; land=coast∧¬underlay-sea; seat-radius trim",
    }
    args.out_hex.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"wrote {args.out_hex} filled={meta['n_filled']} counts={meta['counts']}")
    missing = [n for n in CANON if counts.get(n, 0) == 0]
    if missing:
        print("WARNING empty 州:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
