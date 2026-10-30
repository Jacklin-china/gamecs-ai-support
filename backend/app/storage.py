from fastapi import HTTPException
from minio import Minio
from app.config import settings


def client():
    cfg = settings()
    if not cfg.minio_access_key or not cfg.minio_secret_key:
        raise HTTPException(503, "附件存储服务尚未配置")
    return Minio(cfg.minio_endpoint, access_key=cfg.minio_access_key, secret_key=cfg.minio_secret_key, secure=cfg.minio_secure)


def ensure_bucket(storage, bucket):
    from minio.error import S3Error
    if not storage.bucket_exists(bucket):
        try:
            storage.make_bucket(bucket)
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
