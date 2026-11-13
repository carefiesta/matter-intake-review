"""The local web workbench and the command line use the same service."""

from fastapi.testclient import TestClient

from intake.cli import main
from intake.web import create_app


def test_health_and_sign_in_wall(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    anonymous = client.get("/matters", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"


def test_staff_sees_a_blocked_conflict_and_attorney_sees_no_candidates(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    client = TestClient(app)
    sign_in = client.post(
        "/login",
        data={"user_id": "casey.lin", "password": "intake-demo"},
        follow_redirects=False,
    )
    assert sign_in.status_code == 303
    loaded = client.post("/matters/load-samples", follow_redirects=False)
    assert loaded.status_code == 303
    page = client.get("/matters")
    assert "Hale sidewalk inquiry" in page.text
    assert "Nguyen parking-lot inquiry" in page.text
    matters = app.state.service.list_matters(
        __import__("intake.access", fromlist=["AccessContext"]).AccessContext(
            "casey.lin", "intake_staff", None
        )
    )
    nguyen = next(matter for matter in matters if "Nguyen" in matter["title"])
    blocked = client.post(
        f"/matters/{nguyen['matter_id']}/ready",
        data={"reason": "too soon"},
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert "blocked" in blocked.headers["location"].lower()
    workbench = client.get(f"/matters/{nguyen['matter_id']}")
    assert "March 3, 2026" in workbench.text
    assert "No winning value" in workbench.text

    client.post("/logout", follow_redirects=False)
    client.post(
        "/login",
        data={"user_id": "riley.brooks", "password": "intake-demo"},
        follow_redirects=False,
    )
    attorney_page = client.get(f"/matters/{nguyen['matter_id']}")
    assert "Parker Cole" not in attorney_page.text
    assert "not released" in attorney_page.text.casefold()
    report = client.get(f"/matters/{nguyen['matter_id']}/report?audience=attorney")
    assert report.status_code == 403

    client.post("/logout", follow_redirects=False)
    client.post(
        "/login",
        data={"user_id": "sam.patel", "password": "intake-demo"},
        follow_redirects=False,
    )
    hidden = client.get(f"/matters/{nguyen['matter_id']}")
    assert hidden.status_code == 404
    assert "Parker Cole" not in hidden.text
    assert "Nguyen" not in hidden.text


def test_cli_loads_samples_and_prints_a_report(tmp_path, monkeypatch):
    db = tmp_path / "cli.db"
    monkeypatch.setenv("INTAKE_DB", str(db))
    assert main(["init"]) == 0
    assert main(["load-samples", "--user", "casey.lin", "--password", "intake-demo"]) == 0
    assert main(["list", "--user", "sam.patel", "--password", "intake-demo"]) == 0
    # sam has no matters, so list is empty and still succeeds.
    from intake.service import IntakeService
    from intake.access import AccessContext

    service = IntakeService(db)
    service.init_db()
    rows = service.list_matters(AccessContext("casey.lin", "intake_staff", None))
    hale = next(row for row in rows if row["sample_key"] == "hale-clean")
    assert main([
        "report",
        "--user", "casey.lin",
        "--password", "intake-demo",
        "--matter", hale["matter_id"],
        "--audience", "attorney",
    ]) == 0
    blocked = main([
        "ready",
        "--user", "casey.lin",
        "--password", "intake-demo",
        "--matter", next(row["matter_id"] for row in rows if row["sample_key"] == "nguyen-conflict"),
        "--reason", "too soon",
    ])
    assert blocked == 3
