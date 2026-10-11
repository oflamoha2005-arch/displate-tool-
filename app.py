from __future__ import annotations

import csv
import io as _io
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from processor import analyze, analyze_and_prepare, compute_risk_score
from requirements_config import DEFAULT_PROFILE
from status_model import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    OverallStatus,
    ThresholdType,
    aggregate_overall_status,
)
from hash_store import (
    store_hash, find_duplicates, delete_hash,
    cleanup_old_hashes, available as hash_available,
)

BASE_DIR      = Path(__file__).resolve().parent
JOBS          = BASE_DIR / "jobs"
JOBS.mkdir(exist_ok=True)
MAX_UPLOAD_MB = 80
MAX_BATCH     = 5
JOB_TTL_HOURS = 24
ALLOWED       = {".jpg", ".jpeg", ".png", ".webp", ".avif"}

app = FastAPI(title="Displate One-Click Preparer")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ── Helpers ────────────────────────────────────────────────────────────────────

def safe_name(name: str) -> str:
    keep = [ch if (ch.isalnum() or ch in "._- ") else "_" for ch in name]
    return "".join(keep).strip(" .")[:150] or "image.jpg"


def _cleanup_old_jobs():
    now    = time.time()
    cutoff = now - JOB_TTL_HOURS * 3600
    for d in JOBS.iterdir():
        if d.is_dir() and d.name != "hashes.json":
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    cleanup_old_hashes()


def _download_urls(job_id: str) -> dict:
    return {
        "final_url":        f"/download/{job_id}/displate_ready.jpg",
        "preview_url":      f"/download/{job_id}/safe_area_preview.jpg",
        "mask_preview_url": f"/download/{job_id}/flat_mask_preview.png",
        "report_url":       f"/download/{job_id}/report.json",
        "zip_url":          f"/download/{job_id}/displate_ready_package.zip",
    }


def _build_zip(job_dir: Path, output_dir: Path):
    zip_path = job_dir / "displate_ready_package.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in output_dir.iterdir():
            if path.is_file():
                archive.write(path, path.name)


def _write_readme(output_dir: Path, final: dict, manual_review: list):
    is_up = final.get("upscaled", False)
    up_detail = final.get("upscale_info") or {}
    if is_up:
        up_text = f"YES (Real-ESRGAN {up_detail.get('model', '')} {up_detail.get('scale', 4)}x)"
    else:
        up_text = "NO"

    txt = [
        "DISPLATE ONE-CLICK PREPARER REPORT", "",
        f"Final size: {final['width']} × {final['height']} px",
        "Ratio: 5:7 / 1:1.4", "DPI: 300", "Colour profile: sRGB", "Format: JPEG",
        f"JPEG quality: {final['quality']}", f"File size: {final['file_mb']} MB",
        f"Upscaled: {up_text}", "", "MANUAL REVIEW REQUIRED:",
    ]
    txt += [f"- {item}" for item in manual_review]
    (output_dir / "README_FIRST.txt").write_text("\n".join(txt), encoding="utf-8")


