"""End-to-end smoke test for StudyBuddy.

Boots the FastAPI server on a free port, then exercises:
  health, subjects, chat (grounded), chat (no syllabus match),
  session persistence + session restore.

Run:  python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                http("GET", f"{base}/api/health", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("server did not start in time")

        def check(name: str, ok: bool, detail: str = "") -> None:
            tag = "PASS" if ok else "FAIL"
            print(f"[{tag}] {name}" + (f" — {detail}" if detail and not ok else ""))
            if not ok:
                failures.append(name)

        # 1. health
        health = http("GET", f"{base}/api/health")
        check("health", health.get("status") == "ok" and "math" in health.get("subjects", []), json.dumps(health)[:160])

        # 2. subjects
        subs = http("GET", f"{base}/api/subjects")
        check("subjects listed", len(subs.get("subjects", [])) >= 2)
        math_sub = next((s for s in subs["subjects"] if s["id"] == "math"), None)
        check("math has ingested chunks", math_sub and math_sub["chunks"] > 0, str(math_sub))

        # 3. grounded chat
        r1 = http("POST", f"{base}/api/chat", {"subject_id": "math", "message": "How do I solve quadratic equations?"})
        check("grounded answer", r1.get("status") == "answered" and r1.get("sources"), json.dumps(r1)[:200])
        check("cites a unit", any("Quadratic" in s.get("unit", "") for s in r1.get("sources", [])), r1.get("reply", "")[:120])
        sid = r1.get("session_id", "")

        # 4. out-of-syllabus question -> no_match
        r2 = http("POST", f"{base}/api/chat", {"subject_id": "math", "message": "Tell me about the best pizza restaurants in Naples"})
        check("no syllabus match", r2.get("status") == "no_match" and not r2.get("sources"), json.dumps(r2)[:160])

        # 5. session continuity (follow-up on the same session)
        r3 = http("POST", f"{base}/api/chat",
                  {"subject_id": "math", "session_id": sid, "message": "Give me a worked example using the quadratic formula"})
        check("session continuity", r3.get("session_id") == sid and r3.get("status") == "answered", json.dumps(r3)[:160])

        # 6. physics grounded answer + PDF page citations
        r4 = http("POST", f"{base}/api/chat", {"subject_id": "physics", "message": "Explain friction on a box being pushed across a floor"})
        pages = [s for s in r4.get("sources", []) if s.get("page")]
        check("physics answered", r4.get("status") == "answered", json.dumps(r4)[:160])
        check("PDF page citation captured", any(s.get("page") for s in r4.get("sources", [])), str(pages)[:160])

        # 7. sessions listing + restore
        s_list = http("GET", f"{base}/api/sessions")
        session_ids = [s["id"] for s in s_list.get("sessions", [])]
        check("session persisted", sid in session_ids, str(session_ids))
        restored = http("GET", f"{base}/api/sessions/{sid}")
        check("session restore (4 turns)", len(restored.get("turns", [])) == 4, str(len(restored.get("turns", []))))

        # 8. frontend assets served
        with urllib.request.urlopen(f"{base}/", timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        check("frontend served", "StudyBuddy" in page and "composerInput" in page)
        for asset_hit in ("/static/styles.css", "/static/app.js"):
            with urllib.request.urlopen(f"{base}{asset_hit}", timeout=10) as resp:
                check(f"asset {asset_hit}", resp.status == 200 and len(resp.read()) > 1000)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"unexpected crash: {exc}")
        print(f"[FAIL] {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n" + ("All checks passed ✔" if not failures else f"{len(failures)} check(s) failed: {', '.join(failures)}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())