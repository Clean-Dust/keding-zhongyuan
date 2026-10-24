#!/usr/bin/env python3
"""Build 州/郡 hex fills by tracing purple outlines on Tan-262 分幅 sheets.

No seat-flood across the map. For each sheet (georeferenced by printed grid):
  1. Detect purple boundary ink and seal small gaps.
  2. Land pockets between purple lines → candidate regions.
  3. Label pockets from 州/郡 seats printed inside (sheet-primary 州 only).
  4. If several primary 州 share one pocket, split by nearest seat inside it.
  5. Union → outer contour → lon/lat polygon → rasterize onto hex (beige land).
  6. 郡: inner purple pockets inside the parent 州 polygon.

JPEGs stay in the agent store; only hex bins/json go to git.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

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


def purple_mask(arr: np.ndarray) -> np.ndarray:
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    purple = (r > 90) & (b > 90) & (g < r * 0.85) & (g < b * 0.9) & ((r + b) > 1.6 * g + 40)
    violet = (r > 70) & (b > 100) & (b > g + 25) & (r > g + 10) & (r + g + b < 520)
    return ((purple | violet).astype(np.uint8)) * 255


def sheet_sea(arr: np.ndarray) -> np.ndarray:
    """Blue sea ink on a Tan-262 分幅 sheet (bool). Used for land pockets."""
    lum = arr.mean(axis=2)
    sea = (arr[:, :, 2] > arr[:, :, 0] + 12) & (arr[:, :, 2] > arr[:, :, 1] + 8) & (lum < 210)
    sea |= (arr[:, :, 2] > 90) & (arr[:, :, 2] > arr[:, :, 0]) & (arr[:, :, 2] > arr[:, :, 1]) & (lum < 180)
    return sea


def sheet_sea_coast(arr: np.ndarray) -> np.ndarray:
    """Broader sea mask for coastline clipping (includes shelf / cyan water)."""
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    sea = sheet_sea(arr)
    sea |= (b > 70) & (b > r) & (b > g) & (lum < 200)
    sea |= (b > r + 10) & (g > r + 5) & (lum < 190) & (b > 80)
    return sea


def sheet_land(arr: np.ndarray, wall: np.ndarray) -> np.ndarray:
    lum = arr.mean(axis=2)
    sea = sheet_sea(arr)
    paper = lum > 235
    black = lum < 40
    return (~wall.astype(bool)) & (~sea) & (~paper) & (~black)


# Coastal 州 whose primary sheet paints open sea — clip hex fills to sheet coastline.
COAST_CLIP_ZHOU = {
    "交州": "03-jiaozhou.jpg",
    "幽州": "12-youzhou.jpg",
}


def rasterize_sheet_sea_hex(path: Path, g: dict, scale: float) -> np.ndarray:
    """500² mask of hexes that fall on sheet sea ink."""
    im = np.array(Image.open(path).convert("RGB"))
    small = cv2.resize(
        im, (max(1, int(im.shape[1] * scale)), max(1, int(im.shape[0] * scale))), interpolation=cv2.INTER_AREA
    )
    sea = sheet_sea_coast(small).astype(np.uint8)
    # Dilate so shoreline-straddling / upsampled hexes stay sea-clipped
    sea = cv2.dilate(sea, np.ones((5, 5), np.uint8), iterations=2)
    out = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    ys, xs = np.where(sea > 0)
    for y, x in zip(ys, xs):
        lon = g["lon_a"] * (x / scale) + g["lon_b"]
        lat = g["lat_c"] * (y / scale) + g["lat_d"]
        col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W)
        row = int((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H)
        if 0 <= col < MAP_W and 0 <= row < MAP_H:
            out[row, col] = 1
    # Also mark hexes whose 3×3 neighborhood is mostly sheet-sea (coast fringe)
    if out.any():
        near = cv2.dilate(out, np.ones((3, 3), np.uint8), iterations=1)
        # count sea neighbors
        sea_n = cv2.filter2D(out.astype(np.float32), -1, np.ones((3, 3), np.float32))
        out = ((out > 0) | (sea_n >= 5)).astype(np.uint8)
        _ = near  # kept for clarity / future land-gate
    return out


def clip_zhou_to_sheet_coast(
    hex_zhou: np.ndarray,
    zhou_index: dict[str, int],
    sheets: Path,
    georef: dict,
    scale: float = 0.5,
) -> dict[str, int]:
    """Clear 交州/幽州 hexes that land on their primary sheet's sea. Other 州 untouched."""
    removed: dict[str, int] = {}
    for zname, sheet_name in COAST_CLIP_ZHOU.items():
        zid = zhou_index.get(zname)
        if not zid:
            continue
        g = georef.get(sheet_name)
        path = sheets / sheet_name
        if not g or not g.get("ok") or not path.is_file():
            print(f"coast-clip skip {zname}: missing sheet/georef", flush=True)
            continue
        sea_hex = rasterize_sheet_sea_hex(path, g, scale)
        sel = (hex_zhou == zid) & (sea_hex > 0)
        n = int(sel.sum())
        hex_zhou[sel] = 0
        # Drop only tiny crumbs after sea cut; keep peninsulas / island fragments
        m = (hex_zhou == zid).astype(np.uint8)
        if m.any():
            ncc, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
            for ci in range(1, ncc):
                if int(stats[ci, cv2.CC_STAT_AREA]) < 8:
                    hex_zhou[lab == ci] = 0
        removed[zname] = n
        print(f"coast-clip {zname}: removed {n} sea hexes → {int((hex_zhou == zid).sum())}", flush=True)
    return removed


