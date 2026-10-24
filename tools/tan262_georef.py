#!/usr/bin/env python3
"""Georeference Tan Qixiang 三国 atlas 分幅 sheets from printed degree grids.

Sheets live in the agent store (not committed). Writes transforms to --out-dir.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR


def ocr_region(engine: RapidOCR, im: Image.Image, box: tuple[int, int, int, int], scale: int = 3):
    x0, y0, x1, y1 = box
    crop = im.crop((x0, y0, x1, y1))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    arr = np.asarray(ImageOps.autocontrast(crop.convert("L")))
    result = engine(arr)
    out: list[tuple[str, float, float, float]] = []
    if result is None or not result.txts:
        return out
    for t, s, b in zip(result.txts, result.scores, result.boxes):
        if s is None or s < 0.45:
            continue
        bb = np.array(b)
        cx = x0 + float(bb[:, 0].mean()) / scale
        cy = y0 + float(bb[:, 1].mean()) / scale
        out.append((str(t).strip(), float(s), cx, cy))
    return out


def parse_deg(t: str) -> int | None:
    if any(k in t for k in ("公", "里", "万", "年", "图", "分", "赤", "乌", "附")):
        return None
    m = re.search(r"(\d{2,3})", t)
    if not m:
        return None
    v = int(m.group(1))
    if 8 <= v <= 140:
        return v
    return None


def robust_line(xs: np.ndarray, ys: np.ndarray, max_resid: float = 0.75, rounds: int = 4):
    """Fit y = a*x + b with iterative residual clipping."""
    mask = np.ones(len(xs), dtype=bool)
    a = b = 0.0
    for _ in range(rounds):
        if mask.sum() < 2:
            break
        a, b = np.polyfit(xs[mask], ys[mask], 1)
        resid = np.abs(a * xs + b - ys)
        # keep points within max_resid OR the best half
        keep = resid <= max_resid
        if keep.sum() < 2:
            # loosen: keep lowest-residual half
            order = np.argsort(resid)
            keep = np.zeros_like(mask)
            keep[order[: max(2, len(order) // 2)]] = True
        if keep.sum() == mask.sum():
            mask = keep
            break
        mask = keep
    a, b = np.polyfit(xs[mask], ys[mask], 1)
    rmse = float(np.sqrt(np.mean((a * xs[mask] + b - ys[mask]) ** 2)))
    return float(a), float(b), rmse, mask


def georef_sheet(engine: RapidOCR, path: Path) -> dict:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    margin = max(70, int(min(w, h) * 0.045))
    # Keep side strips away from corners so lon labels don't pollute lat GCPs
    corner = margin + 30
    top = ocr_region(engine, im, (corner, 0, w - corner, margin))
    bottom = ocr_region(engine, im, (corner, h - margin - 20, w - corner, h))
    left = ocr_region(engine, im, (0, corner, margin + 20, h - corner))
    right = ocr_region(engine, im, (w - margin - 20, corner, w, h - corner))

    lon_pts: list[tuple[float, float, float, str]] = []
    for t, s, cx, _cy in top + bottom:
        v = parse_deg(t)
        if v is not None and 90 <= v <= 135:
            lon_pts.append((cx, float(v), s, t))
    lat_pts: list[tuple[float, float, float, str]] = []
    for t, s, _cx, cy in left + right:
        v = parse_deg(t)
        if v is not None and 10 <= v <= 55:
            lat_pts.append((cy, float(v), s, t))

    def dedup_axis(pts: list[tuple[float, float, float, str]], bin_px: float = 12.0):
        # Prefer unique degree values; if same degree, average x/y weighted by conf
        by_val: dict[float, list[tuple[float, float, float, str]]] = {}
        for p in pts:
            by_val.setdefault(p[1], []).append(p)
        out = []
        for val, group in by_val.items():
            ws = sum(g[2] for g in group)
            ax = sum(g[0] * g[2] for g in group) / ws
            out.append((ax, val, max(g[2] for g in group), group[0][3]))
        return out

    lon_pts = dedup_axis(lon_pts)
    lat_pts = dedup_axis(lat_pts)

    info: dict = {
        "file": path.name,
        "w": w,
        "h": h,
        "n_lon": len(lon_pts),
        "n_lat": len(lat_pts),
        "lon_pts": [{"x": x, "lon": lon, "conf": s, "text": t} for x, lon, s, t in sorted(lon_pts)],
        "lat_pts": [{"y": y, "lat": lat, "conf": s, "text": t} for y, lat, s, t in sorted(lat_pts)],
    }
    if len(lon_pts) < 2 or len(lat_pts) < 2:
        info["ok"] = False
        info["error"] = "insufficient GCPs"
        return info

    xs = np.array([p[0] for p in lon_pts], dtype=float)
    lons = np.array([p[1] for p in lon_pts], dtype=float)
    lon_a, lon_b, lon_rmse, lon_mask = robust_line(xs, lons, max_resid=0.6)
    # Lon must increase with x
    if lon_a <= 0:
        info["ok"] = False
        info["error"] = "lon slope non-positive"
        return info

    ys = np.array([p[0] for p in lat_pts], dtype=float)
    lats = np.array([p[1] for p in lat_pts], dtype=float)
    lat_c, lat_d, lat_rmse, lat_mask = robust_line(ys, lats, max_resid=0.6)
    # Lat must decrease with y (north up)
    if lat_c >= 0:
        # try flipping by dropping outliers more aggressively
        lat_c, lat_d, lat_rmse, lat_mask = robust_line(ys, lats, max_resid=0.35)
    if lat_c >= 0:
        info["ok"] = False
        info["error"] = "lat slope non-negative (north-up expected)"
        info["lat_c_raw"] = lat_c
        return info

    corners_lon = [float(lon_a * 0 + lon_b), float(lon_a * (w - 1) + lon_b)]
    corners_lat = [float(lat_c * 0 + lat_d), float(lat_c * (h - 1) + lat_d)]
    info.update(
        {
            "ok": True,
            "lon_a": lon_a,
            "lon_b": lon_b,
            "lat_c": lat_c,
            "lat_d": lat_d,
            "lon_rmse": lon_rmse,
            "lat_rmse": lat_rmse,
            "n_lon_used": int(lon_mask.sum()),
            "n_lat_used": int(lat_mask.sum()),
            "bbox": {
                "lon_min": min(corners_lon),
                "lon_max": max(corners_lon),
                "lat_min": min(corners_lat),
                "lat_max": max(corners_lat),
            },
        }
    )
    return info


def pix_to_lonlat(info: dict, x: float, y: float) -> tuple[float, float]:
    return float(info["lon_a"] * x + info["lon_b"]), float(info["lat_c"] * y + info["lat_d"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheets",
        type=Path,
        default=Path("/cursor/stores/bc-98f6facd-822a-4e6f-badd-6fc0a5aa5ecf/media/tan-262"),
    )
    ap.add_argument("--out-dir", type=Path, default=Path("/tmp/tan262/georef"))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR()
    all_info: dict = {}
    for path in sorted(args.sheets.glob("*.jpg")):
        print(f"georef {path.name}", flush=True)
        info = georef_sheet(engine, path)
        all_info[path.name] = info
        status = "OK" if info.get("ok") else "FAIL"
        print(
            f"  {status} lon_n={info['n_lon']} lat_n={info['n_lat']} "
            f"used={info.get('n_lon_used')}/{info.get('n_lat_used')} "
            f"rmse={info.get('lon_rmse')},{info.get('lat_rmse')} bbox={info.get('bbox')} "
            f"{info.get('error','')}",
            flush=True,
        )
    out = args.out_dir / "all_georef.json"
    out.write_text(json.dumps(all_info, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
