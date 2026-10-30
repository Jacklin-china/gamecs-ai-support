"""Opt-in integration test against an isolated local MinIO process."""
import base64
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from minio import Minio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import storage
from app.auth import hash_password
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Category, User


@pytest.mark.skipif(not os.environ.get('MINIO_TEST_BINARY'), reason='set MINIO_TEST_BINARY for isolated MinIO integration')
def test_attachment_roundtrip_with_real_minio(tmp_path, monkeypatch):
    binary = Path(os.environ['MINIO_TEST_BINARY'])
    if not binary.is_file():
        pytest.fail('MINIO_TEST_BINARY does not exist')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env['MINIO_ROOT_USER'] = 'gamecs-test-only'
    env['MINIO_ROOT_PASSWORD'] = 'gamecs-test-only-secret'
    process = subprocess.Popen([str(binary), 'server', str(tmp_path), '--address', f'127.0.0.1:{port}'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        client = Minio(f'127.0.0.1:{port}', access_key=env['MINIO_ROOT_USER'], secret_key=env['MINIO_ROOT_PASSWORD'], secure=False)
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail('isolated MinIO exited during startup')
            try:
                client.list_buckets()
                break
            except Exception:
                time.sleep(.1)
        else:
            pytest.fail('isolated MinIO did not become ready')
        monkeypatch.setattr(storage, 'client', lambda: client)
        monkeypatch.setattr(settings(), 'minio_bucket', 'gamecs-test')
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as db:
            db.add_all([Category(id=1,parent_id=0,name='Game',level=1), Category(id=2,parent_id=1,name='Version',level=2), Category(id=3,parent_id=2,name='Issue',level=3), User(username='testplayer',display_name='Test',password_hash=hash_password('password'),role='player')])
            db.commit()
        def override():
            with factory() as db:
                yield db
        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as api:
                token = api.post('/api/v1/auth/login', json={'username':'testplayer','password':'password'}).json()['access_token']
                headers = {'Authorization':f'Bearer {token}'}
                ticket = api.post('/api/v1/tickets', headers=headers, json={'game_id':1,'version_id':2,'category_id':3,'title':'Test','content':'Test'}).json()
                image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
                url = f"/api/v1/tickets/{ticket['id']}/images"
                response = api.post(url, headers=headers, files={'file':('pixel.png',image,'image/png')})
                assert response.status_code == 201, response.text
                downloaded = api.get(f"{url}/{response.json()['id']}", headers=headers)
                assert downloaded.status_code == 200 and downloaded.content == image
                assert api.get(f"{url}/{response.json()['id']}").status_code == 401
        finally:
            app.dependency_overrides.clear()
            engine.dispose()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