def underlay_beige_land(underlay: Path, coast: np.ndarray) -> np.ndarray:
    img = np.array(Image.open(underlay).convert("RGB").resize((MAP_W, MAP_H), Image.Resampling.BILINEAR))
    r = img[:, :, 0].astype(np.float32)
    g = img[:, :, 1].astype(np.float32)
    b = img[:, :, 2].astype(np.float32)
    lum = (r + g + b) / 3.0
    beige = (lum > 125) & (lum < 235) & (r > 145) & (g > 135) & (r + g > b * 1.55)
    sea = (lum < 110) | ((b > r + 15) & (b > g + 10) & (lum < 160))
    return ((coast > 0) & beige & (~sea)).astype(np.uint8)


def seal_purple(pur: np.ndarray) -> np.ndarray:
    wall = cv2.morphologyEx(pur, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=3)
    wall = cv2.dilate(wall, np.ones((3, 3), np.uint8), iterations=1)
    return wall


def contour_to_lonlat(contour: np.ndarray, g: dict, scale: float, simplify_eps: float = 2.0) -> list[tuple[float, float]]:
    if contour is None or len(contour) < 3:
        return []
    approx = cv2.approxPolyDP(contour, simplify_eps, True)
    ring: list[tuple[float, float]] = []
    for pt in approx.reshape(-1, 2):
        x = float(pt[0]) / scale
        y = float(pt[1]) / scale
        lon = g["lon_a"] * x + g["lon_b"]
        lat = g["lat_c"] * y + g["lat_d"]
        ring.append((lon, lat))
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def rasterize_polygon_votes(
    ring: list[tuple[float, float]], votes: np.ndarray, zid: int, land_ok: np.ndarray, weight: int = 1
) -> int:
    """Add polygon coverage into votes[zid] (uint16 stack)."""
    if len(ring) < 4 or zid <= 0:
        return 0
    pts = [
        [
            (lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W,
            (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H,
        ]
        for lon, lat in ring
    ]
    mask = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(pts, dtype=np.int32)], 1)
    sel = (mask > 0) & (land_ok > 0)
    votes[zid][sel] = np.minimum(votes[zid][sel] + weight, 65535)
    return int(sel.sum())


def largest_connected(mask: np.ndarray) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
    if n <= 1:
        return mask
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return lab == best