def _inject_duplicate_check(report: dict, duplicates: list[dict]) -> dict:
    """Add near_duplicate heuristic check into the report and recompute overall status."""
    if duplicates:
        best = duplicates[0]
        dup_check = CheckResult(
            id="near_duplicate",
            name="Near-Duplicate Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING,
            value=duplicates,
            threshold=DEFAULT_PROFILE.phash_distance_threshold,
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=(
                f"Possible local near-duplicate of job {best['job_id']} "
                f"({best['similarity']}% similar, Hamming distance {best['distance']}). "
                "Local screening heuristic; not a Displate re-upload decision."
            ),
            details={"matches": duplicates, "best_match": best},
        )
    else:
        dup_check = CheckResult(
            id="near_duplicate",
            name="Near-Duplicate Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.PASS,
            value=[],
            threshold=DEFAULT_PROFILE.phash_distance_threshold,
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message="No near-duplicate found in recent local jobs.",
            details={"matches": []},
        )

    dup_dict = dup_check.to_dict()

    if isinstance(report.get("checks"), dict):
        report["checks"].setdefault("internal_heuristics", []).append(dup_dict)
    elif isinstance(report.get("checks"), list):
        report["checks"].append(dup_dict)

    if "checks_flat" in report:
        report["checks_flat"].append(dup_dict)

    all_check_results = []
    if "checks_flat" in report:
        for c in report["checks_flat"]:
            try:
                all_check_results.append(CheckResult.from_dict(c))
            except Exception:
                pass
    elif isinstance(report.get("checks"), dict):
        for cat_list in report["checks"].values():
            for c in cat_list:
                try:
                    all_check_results.append(CheckResult.from_dict(c))
                except Exception:
                    pass

    if all_check_results:
        overall_status, counts = aggregate_overall_status(all_check_results)
        report["overall"] = {
            "status": overall_status.value,
            **counts,
        }
        report["final_status"] = overall_status.value
        report["blocked"] = (overall_status == OverallStatus.BLOCKED)
        val_complete = bool(counts.get("validation_complete", False))
        report["ready_for_prepare"] = (not report["blocked"] and val_complete)

    if "risk_score" in report:
        del report["risk_score"]
    return report


def _dup_check_entry(status: str, message: str, value: list) -> dict:
    stat_enum = CheckStatus.WARNING if status in {"warn", "warning"} else CheckStatus.PASS
    return CheckResult(
        id="near_duplicate",
        name="Near-Duplicate Detection",
        category=CheckCategory.INTERNAL_HEURISTICS,
        status=stat_enum,
        value=value,
        threshold=DEFAULT_PROFILE.phash_distance_threshold,
        threshold_type=ThresholdType.INTERNAL_HEURISTIC,
        message=message,
        details={"matches": value},
    ).to_dict()


def _process_one(
    source: Path,
    output_dir: Path,
    focus: str,
    job_id: str,
    resize_mode: str = "recommended_size",
    auto_upscale: bool = True,
    upscale_model: str = "realesrgan-x4plus",
    texture_enabled: bool = False,
    texture_type: str = "fine_paper",
    texture_strength: str = "very_subtle",
) -> dict:
    """Run analyze_and_prepare, then inject duplicate check + risk score."""
    result = analyze_and_prepare(
        source,
        output_dir,
        focus,
        resize_mode=resize_mode,
        auto_upscale=auto_upscale,
        upscale_model=upscale_model,
        texture_enabled=texture_enabled,
        texture_type=texture_type,
        texture_strength=texture_strength,
    )
    report = result["report"]

    # Near-duplicate detection (requires imagehash to be installed)
    duplicates = find_duplicates(job_id, source)
    report     = _inject_duplicate_check(report, duplicates)
    result["report"] = report

    # Store hash only after successful prep (or always for blocked too)
    width = report.get("final", {}).get("width") or report.get("input", {}).get("width") or 0
    height = report.get("final", {}).get("height") or report.get("input", {}).get("height") or 0
    store_hash(job_id, source, width, height)
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    _cleanup_old_jobs()
    return templates.TemplateResponse(request=request, name="index.html", context={})


# ── NEW: One-click endpoint ────────────────────────────────────────────────────

