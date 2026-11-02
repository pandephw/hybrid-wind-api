#!/usr/bin/env python3
"""Read-only, low-volume audit of BMKG documented marine wind endpoints.

No publication or changes to the user's existing Hybrid API.
Calls only four public marine JSON endpoints and the documented modelrun endpoint.
Python 3.12+, standard library only.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://maritim.bmkg.go.id"
AREAS = ["P.P.02", "P.P.03", "P.P.04", "P.P.06"]
OUT = Path("bmkg_marine_audit")
OUT.mkdir(exist_ok=True)
UA = "WindKite-BMKG-Marine-Feasibility-Test/0.1 (educational, 5 low-rate GET requests)"


def load_url(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Response larger than 10 MiB: skipped")
        return json.loads(raw.decode("utf-8-sig"))


def utc(t):
    if not t:
        return None
    s = str(t).replace(" UTC", "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None


def ts(t):
    return t.isoformat().replace("+00:00", "Z") if t else None


def probe_area(code, now):
    url = f"{BASE}/marine2026-data/perairan/{code}.json"
    result = {"code": code, "url": url}
    try:
        obj = load_url(url)
        fcs = (obj.get("forecast_day1") or []) + (obj.get("forecast_day2-4") or [])
        valid = [x for x in fcs if isinstance(x, dict) and utc(x.get("time")) and utc(x.get("time")) >= now]
        with_wind = [x for x in valid if x.get("wind_from") and isinstance(x.get("wind_speed"), (int, float))]
        issued, start, end = utc(obj.get("issued")), utc(obj.get("valid_from")), utc(obj.get("valid_to"))
        is_current = bool(start and end and start <= now < end)
        result.update({
            "http": "OK", "name": obj.get("name"),
            "issued_utc": ts(issued), "valid_from_utc": ts(start), "valid_to_utc": ts(end),
            "active_now": is_current, "forecast_rows": len(fcs),
            "future_rows": len(valid), "future_rows_with_wind": len(with_wind),
            "first_future_wind": ({k: with_wind[0].get(k) for k in ("time", "wind_from", "wind_speed", "wind_gust")} if with_wind else None),
            "problems": []
        })
        if not is_current:
            result["problems"].append("Masa berlaku tidak mencakup waktu eksekusi")
        if not with_wind:
            result["problems"].append("Tidak ditemukan prakiraan mendatang dengan arah dan kecepatan angin")
        if issued and (now - issued).total_seconds() > 60 * 60 * 48:
            result["problems"].append("Waktu penerbitan sudah lebih dari 48 jam")
    except urllib.error.HTTPError as e:
        result.update({"http": e.code, "problems": [f"HTTP {e.code}"]})
    except Exception as e:
        result.update({"http": "ERROR", "problems": [f"{type(e).__name__}: {str(e)[:250]}"]})
    return result


def probe_modelrun():
    url = f"{BASE}/pusmar/api23/modelrun"
    result = {"url": url, "stage": "modelrun discovery only; NOT yet verified wind grid", "array_fetch_attempted": False}
    try:
        obj = load_url(url)
        result.update({"http": "OK", "json_type": type(obj).__name__,
                       "top_level_keys": list(obj)[:25] if isinstance(obj, dict) else None,
                       "sample": json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:1800]})
    except urllib.error.HTTPError as e:
        result.update({"http": e.code, "error": f"HTTP {e.code}; service may be unavailable or restricted"})
    except Exception as e:
        result.update({"http": "ERROR", "error": f"{type(e).__name__}: {str(e)[:250]}"})
    return result


def main():
    now = datetime.now(timezone.utc)
    report = {"audit_utc": ts(now), "purpose": "Read-only initial feasibility check, not forecast product", "areas": [], "inawaves": {}}
    for code in AREAS:
        a = probe_area(code, now)
        report["areas"].append(a)
        print(code, a.get("http"), a.get("name", ""), "active_now=", a.get("active_now"), "future_wind=", a.get("future_rows_with_wind"), flush=True)
        time.sleep(1.5)
    report["inawaves"] = probe_modelrun()
    print("INAWAVES modelrun:", report["inawaves"].get("http"), flush=True)
    (OUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["# Hasil uji awal BMKG Maritim", "", f"Waktu pengujian (UTC): {ts(now)}", "", "## Prakiraan perairan", "",
             "| Area | Respons | Aktif saat uji | Waktu prakiraan mendatang dengan angin | Catatan |", "|---|---|---|---:|---|"]
    for a in report["areas"]:
        comments = "; ".join(a.get("problems", [])) or "—"
        lines.append(f"| {a['code']} – {a.get('name','')} | {a.get('http','')} | {a.get('active_now','?')} | {a.get('future_rows_with_wind','?')} | {comments} |")
    lines += ["", "## INAWAVES", "", f"Modelrun response: **{report['inawaves'].get('http')}**.",
              "", "Dokumentasi mencantumkan `wind` = U/V, tetapi respons modelrun saja belum membuktikan ketersediaan grid, tinggi referensi, koordinat, dan satuan. Jangan gunakan sebagai sumber streamline sebelum semua diverifikasi.",
              "", "## Keputusan awal", ""]
    good = sum(1 for a in report["areas"] if a.get("active_now") and a.get("future_rows_with_wind", 0) > 0)
    if good:
        lines.append(f"- {good}/{len(AREAS)} area menunjukkan prakiraan angin yang masih berlaku dan memiliki waktu ke depan.")
        lines.append("- Jika grid INAWAVES belum dapat diakses, jalur cadangan adalah data berbasis poligon dengan label 'interpolasi antarwilayah', bukan grid model asli.")
    else:
        lines.append("- Belum ditemukan area yang memenuhi syarat; jangan publikasikan produk baru.")
    lines.append("- Uji lanjutan: konfirmasi tinggi referensi angin laut, QC kesesuaian waktu dengan ADM4, dan hak pemanfaatan ulang API hasil pengolahan.")
    (OUT / "RINGKASAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = Path(__import__('os').environ.get('GITHUB_STEP_SUMMARY', ''))
    if str(summary) != '.':
        try:
            with summary.open('a', encoding='utf-8') as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass
    # We intentionally finish successfully even if network fails, so the user gets an audit artifact.
    return 0


if __name__ == "__main__":
    sys.exit(main())