def yizhou_west_purple_cuts(wall: np.ndarray) -> np.ndarray:
    """Per-row x cut for 益州 west edge: follow inland purple, not sheet neatline.

    Traces the continuous western purple wall (sheet-06/07), skipping left-margin
    neatline ink. Returns int32 cut[y] = first interior column to keep.
    """
    h, w = wall.shape
    margin = max(2, int(0.035 * w))
    right = int(0.62 * w)
    # Anchor on a strong purple column in the western half (outer 州 wall, not neatline).
    dens = wall[:, margin:right].sum(0).astype(np.float64)
    if dens.max() <= 0:
        return np.zeros(h, dtype=np.int32)
    thr = 0.40 * float(dens.max())
    strong = [i for i in range(len(dens)) if dens[i] >= thr]
    if not strong:
        strong = [int(np.argmax(dens))]
    # Westernmost strong peak — outer wall; tracing walks along it north/south.
    anchor_rel = min(strong)
    prev = margin + anchor_rel
    cut = np.zeros(h, dtype=np.int32)
    for y in range(h):
        xs = np.where(wall[y] > 0)[0]
        xs = xs[(xs > margin) & (xs < right)]
        if len(xs) == 0:
            cut[y] = prev
            continue
        # Cluster into runs (gap ≤ 3 px)
        runs: list[tuple[int, int]] = []
        start = prevx = int(xs[0])
        for x in xs[1:]:
            x = int(x)
            if x - prevx <= 3:
                prevx = x
            else:
                runs.append((start, prevx))
                start = prevx = x
        runs.append((start, prevx))
        # Run whose center is closest to the tracked wall
        run = min(runs, key=lambda r: abs(0.5 * (r[0] + r[1]) - prev))
        x = run[0]
        # Prefer a slightly western neighbor run if it is part of the same wall bundle
        for r in runs:
            if r[1] < x and x - r[1] <= 45 and (r[1] - r[0]) >= 1:
                if abs(0.5 * (r[0] + r[1]) - prev) < 75:
                    x = r[0]
        cut[y] = int(x)
        prev = int(0.85 * prev + 0.15 * x)
    # Median smooth to kill single-row spikes (keeps meander, drops neatline flickers)
    k = 31
    pad = np.pad(cut.astype(np.float64), (k // 2, k // 2), mode="edge")
    smooth = np.array([np.median(pad[i : i + k]) for i in range(h)])
    for y in range(h):
        xs = np.where(wall[y] > 0)[0]
        xs = xs[(xs > margin) & (xs < right)]
        if len(xs) == 0:
            cut[y] = int(smooth[y])
            continue
        lo, hi = smooth[y] - 40, smooth[y] + 70
        near = xs[(xs >= lo) & (xs <= hi)]
        cut[y] = int(near.min()) if len(near) else int(min(xs, key=lambda x: abs(x - smooth[y])))
    return cut


def clip_mask_west_of_purple(mask: np.ndarray, wall: np.ndarray) -> tuple[np.ndarray, int]:
    """Zero mask pixels west of the traced inland purple wall. Returns (mask, n_removed)."""
    if mask is None or not mask.any():
        return mask, 0
    cut = yizhou_west_purple_cuts(wall)
    out = mask.copy()
    removed = 0
    h, w = out.shape
    for y in range(h):
        xcut = int(cut[y])
        if xcut <= 0:
            continue
        before = int(out[y, :xcut].sum())
        if before:
            out[y, :xcut] = 0
            removed += before
    out = largest_connected(out).astype(np.uint8)
    return out, removed


def _fill_holes_binary(mask: np.ndarray) -> np.ndarray:
    """Fill interior holes of a binary mask (uint8 0/1).

    Pads a 1-px empty border so flood-fill always starts in true exterior — even
    when the mask touches the image corner/edge (otherwise every empty cell is
    misclassified as a hole and the 州 swallows the map).
    """
    m = (mask > 0).astype(np.uint8)
    if not m.any():
        return m
    padded = np.pad(m, 1, mode="constant", constant_values=0)
    inv = (padded == 0).astype(np.uint8) * 255
    ff = inv.copy()
    cv2.floodFill(ff, np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8), (0, 0), 128)
    holes = ff[1:-1, 1:-1] == 255
    return (m | holes).astype(np.uint8)


def keep_largest_per_id(
    grid: np.ndarray, land_ok: np.ndarray | None = None, close_px: int = 3
) -> np.ndarray:
    """One solid connected region per id (land-only close + hole fill + largest CC)."""
    out = np.zeros_like(grid)
    k = None
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px * 2 + 1, close_px * 2 + 1))
    for vid0 in np.unique(grid):
        vid = int(vid0)
        if vid == 0:
            continue
        m = (grid == vid).astype(np.uint8)
        if land_ok is not None:
            m = ((m > 0) & (land_ok > 0)).astype(np.uint8)
        if not m.any():
            continue
        if k is not None:
            # morph close constrained to land: dilate∩land, then erode; keeps solid fills
            dil = cv2.dilate(m, k, iterations=1)
            if land_ok is not None:
                dil = dil & (land_ok > 0).astype(np.uint8)
            m = cv2.erode(dil, k, iterations=1)
            # restore original core so thin peninsulas aren't erased, then drop islands later
            core = (grid == vid).astype(np.uint8)
            if land_ok is not None:
                core = core & (land_ok > 0).astype(np.uint8)
            m = np.maximum(m, core)
        m = _fill_holes_binary(m)
        if land_ok is not None:
            m = m & (land_ok > 0).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
        if n <= 1:
            continue
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        out[lab == best] = vid
    return out