@app.post("/api/process")
async def api_process(
    image: UploadFile = File(...),
    focus: str        = Form("center"),
    resize_mode: str  = Form("recommended_size"),
    auto_upscale: bool = Form(True),
    upscale_model: str = Form("realesrgan-x4plus"),
    texture_enabled: bool = Form(False),
    texture_type: str = Form("fine_paper"),
    texture_strength: str = Form("very_subtle"),
):
    """Analyse + prepare + duplicate-check + risk-score in a single request."""
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "Use JPG, PNG, WEBP, or AVIF.")
    data = await image.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Maximum upload size is {MAX_UPLOAD_MB} MB.")
    if focus not in {"center", "top", "bottom"}:
        focus = "center"

    job_id     = uuid.uuid4().hex[:12]
    job_dir    = JOBS / job_id
    job_dir.mkdir(parents=True)
    source     = job_dir / safe_name(image.filename or "image.jpg")
    source.write_bytes(data)
    output_dir = job_dir / "output"

    try:
        result = _process_one(
            source,
            output_dir,
            focus,
            job_id,
            resize_mode=resize_mode,
            auto_upscale=auto_upscale,
            upscale_model=upscale_model,
            texture_enabled=texture_enabled,
            texture_type=texture_type,
            texture_strength=texture_strength,
        )
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"Processing failed: {exc}")

    (job_dir / "report.json").write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not result["prepared"]:
        return {
            "job_id":     job_id,
            "prepared":   False,
            "report":     result["report"],
            "steps":      result.get("steps", []),
            "source_url": f"/media/{job_id}/{source.name}",
        }

    _build_zip(job_dir, output_dir)
    _write_readme(output_dir, result["report"]["final"], result["report"].get("manual_review", []))

    return {
        "job_id":     job_id,
        "prepared":   True,
        "steps":      result.get("steps", []),
        "report":     result["report"],
        "source_url": f"/media/{job_id}/{source.name}",
        **_download_urls(job_id),
    }


# ── NEW: Batch endpoint ────────────────────────────────────────────────────────

@app.post("/api/batch")
async def api_batch(
    request: Request,
    focus: str = Form("center"),
    resize_mode: str = Form("recommended_size"),
    auto_upscale: bool = Form(True),
    upscale_model: str = Form("realesrgan-x4plus"),
):
    form   = await request.form()
    images = form.getlist("images[]")
    if not images:
        raise HTTPException(400, "No images provided.")
    if len(images) > MAX_BATCH:
        raise HTTPException(400, f"Maximum {MAX_BATCH} images per batch.")
    if focus not in {"center", "top", "bottom"}:
        focus = "center"

    results = []
    for upload in images:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in ALLOWED:
            results.append({"filename": upload.filename, "error": "Unsupported format."})
            continue
        data = await upload.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            results.append({"filename": upload.filename, "error": "File too large."})
            continue

        job_id     = uuid.uuid4().hex[:12]
        job_dir    = JOBS / job_id
        job_dir.mkdir(parents=True)
        source     = job_dir / safe_name(upload.filename or "image.jpg")
        source.write_bytes(data)
        output_dir = job_dir / "output"

        try:
            result = _process_one(
                source,
                output_dir,
                focus,
                job_id,
                resize_mode=resize_mode,
                auto_upscale=auto_upscale,
                upscale_model=upscale_model,
            )
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            results.append({"filename": upload.filename, "error": str(exc)})
            continue

        (job_dir / "report.json").write_text(
            json.dumps(result["report"], ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if result["prepared"]:
            _build_zip(job_dir, output_dir)
            _write_readme(output_dir, result["report"]["final"], result["report"].get("manual_review", []))
            results.append({
                "filename":   upload.filename,
                "job_id":     job_id,
                "prepared":   True,
                "report":     result["report"],
                "source_url": f"/media/{job_id}/{source.name}",
                **_download_urls(job_id),
            })
        else:
            results.append({
                "filename":   upload.filename,
                "job_id":     job_id,
                "prepared":   False,
                "report":     result["report"],
                "source_url": f"/media/{job_id}/{source.name}",
            })

    return {"results": results, "total": len(results)}


# ── NEW: Standalone duplicate check ───────────────────────────────────────────

@app.get("/api/upscaler/info")
def api_upscaler_info():
    """Diagnostic info for the local AI upscaler (Real-ESRGAN)."""
    from upscaler import get_upscaler_info
    return get_upscaler_info()


@app.post("/api/check-duplicate")
async def api_check_duplicate(image: UploadFile = File(...)):
    """Pre-flight duplicate check without processing the image."""
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "Use JPG, PNG, WEBP, or AVIF.")
    data = await image.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Maximum upload size is {MAX_UPLOAD_MB} MB.")

    tmp_id  = "tmp_" + uuid.uuid4().hex[:8]
    tmp_dir = JOBS / tmp_id
    tmp_dir.mkdir(parents=True)
    tmp_src = tmp_dir / safe_name(image.filename or "image.jpg")
    tmp_src.write_bytes(data)

    try:
        duplicates = find_duplicates(tmp_id, tmp_src)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "has_duplicate": bool(duplicates),
        "matches":       duplicates,
        "hash_available": hash_available(),
    }


