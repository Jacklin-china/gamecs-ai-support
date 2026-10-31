"""Opt-in storage compatibility check using disposable MinIO and Silo processes."""
import os
import socket
import subprocess
import time
from io import BytesIO
from pathlib import Path

import pytest
from minio import Minio


@pytest.mark.skipif(not (os.environ.get("MINIO_TEST_BINARY") and os.environ.get("SILO_TEST_BINARY")), reason="set both disposable server binaries")
def test_synthetic_minio_data_can_be_read_by_silo_and_rollback(tmp_path):
    binaries = [Path(os.environ[key]) for key in ("MINIO_TEST_BINARY", "SILO_TEST_BINARY")]
    if not all(binary.is_file() for binary in binaries):
        pytest.fail("test server binary is missing")
    env = os.environ.copy()
    env.update(MINIO_ROOT_USER="gamecs-test-user", MINIO_ROOT_PASSWORD="gamecs-test-password")
    storage = tmp_path / "storage"
    storage.mkdir()

    def run_server(binary, action):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([str(binary), "server", str(storage), "--address", f"127.0.0.1:{port}"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        client = Minio(f"127.0.0.1:{port}", access_key=env["MINIO_ROOT_USER"], secret_key=env["MINIO_ROOT_PASSWORD"], secure=False)
        try:
            for _ in range(150):
                if process.poll() is not None:
                    pytest.fail("isolated storage server exited during startup")
                try:
                    client.list_buckets()
                    break
                except Exception:
                    time.sleep(.1)
            else:
                pytest.fail("isolated storage server did not become ready")
            action(client)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def read(client, key):
        result = client.get_object("gamecs-test", key)
        try:
            return result.read()
        finally:
            result.close()
            result.release_conn()

    def seed(client):
        client.make_bucket("gamecs-test")
        client.put_object("gamecs-test", "old", BytesIO(b"before upgrade"), 14)

    def upgrade(client):
        assert read(client, "old") == b"before upgrade"
        client.put_object("gamecs-test", "new", BytesIO(b"after upgrade"), 13)

    def rollback(client):
        assert read(client, "old") == b"before upgrade"
        assert read(client, "new") == b"after upgrade"

    run_server(binaries[0], seed)
    run_server(binaries[1], upgrade)
    run_server(binaries[0], rollback)
