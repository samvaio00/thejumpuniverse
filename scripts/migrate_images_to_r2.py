#!/usr/bin/env python3
"""
One-time migration: move editions/images/*.png out of the git repo and into
Cloudflare R2 (as WebP), then rewrite every reference to point at the public
image domain.

Steps:
  1. Convert + upload every file in editions/images/ to R2 (shared helpers
     from generate.py: convert_to_webp / upload_image_to_r2).
  2. Rewrite editions/*.json image fields (hero_image, stories[*].image,
     comic_strip.image) from /editions/images/<name>.png to
     https://images.thejumpuniverse.com/<name>.webp — preserving each file's
     JSON formatting style. Editions already on https:// are skipped.
  3. Refresh index.html: re-run generate.prerender_index() on the lead
     edition, then a regex pass for any remaining /editions/images/ refs.
  4. Regenerate editions/manifest.json and rss.xml.
  5. Delete the migrated local image files so the follow-up git commit
     removes them from the repo tree.

Requires R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY in the environment.
Idempotent: safe to re-run; already-migrated editions are left untouched.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)  # generate.py uses relative paths
sys.path.insert(0, str(REPO_ROOT))

import generate  # noqa: E402

IMAGE_URL_RE = re.compile(r"(?:https?://[a-z0-9.-]+)?/editions/images/([A-Za-z0-9._-]+?)\.(png|jpg|jpeg|webp)")


def public_url_for(filename):
    """Public R2 URL an image filename migrates to (extension → .webp)."""
    stem = filename.rsplit(".", 1)[0]
    return f"{generate.R2_PUBLIC_BASE}/{stem}.webp"


def migrate_image_files():
    """Upload every local image to R2. Returns (uploaded, failed) filename lists."""
    image_dir = generate.IMAGE_DIR
    files = sorted(p for p in image_dir.glob("*") if p.is_file()) if image_dir.exists() else []
    uploaded, failed = [], []
    for path in files:
        try:
            url = generate.upload_image_to_r2(path.read_bytes(), path.name)
            uploaded.append(path.name)
            print(f"Uploaded {path.name} -> {url}")
        except Exception as e:
            failed.append(path.name)
            print(f"ERROR uploading {path.name}: {e}")
    return uploaded, failed


def rewrite_value(value):
    """Rewrite one image field value. Returns (new_value, changed)."""
    if not isinstance(value, str) or value.startswith("http"):
        return value, False
    m = IMAGE_URL_RE.fullmatch(value)
    if not m:
        return value, False
    return public_url_for(f"{m.group(1)}.{m.group(2)}"), True


def rewrite_editions():
    """Point every edition JSON's image fields at R2. Preserves formatting style."""
    changed_files = 0
    for path in sorted(generate.OUTPUT_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        original_text = path.read_text(encoding="utf-8")
        try:
            ed = json.loads(original_text)
        except json.JSONDecodeError as e:
            print(f"Skipping {path.name}: invalid JSON ({e})")
            continue

        changed = False
        new_hero, c = rewrite_value(ed.get("hero_image"))
        if c:
            ed["hero_image"] = new_hero
            changed = True
        for story in ed.get("stories") or []:
            if isinstance(story, dict):
                new_img, c = rewrite_value(story.get("image"))
                if c:
                    story["image"] = new_img
                    changed = True
        strip = ed.get("comic_strip")
        if isinstance(strip, dict):
            new_img, c = rewrite_value(strip.get("image"))
            if c:
                strip["image"] = new_img
                changed = True

        if not changed:
            continue
        # Match the file's existing style: indented (save_edition writes
        # indent=2) or compact single-line (older files).
        indent = 2 if original_text.lstrip().startswith("{\n") else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ed, f, indent=indent, ensure_ascii=False)
        changed_files += 1
        print(f"Rewrote {path.name}")
    return changed_files


def find_lead_edition():
    """Today's lead edition if published, else the newest date's lead/first edition."""
    today = datetime.now(timezone.utc)
    lead = generate.OUTPUT_DIR / f"{today.strftime('%Y-%m-%d')}-{generate.default_timeline_for(today)}.json"
    if lead.exists():
        return lead
    by_date = generate.edition_files_by_date()
    if not by_date:
        return None
    latest_date, files = sorted(by_date.items())[-1]
    date = datetime.strptime(latest_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    default = generate.OUTPUT_DIR / f"{latest_date}-{generate.default_timeline_for(date)}.json"
    return default if default.exists() else sorted(files)[0]


def rewrite_index_html():
    """Re-prerender index.html from the lead edition, then sweep any remaining
    /editions/images/ references (prerendered story images etc.)."""
    lead = find_lead_edition()
    if lead:
        with open(lead, encoding="utf-8") as f:
            generate.prerender_index(json.load(f))
    else:
        print("Warning: no lead edition found; skipping prerender")

    index_path = Path("index.html")
    if not index_path.exists():
        return
    html = index_path.read_text(encoding="utf-8")
    new_html, n = IMAGE_URL_RE.subn(
        lambda m: public_url_for(f"{m.group(1)}.{m.group(2)}"), html)
    if n:
        index_path.write_text(new_html, encoding="utf-8")
        print(f"index.html: rewrote {n} remaining /editions/images/ reference(s)")


def main():
    if not generate.r2_enabled():
        sys.exit(
            "ABORT: R2 credentials missing. Set R2_ACCESS_KEY_ID and "
            "R2_SECRET_ACCESS_KEY (and optionally R2_ACCOUNT_ID / R2_BUCKET / "
            "R2_PUBLIC_BASE) before running the migration."
        )

    uploaded, failed = migrate_image_files()
    print(f"\nUploaded {len(uploaded)} image(s), {len(failed)} failure(s)")
    if failed:
        sys.exit(f"ABORT: {len(failed)} upload(s) failed; nothing was rewritten or deleted. "
                 "Fix and re-run (idempotent).")

    changed = rewrite_editions()
    print(f"Rewrote {changed} edition file(s)")

    rewrite_index_html()
    generate.generate_manifest()
    generate.generate_rss()

    for name in uploaded:
        path = generate.IMAGE_DIR / name
        if path.exists():
            os.remove(path)
    print(f"Deleted {len(uploaded)} local image file(s) from {generate.IMAGE_DIR}")
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