# ── NEW: Risk score for existing job ─────────────────────────────────────────

@app.get("/api/jobs/{job_id}/risk-score")
def api_risk_score(job_id: str):
    """Return status breakdown for an existing job (numerical risk scoring is deprecated)."""
    report_path = JOBS / job_id / "report.json"
    if not report_path.exists():
        raise HTTPException(404, "Job not found.")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "Could not read report.")
    return {
        "job_id": job_id,
        "deprecated": True,
        "risk_score": None,
        "final_status": report.get("final_status") or report.get("overall", {}).get("status", "UNKNOWN"),
        "overall": report.get("overall", {}),
    }


# ── NEW: Batch CSV report ─────────────────────────────────────────────────────

@app.post("/api/batch-report")
async def api_batch_report(request: Request):
    """
    Generate a CSV report for a list of job IDs.
    Body: {"job_ids": ["abc123", "def456", ...]}
    """
    payload = await request.json()
    job_ids = payload.get("job_ids", [])
    if not job_ids:
        raise HTTPException(400, "No job_ids provided.")

    output = _io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Job ID", "Filename", "Status", "Size Badge",
        "Final Width", "Final Height", "DPI",
        "Colour Profile", "File MB", "JPEG Quality",
        "Crop Kind", "Final Status", "Warnings Count",
        "Failed Checks", "Warned Checks",
    ])

    for job_id in job_ids:
        rp = JOBS / job_id / "report.json"
        if not rp.exists():
            writer.writerow([job_id, "NOT_FOUND"] + [""] * 13)
            continue
        try:
            report = json.loads(rp.read_text(encoding="utf-8"))
            final  = report.get("final", {})
            overall_status = (
                report.get("final_status")
                or report.get("overall", {}).get("status")
                or ("BLOCKED" if report.get("blocked") else "READY")
            )
            warn_count = report.get("overall", {}).get("warnings", 0)

            # Extract failed / warned check IDs cleanly
            checks_source = report.get("checks_flat")
            if not checks_source:
                if isinstance(report.get("checks"), list):
                    checks_source = report.get("checks")
                elif isinstance(report.get("checks"), dict):
                    checks_source = [item for sub in report["checks"].values() for item in sub]
                else:
                    checks_source = []

            failed = [c.get("id") or c.get("code", "") for c in checks_source if c.get("status") in {"FAIL", "fail"}]
            warned = [c.get("id") or c.get("code", "") for c in checks_source if c.get("status") in {"WARN", "warn", "WARNING"}]

            writer.writerow([
                job_id,
                report.get("input", {}).get("filename", report.get("filename", "")),
                "READY" if not report.get("blocked") else "BLOCKED",
                report.get("input", {}).get("size_badge", report.get("size_badge", "")),
                final.get("width",   report.get("width",  "")),
                final.get("height",  report.get("height", "")),
                (final.get("dpi") or report.get("dpi") or [""])[0],
                final.get("profile", report.get("profile", "")),
                final.get("file_mb", report.get("file_mb", "")),
                final.get("quality", ""),
                report.get("crop_kind", ""),
                overall_status,
                warn_count,
                "; ".join(filter(None, failed)) or "—",
                "; ".join(filter(None, warned)) or "—",
            ])
        except Exception as exc:
            writer.writerow([job_id, f"ERROR: {exc}"] + [""] * 13)

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM for Excel
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=displate_batch_report.csv"},
    )


# ── NEW: Delete a specific job ────────────────────────────────────────────────

@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: str):
    """Manually delete a job directory and its hash entry."""
    job_dir = JOBS / job_id
    if not job_dir.exists():
        raise HTTPException(404, "Job not found.")
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
        delete_hash(job_id)
    except Exception as exc:
        raise HTTPException(500, f"Could not delete job: {exc}")
    return {"status": "deleted", "job_id": job_id}