def _nudge_to_land(lab: np.ndarray, x: float, y: float, max_r: int = 50) -> tuple[int, int, int] | None:
    h, w = lab.shape
    if not (0 <= x < w and 0 <= y < h):
        return None
    ix, iy = int(x), int(y)
    if lab[iy, ix] > 0:
        return int(lab[iy, ix]), ix, iy
    for rad in range(1, max_r + 1):
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                nx, ny = ix + dx, iy + dy
                if 0 <= nx < w and 0 <= ny < h and lab[ny, nx] > 0:
                    return int(lab[ny, nx]), nx, ny
    return None


def sheet_zhou_polygons(
    path: Path,
    g: dict,
    zhou_seats: list[tuple[str, float, float]],
    jun_seats: list[tuple[str, str, float, float]],
    xian_seats: list[tuple[str, float, float]],
    scale: float,
) -> dict[str, list[tuple[float, float]]]:
    """Primary-州 lon/lat rings from purple pockets on this sheet."""
    im = np.array(Image.open(path).convert("RGB"))
    small = cv2.resize(
        im, (max(1, int(im.shape[1] * scale)), max(1, int(im.shape[0] * scale))), interpolation=cv2.INTER_AREA
    )
    pur = purple_mask(small)
    wall = seal_purple(pur)
    land = sheet_land(small, wall)
    h, w = land.shape
    _n, lab, stats, _ = cv2.connectedComponentsWithStats(land.astype(np.uint8), connectivity=4)

    primary = SHEET_PRIMARY.get(path.name, ())
    if not primary:
        return {}

    def ll_pix(lon: float, lat: float) -> tuple[float, float]:
        return (lon - g["lon_b"]) / g["lon_a"] * scale, (lat - g["lat_d"]) / g["lat_c"] * scale

    comp_seats: dict[int, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for name, lon, lat in zhou_seats:
        if name not in primary:
            continue
        hit = _nudge_to_land(lab, *ll_pix(lon, lat), max_r=60)
        if hit:
            cid, ix, iy = hit
            comp_seats[cid][name].append((ix, iy))
    for _jn, zname, lon, lat in jun_seats:
        if zname not in primary:
            continue
        hit = _nudge_to_land(lab, *ll_pix(lon, lat), max_r=60)
        if hit:
            cid, ix, iy = hit
            comp_seats[cid][zname].append((ix, iy))
    # 县 seats help sheets where 郡治 sit on purple ink (e.g. 益州南)
    for zname, lon, lat in xian_seats:
        if zname not in primary:
            continue
        hit = _nudge_to_land(lab, *ll_pix(lon, lat), max_r=40)
        if hit:
            cid, ix, iy = hit
            if stats[cid, cv2.CC_STAT_AREA] >= 150:
                comp_seats[cid][zname].append((ix, iy))

    masks: dict[str, np.ndarray] = {p: np.zeros((h, w), dtype=np.uint8) for p in primary}
    for cid, by_z in comp_seats.items():
        if stats[cid, cv2.CC_STAT_AREA] < 150:
            continue
        names = [zn for zn in primary if by_z.get(zn)]
        if not names:
            continue
        ys, xs = np.where(lab == cid)
        if len(names) == 1:
            masks[names[0]][ys, xs] = 1
            continue
        seeds = {zn: by_z[zn] for zn in names}
        for y, x in zip(ys, xs):
            best_zn, best_d = names[0], 1e18
            for zn, pts_ in seeds.items():
                for sx, sy in pts_:
                    d = (x - sx) ** 2 + (y - sy) ** 2
                    if d < best_d:
                        best_d, best_zn = d, zn
            masks[best_zn][y, x] = 1

    rings: dict[str, list[tuple[float, float]]] = {}
    for zname, union in masks.items():
        if int(union.sum()) < 300:
            continue
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
        union = cv2.morphologyEx(union, cv2.MORPH_CLOSE, k, iterations=2)
        union = largest_connected(union).astype(np.uint8)
        union = cv2.morphologyEx(union, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=1)
        # 益州 on sheets 06/07: west edge must follow inland purple, not neatline
        if zname == "益州" and path.name in ("06-yizhou-north.jpg", "07-yizhou-south.jpg"):
            union, ncut = clip_mask_west_of_purple(union, wall)
            if ncut:
                print(f"    {path.name} 益州 west-purple clip -{ncut} px", flush=True)
        cnts, _ = cv2.findContours(union, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        cnt = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(cnt) < 400:
            continue
        ring = contour_to_lonlat(cnt, g, scale, simplify_eps=2.5)
        if len(ring) >= 4:
            rings[zname] = ring
    return rings


def sheet_jun_polygons(
    path: Path,
    g: dict,
    zhou_ring: dict[str, list[tuple[float, float]]],
    jun_seats: list[tuple[str, str, float, float]],
    scale: float,
) -> dict[str, list[tuple[float, float]]]:
    im = np.array(Image.open(path).convert("RGB"))
    small = cv2.resize(
        im, (max(1, int(im.shape[1] * scale)), max(1, int(im.shape[0] * scale))), interpolation=cv2.INTER_AREA
    )
    pur = purple_mask(small)
    wall = cv2.morphologyEx(pur, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)
    wall = cv2.dilate(wall, np.ones((2, 2), np.uint8), iterations=1)
    land = sheet_land(small, wall)
    h, w = land.shape

    zhou_mask = {zn: np.zeros((h, w), dtype=np.uint8) for zn in zhou_ring}
    for zn, ring in zhou_ring.items():
        pts = []
        for lon, lat in ring:
            x = (lon - g["lon_b"]) / g["lon_a"] * scale
            y = (lat - g["lat_d"]) / g["lat_c"] * scale
            pts.append([x, y])
        if len(pts) >= 3:
            cv2.fillPoly(zhou_mask[zn], [np.array(pts, dtype=np.int32)], 1)

    _n, lab, stats, _ = cv2.connectedComponentsWithStats(land.astype(np.uint8), connectivity=4)
    primary = set(SHEET_PRIMARY.get(path.name, ()))

    def ll_pix(lon: float, lat: float) -> tuple[float, float]:
        return (lon - g["lon_b"]) / g["lon_a"] * scale, (lat - g["lat_d"]) / g["lat_c"] * scale

    assigned: dict[int, tuple[str, str]] = {}
    for jname, zname, lon, lat in jun_seats:
        if primary and zname not in primary:
            continue
        hit = _nudge_to_land(lab, *ll_pix(lon, lat), max_r=40)
        if not hit:
            continue
        cid, ix, iy = hit
        if stats[cid, cv2.CC_STAT_AREA] < 80:
            continue
        if zname in zhou_mask and zhou_mask[zname].sum() > 0 and zhou_mask[zname][iy, ix] == 0:
            continue
        assigned[cid] = (jname, zname)

    rings: dict[str, list[tuple[float, float]]] = {}
    for cid, (jname, zname) in assigned.items():
        m = (lab == cid).astype(np.uint8)
        if zname in zhou_mask and zhou_mask[zname].sum() > 0:
            m = m & zhou_mask[zname]
        if m.sum() < 80:
            continue
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        ring = contour_to_lonlat(max(cnts, key=cv2.contourArea), g, scale, simplify_eps=2.0)
        if len(ring) >= 4:
            rings[jname] = ring
    return rings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--georef", type=Path, default=Path("/tmp/tan262/georef/all_georef.json"))
    ap.add_argument("--points", type=Path, default=Path("data/admin_points.json"))
    ap.add_argument("--coast", type=Path, default=Path("data/coast_mask.bin"))
    ap.add_argument("--underlay", type=Path, default=Path("/tmp/sinica_ad0262_z8.jpg"))
    ap.add_argument("--fallback-underlay", type=Path, default=Path("data/sinica_ad0262_underlay.jpg"))
    ap.add_argument("--out-hex", type=Path, default=Path("/tmp/tan262/zhou_hex_votes.json"))
    ap.add_argument("--out-jun-hex", type=Path, default=Path("/tmp/tan262/jun_hex_votes.bin"))
    ap.add_argument("--out-polygons", type=Path, default=Path("/tmp/tan262/zhou_polygons.json"))
    ap.add_argument("--scale", type=float, default=0.5)
    args = ap.parse_args()

    georef = json.loads(args.georef.read_text())
    pts = json.loads(args.points.read_text())
    coast = np.frombuffer(args.coast.read_bytes(), dtype=np.uint8).reshape(MAP_H, MAP_W)
    under = args.underlay if args.underlay.is_file() else args.fallback_underlay
    land_ok = underlay_beige_land(under, coast)
    Path("/tmp/tan262/land_ok.bin").write_bytes(land_ok.astype(np.uint8).tobytes())
    print(f"land_ok={int(land_ok.sum())}", flush=True)

    zhou_seats = [(z["zhou"], z["lon"], z["lat"]) for z in pts["zhou"] if z["zhou"] in CANON]
    jun_to_zhou = {j["jun"]: j["zhou"] for j in pts["jun"]}
    jun_seats = [(j["jun"], j["zhou"], j["lon"], j["lat"]) for j in pts["jun"] if j["zhou"] in CANON]
    xian_seats = [
        (x["zhou"], x["lon"], x["lat"])
        for x in pts["xian"]
        if x.get("zhou") in CANON and "lon" in x and "lat" in x
    ]
    # some xian records may only have jun — map via jun_to_zhou
    if not xian_seats:
        xian_seats = [
            (jun_to_zhou[x["jun"]], x["lon"], x["lat"])
            for x in pts["xian"]
            if x.get("jun") in jun_to_zhou and jun_to_zhou[x["jun"]] in CANON
        ]
    else:
        # also add jun-mapped ones missing zhou field
        for x in pts["xian"]:
            if x.get("zhou") in CANON:
                continue
            if x.get("jun") in jun_to_zhou and jun_to_zhou[x["jun"]] in CANON:
                xian_seats.append((jun_to_zhou[x["jun"]], x["lon"], x["lat"]))

    zhou_names = [""] + [z["zhou"] for z in pts["zhou"] if z["zhou"] in CANON]
    zhou_index = {n: i for i, n in enumerate(zhou_names) if n}
    jun_names = [""]
    seen: set[str] = set()
    for j in pts["jun"]:
        if j["jun"] not in seen:
            seen.add(j["jun"])
            jun_names.append(j["jun"])
    jun_index = {n: i for i, n in enumerate(jun_names) if n}

    n_zhou = len(zhou_names)
    zhou_votes = np.zeros((n_zhou, MAP_H, MAP_W), dtype=np.uint16)
    jun_votes = np.zeros((len(jun_names), MAP_H, MAP_W), dtype=np.uint16)
    poly_dump: dict[str, list] = {"zhou": {}, "jun": {}}

    for path in sorted(args.sheets.glob("*.jpg")):
        if path.name in SKIP:
            continue
        g = georef.get(path.name)
        if not g or not g.get("ok"):
            print(f"skip {path.name}: no georef", flush=True)
            continue
        print(f"polygon {path.name}", flush=True)
        zrings = sheet_zhou_polygons(path, g, zhou_seats, jun_seats, xian_seats, args.scale)
        print(f"  州 polygons: {list(zrings.keys())}", flush=True)
        for zn, ring in zrings.items():
            zid = zhou_index.get(zn)
            if not zid:
                continue
            # primary sheet of a 州 weighs more
            w = 3 if zn in SHEET_PRIMARY.get(path.name, ()) else 1
            nset = rasterize_polygon_votes(ring, zhou_votes, zid, land_ok, weight=w)
            poly_dump["zhou"].setdefault(zn, []).append({"sheet": path.name, "n_ring": len(ring), "hex": nset})
            print(f"    {zn}: ring={len(ring)} hex+={nset}", flush=True)

        jrings = sheet_jun_polygons(path, g, zrings, jun_seats, args.scale)
        for jn, ring in jrings.items():
            jid = jun_index.get(jn)
            if not jid:
                continue
            pts_xy = [
                [
                    (lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W,
                    (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H,
                ]
                for lon, lat in ring
            ]
            if len(pts_xy) < 3:
                continue
            mask = np.zeros((MAP_H, MAP_W), dtype=np.uint8)
            cv2.fillPoly(mask, [np.array(pts_xy, dtype=np.int32)], 1)
            sel = (mask > 0) & (land_ok > 0)
            jun_votes[jid][sel] = np.minimum(jun_votes[jid][sel] + 1, 65535)
            poly_dump["jun"].setdefault(jn, []).append({"sheet": path.name, "hex": int(sel.sum())})

    # Winner-take-all 州
    mx = zhou_votes.max(axis=0)
    hex_zhou = np.where(mx > 0, zhou_votes.argmax(axis=0), 0).astype(np.uint8)
    hex_zhou[land_ok == 0] = 0
    # Bridge sheet seams on land only, then force exactly one CC per 州
    hex_zhou = keep_largest_per_id(hex_zhou, land_ok=land_ok, close_px=8)
    hex_zhou[land_ok == 0] = 0
    hex_zhou = keep_largest_per_id(hex_zhou, land_ok=land_ok, close_px=0)
    # Solid-fill only true interior holes (beige land; never steal foreign 州 / no exterior expand).
    for zid in range(1, n_zhou):
        m = (hex_zhou == zid).astype(np.uint8)
        if not m.any():
            continue
        foreign = (hex_zhou != 0) & (hex_zhou != zid)
        holes = _fill_holes_binary(m)
        # Cap hole fill: only accept hole CCs much smaller than the 州 itself
        n_h, lab_h, st_h, _ = cv2.connectedComponentsWithStats(
            ((holes > 0) & (m == 0)).astype(np.uint8), connectivity=4
        )
        core = int(m.sum())
        for hi in range(1, n_h):
            area = int(st_h[hi, cv2.CC_STAT_AREA])
            if area > max(80, core // 5):
                continue
            sel = lab_h == hi
            sel = sel & (land_ok > 0) & (hex_zhou == 0) & (~foreign)
            hex_zhou[sel] = zid

    # 交州/幽州: drop hexes that fall on sheet sea ink (coastline clip)
    clip_zhou_to_sheet_coast(hex_zhou, zhou_index, args.sheets, georef, args.scale)

    mxj = jun_votes.max(axis=0)
    hex_jun = np.where(mxj > 0, jun_votes.argmax(axis=0), 0).astype(np.uint16)
    for jn, jid in jun_index.items():
        zid = zhou_index.get(jun_to_zhou.get(jn, ""), 0)
        if zid:
            hex_jun[(hex_jun == jid) & (hex_zhou != zid)] = 0
    hex_jun = keep_largest_per_id(hex_jun, land_ok=land_ok, close_px=3)
    hex_jun = keep_largest_per_id(hex_jun, land_ok=land_ok, close_px=0)

    zcounts = Counter(zhou_names[int(i)] for i in hex_zhou.ravel() if i)
    jcounts = Counter(jun_names[int(i)] for i in hex_jun.ravel() if i)
    bin_path = args.out_hex.with_suffix(".bin")
    bin_path.write_bytes(hex_zhou.astype(np.uint8).tobytes())
    args.out_jun_hex.write_bytes(hex_jun.astype("<u2").tobytes())
    args.out_polygons.write_text(json.dumps(poly_dump, ensure_ascii=False, indent=2))
    meta = {
        "zhou_names": zhou_names,
        "jun_names": jun_names,
        "counts": dict(zcounts),
        "jun_counts_top": dict(jcounts.most_common(20)),
        "n_filled": int((hex_zhou > 0).sum()),
        "n_jun_filled": int((hex_jun > 0).sum()),
        "n_land_ok": int(land_ok.sum()),
        "bin": str(bin_path),
        "jun_bin": str(args.out_jun_hex),
        "layout": "uint8 zhou_idx / uint16 jun_idx row-major 500x500",
        "authority": "Tan-262 purple 州/郡 outline polygons",
        "method": "purple pocket union → outer contour lon/lat polygon → hex; no seat-flood",
    }
    args.out_hex.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"wrote {args.out_hex} filled={meta['n_filled']} jun={meta['n_jun_filled']}")
    print("zhou", dict(zcounts))
    missing = [n for n in CANON if zcounts.get(n, 0) == 0]
    if missing:
        print("WARNING empty 州:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
