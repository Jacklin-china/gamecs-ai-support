"""Smoke-test the disposable `gamecs-ci` Compose stack; never use on real data."""
import json
import os
import subprocess
import urllib.error
import urllib.request

PLAYER = "http://127.0.0.1:8080"
STAFF = "http://127.0.0.1:8081"


def call(base, path, method="GET", data=None, token=None, content_type=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        if response.headers.get_content_type() == "application/json":
            return json.loads(body)
        return body


def sql(statement):
    # This is invoked only inside the isolated GitHub Actions Compose project.
    subprocess.run(["docker", "compose", "-p", "gamecs-ci", "-f", "infra/docker-compose.yml", "exec", "-T", "mysql", "sh", "-c", 'exec mysql -u gamecs -p"$MYSQL_PASSWORD" gamecs_new'],
                   input=statement, text=True, check=True, stdout=subprocess.DEVNULL)


def main():
    if os.environ.get("GAMECS_COMPOSE_TEST") != "isolated-ci-only":
        raise SystemExit("This script is only for an isolated gamecs-ci Compose stack")
    assert b"<html" in call(PLAYER, "/").lower()
    assert b"<html" in call(STAFF, "/").lower()
    sql("INSERT INTO categories (id,parent_id,name,level,active) VALUES (1,0,'Synthetic Game',1,1),(2,1,'Version',2,1),(3,2,'Issue',3,1);")
    for name in ("ci_player", "ci_staff"):
        call(PLAYER, "/api/v1/auth/register", "POST", {"username": name, "password": "ci-test-password"})
    sql("UPDATE users SET role='staff' WHERE username='ci_staff';")
    player = call(PLAYER, "/api/v1/auth/login", "POST", {"username":"ci_player", "password":"ci-test-password"})["access_token"]
    staff = call(STAFF, "/api/v1/auth/login", "POST", {"username":"ci_staff", "password":"ci-test-password"})["access_token"]
    ticket = call(PLAYER, "/api/v1/tickets", "POST", {"game_id":1, "version_id":2, "category_id":3, "title":"Synthetic ticket", "content":"Test"}, player)
    tid = ticket["id"]
    # More than nginx's default 1 MB, below the app's 5 MB limit.
    picture = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)
    boundary = "isolated-gamecs-boundary"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + picture + f'\r\n--{boundary}--\r\n'.encode())
    image = call(PLAYER, f"/api/v1/tickets/{tid}/images", "POST", body, player, f"multipart/form-data; boundary={boundary}")
    assert call(STAFF, f"/api/v1/tickets/{tid}/images/{image['id']}", token=staff) == picture
    call(STAFF, f"/api/v1/tickets/{tid}/assign", "POST", token=staff)
    call(STAFF, f"/api/v1/tickets/{tid}/messages", "POST", {"content":"Synthetic reply"}, staff)
    call(STAFF, f"/api/v1/tickets/{tid}/transition", "POST", {"status":"CLOSED", "remark":"Resolved"}, staff)
    call(PLAYER, f"/api/v1/tickets/{tid}/rating", "POST", {"score":5, "comment":"Thanks"}, player)
    assert call(PLAYER, f"/api/v1/tickets/{tid}", token=player)["status"] == "RATED"
    knowledge = call(STAFF, "/api/v1/knowledge", "POST", {"game_id":1, "title":"Synthetic knowledge", "content":"Test", "active":True}, staff)
    assert knowledge["index_status"] == "pending"
    print("Compose smoke: web gateways, registration, MySQL, Redis limits, MinIO, ticket lifecycle and knowledge passed")


if __name__ == "__main__":
    main()