# ── Jobs list ──────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def api_jobs():
    """Return the 10 most recent jobs that have a report.json."""
    jobs = []
    dirs = sorted(
        (d for d in JOBS.iterdir() if d.is_dir()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    for d in dirs:
        rp = d / "report.json"
        if not rp.exists():
            continue
        try:
            report = json.loads(rp.read_text(encoding="utf-8"))
            final_status = (
                report.get("final_status")
                or report.get("overall", {}).get("status")
                or ("READY" if not report.get("blocked", True) else "BLOCKED")
            )
            jobs.append({
                "job_id":       d.name,
                "prepared":     not report.get("blocked", True),
                "final_status": final_status,
                "size_badge":   report.get("size_badge", ""),
                "width":        report.get("width"),
                "height":       report.get("height"),
                "risk_score":   None,
                "risk_level":   "none",
                "mtime":        d.stat().st_mtime,
            })
        except Exception:
            pass
        if len(jobs) >= 10:
            break
    return {"jobs": jobs}


@app.delete("/api/jobs/cleanup")
def api_cleanup():
    _cleanup_old_jobs()
    return {"status": "ok"}


# ── Legacy endpoints (backwards compat) ───────────────────────────────────────

@app.post("/api/analyze")
async def api_analyze(image: UploadFile = File(...), focus: str = Form("center")):
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "Use JPG, PNG, WEBP, or AVIF.")
    data = await image.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Maximum upload size is {MAX_UPLOAD_MB} MB.")

    job_id  = uuid.uuid4().hex[:12]
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True)
    source  = job_dir / safe_name(image.filename or "image.jpg")
    source.write_bytes(data)
    try:
        report = analyze(source, focus)
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"Could not analyse the image: {exc}")
    (job_dir / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"job_id": job_id, "source_url": f"/media/{job_id}/{source.name}", "report": report}


@app.post("/api/prepare/{job_id}")
async def api_prepare(job_id: str, request: Request):
    payload = await request.json()
    focus   = str(payload.get("focus", "center"))
    if focus not in {"center", "top", "bottom"}:
        focus = "center"
    job_dir = JOBS / job_id
    if not job_dir.exists():
        raise HTTPException(404, "Job not found.")
    source_candidates = [
        p for p in job_dir.iterdir()
        if p.is_file() and p.name not in {"analysis.json", "report.json"}
    ]
    if not source_candidates:
        raise HTTPException(404, "Source image not found.")
    source     = source_candidates[0]
    output_dir = job_dir / "output"

    texture_enabled = bool(payload.get("texture_enabled", False))
    texture_type = str(payload.get("texture_type", "fine_paper"))
    texture_strength = str(payload.get("texture_strength", "very_subtle"))

    result = _process_one(
        source,
        output_dir,
        focus,
        job_id,
        texture_enabled=texture_enabled,
        texture_type=texture_type,
        texture_strength=texture_strength,
    )
    (job_dir / "report.json").write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not result["prepared"]:
        return {"prepared": False, "report": result["report"]}

    _build_zip(job_dir, output_dir)
    _write_readme(output_dir, result["report"]["final"], result["report"].get("manual_review", []))
    return {
        "job_id":   job_id,
        "prepared": True,
        "report":   result["report"],
        "steps":    result.get("steps", []),
        **_download_urls(job_id),
    }


# ── File serving ───────────────────────────────────────────────────────────────

@app.get("/media/{job_id}/{filename}")
def media(job_id: str, filename: str):
    path = JOBS / job_id / filename
    if not path.exists():
        raise HTTPException(404, "File not found.")
    return FileResponse(path)


@app.get("/download/{job_id}/{filename}")
def download(job_id: str, filename: str):
    job_dir = JOBS / job_id
    for path in [
        job_dir / filename,
        job_dir / "output" / filename,
        job_dir / "preview" / filename,
        job_dir / "output" / "preview" / filename,
    ]:
        if path.exists() and path.is_file():
            return FileResponse(path, filename=path.name)
    raise HTTPException(404, "File not found.")
