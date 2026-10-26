import logging
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db
from app.models import Asset
from app.storage import get_storage
from app.thumbnail_service import create_image_thumbnail

logger = logging.getLogger("backfill_thumbnails")


def main() -> None:
    init_db()
    storage = get_storage()
    created = 0
    skipped = 0
    failed = 0

    with SessionLocal() as db:
        assets = db.scalars(
            select(Asset)
            .options(selectinload(Asset.files))
            .where(Asset.asset_type == "image")
            .order_by(Asset.created_at)
        ).all()
        for asset in assets:
            thumbnail = next((item for item in asset.files if item.kind == "thumbnail"), None)
            if thumbnail:
                if not asset.cover_key:
                    asset.cover_key = thumbnail.object_key
                    db.commit()
                skipped += 1
                continue

            original = next((item for item in asset.files if item.kind == "original"), None)
            if not original:
                failed += 1
                logger.warning("Asset %s has no original file", asset.id)
                continue

            try:
                result = create_image_thumbnail(storage, original, original.original_name)
                if not result:
                    failed += 1
                    logger.warning("Unsupported or invalid image for asset %s", asset.id)
                    continue
                thumbnail, source_width, source_height = result
                asset.files.append(thumbnail)
                asset.cover_key = thumbnail.object_key
                original.width = source_width
                original.height = source_height
                db.commit()
                created += 1
            except Exception:
                db.rollback()
                failed += 1
                logger.exception("Failed to create thumbnail for asset %s", asset.id)

    print(f"thumbnails_created={created}")
    print(f"thumbnails_skipped={skipped}")
    print(f"thumbnails_failed={failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
