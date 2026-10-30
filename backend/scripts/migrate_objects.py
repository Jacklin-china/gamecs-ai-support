"""Copy legacy MinIO attachments without overwriting existing objects.

Set LEGACY_MINIO_ENDPOINT, LEGACY_MINIO_ACCESS_KEY, LEGACY_MINIO_SECRET_KEY,
and target MINIO_* settings. Run without --copy to check source availability.
"""
import argparse
import hashlib
import os
import uuid
from urllib.parse import unquote, urlparse
from minio import Minio
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import TicketImage
from app.storage import client, ensure_bucket


def hash_stream(response):
    digest = hashlib.sha256()
    try:
        for chunk in response.stream(64 * 1024):
            digest.update(chunk)
    finally:
        response.close()
        response.release_conn()
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()
    endpoint = os.getenv("LEGACY_MINIO_ENDPOINT")
    access = os.getenv("LEGACY_MINIO_ACCESS_KEY")
    secret = os.getenv("LEGACY_MINIO_SECRET_KEY")
    if not endpoint or not access or not secret:
        raise SystemExit("Set LEGACY_MINIO_ENDPOINT, LEGACY_MINIO_ACCESS_KEY and LEGACY_MINIO_SECRET_KEY.")
    source = Minio(endpoint, access_key=access, secret_key=secret, secure=os.getenv("LEGACY_MINIO_SECURE", "false").lower() == "true")
    destination = client() if args.copy else None
    copied = checked = 0
    with SessionLocal() as db:
        images = db.scalars(select(TicketImage).where(TicketImage.legacy_url.is_not(None))).all()
        for image in images:
            path = unquote(urlparse(image.legacy_url).path).lstrip("/")
            bucket, sep, key = path.partition("/")
            if not sep or not key:
                raise RuntimeError(f"Image {image.id}: invalid source reference")
            info = source.stat_object(bucket, key)
            if info.size != image.size:
                raise RuntimeError(f"Image {image.id}: source size does not match database")
            checked += 1
            if not args.copy:
                continue
            if image.object_key.startswith("migration/"):
                if hash_stream(source.get_object(bucket, key)) != hash_stream(destination.get_object(image.bucket, image.object_key)):
                    raise RuntimeError(f"Image {image.id}: previously copied object is missing or has changed")
                continue
            # A random destination key avoids overwriting an existing object.
            target_bucket = settings().minio_bucket
            ensure_bucket(destination, target_bucket)
            new_key = f"migration/{uuid.uuid4().hex}"
            stream = source.get_object(bucket, key)
            try:
                destination.put_object(target_bucket, new_key, stream, info.size, content_type=image.content_type)
            finally:
                stream.close()
                stream.release_conn()
            try:
                if hash_stream(source.get_object(bucket, key)) != hash_stream(destination.get_object(target_bucket, new_key)):
                    raise RuntimeError(f"Image {image.id}: checksum mismatch")
                image.bucket = target_bucket
                image.object_key = new_key
                db.commit()
            except Exception:
                db.rollback()
                destination.remove_object(target_bucket, new_key)
                raise
            copied += 1
    print(f"Checked {checked} legacy attachments; copied {copied} new objects.")


if __name__ == "__main__":
    main()
