from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.api import clinical, mobile
from backend.app.api.deps import get_container
from backend.app.services.mobile_alerts import MobileAlerts, MobileAuthError, MobileRateLimit


def publish(service, patient="a", tier="URGENT"):
    return service.publish(patient_id=patient, event_id="message-job", tier=tier,
                           patient_name="Test Patient", vitals={"heart_rate": 148}, text="Review vitals")


def pair(service, patient="a"):
    code = service.create_pairing(patient, "Test Patient")["code"]
    return service.claim(code, "127.0.0.1")


def test_patient_scope_cursor_and_single_use():
    service = MobileAlerts()
    publish(service)
    code = service.create_pairing("a", "Test Patient")["code"]
    device = service.claim(code, "127.0.0.1")
    with pytest.raises(MobileAuthError):
        service.claim(code, "127.0.0.1")
    assert service.feed(device["token"], device["cursor"])["events"] == []
    publish(service, "b")
    queued = publish(service)
    feed = service.feed(device["token"], device["cursor"])
    assert len(feed["events"]) == 1
    assert feed["events"][0]["event_id"] == queued["event_id"]
    assert feed["events"][0]["phase"] == "urgent"
    assert "patient_id" not in feed["events"][0]
    assert service.feed(device["token"], feed["cursor"])["events"] == []


def test_expiration_presence_and_disconnect(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.app.services.mobile_alerts.time.monotonic", lambda: clock[0])
    service = MobileAlerts()
    device = pair(service)
    assert service.status("a") == {"paired": 1, "connected": 0}
    service.feed(device["token"], 0)
    assert service.status("a")["connected"] == 1
    publish(service)
    clock[0] += 11
    assert service.status("a")["connected"] == 0
    clock[0] += 120
    assert service.feed(device["token"], 0)["events"] == []
    assert service.feed(device["token"], 0)["cursor"] == 1
    service.disconnect(device["token"])
    with pytest.raises(MobileAuthError):
        service.feed(device["token"], 0)
    device = pair(service)
    clock[0] += service.SESSION_SECONDS + 1
    with pytest.raises(MobileAuthError):
        service.feed(device["token"], 0)


def test_pairing_expiration_replacement_and_rate_limit(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("backend.app.services.mobile_alerts.time.monotonic", lambda: clock[0])
    service = MobileAlerts()
    first = service.create_pairing("a", "Test")["code"]
    second = service.create_pairing("a", "Test")["code"]
    assert first not in service._pairings or first == second
    clock[0] = 601
    with pytest.raises(MobileAuthError):
        service.claim(second, "host")
    for _ in range(4):
        with pytest.raises(MobileAuthError):
            service.claim("invalid", "host")
    with pytest.raises(MobileRateLimit):
        service.claim("invalid", "host")
    clock[0] += 61
    with pytest.raises(MobileAuthError):
        service.claim("invalid", "host")


@pytest.fixture
def mobile_client(monkeypatch):
    patient = SimpleNamespace(
        profile=SimpleNamespace(patient_id="a", display_name="Test Patient"),
        last_processed=None, stored_observations=0,
        readings=[SimpleNamespace(timestamp=datetime(2026, 11, 13), heart_rate=72)],
    )
    result = SimpleNamespace(
        tier=SimpleNamespace(value="URGENT"), alert_kind=SimpleNamespace(value="CLINICAL"),
        suppressed=False, news2=None, explanation=None, drift=[],
        to_dict=lambda: {"tier": "URGENT"},
    )

    def get_patient(patient_id):
        if patient_id != "a":
            raise KeyError(patient_id)
        return patient

    service = SimpleNamespace(patient=get_patient, _score_timestamp=Mock(return_value=result),
                              hapi=SimpleNamespace(store_bundle=AsyncMock()))
    container = SimpleNamespace(clinical=service, mobile_alerts=MobileAlerts())
    wa = Mock(return_value={"sent": True})
    tg = Mock(return_value={"sent": True})
    monkeypatch.setattr(clinical, "send_whatsapp_alert", wa)
    monkeypatch.setattr(clinical, "send_telegram_alert", tg)
    monkeypatch.setattr(clinical, "compare_with_xgboost", lambda **kwargs: {})
    app = FastAPI()
    app.include_router(mobile.router)
    app.include_router(clinical.router)
    app.dependency_overrides[get_container] = lambda: container
    with TestClient(app) as client:
        yield client, container.mobile_alerts, result, wa, tg


def post_reading(client, notify=True):
    return client.post("/api/clinical/patients/a/readings", json={
        "timestamp": "2026-11-13T10:00:00Z", "heart_rate": 148, "spo2": 84,
        "respiratory_rate": 28, "temperature": 37.3,
        "source": "smartwatch_demo", "send_whatsapp": notify,
    })


@pytest.mark.parametrize("tier", ["WATCH", "REVIEW", "URGENT"])
def test_reading_uses_same_event_for_mobile_and_messages(mobile_client, tier):
    client, service, result, wa, tg = mobile_client
    result.tier.value = tier
    device = pair(service)
    response = post_reading(client)
    assert response.status_code == 200
    data = response.json()
    events = service.feed(device["token"], device["cursor"])["events"]
    assert len(events) == 1
    assert events[0]["event_id"] == data["whatsapp"]["job_id"] == data["mobile"]["event_id"]
    assert events[0]["phase"] == tier.lower()
    assert events[0]["vitals"]["heart_rate"] == 148
    assert wa.call_args.kwargs["vitals"] == events[0]["vitals"]
    assert tg.call_args.kwargs["tier"] == tier


@pytest.mark.parametrize("tier,kind,suppressed,notify", [
    ("NORMAL", "CLINICAL", False, True),
    ("URGENT", "SENSOR", False, True),
    ("URGENT", "DEVICE_OFFLINE", False, True),
    ("URGENT", "CLINICAL", True, True),
    ("URGENT", "CLINICAL", False, False),
])
def test_no_mobile_alarm_when_messages_are_suppressed(mobile_client, tier, kind, suppressed, notify):
    client, service, result, wa, tg = mobile_client
    result.tier.value, result.alert_kind.value, result.suppressed = tier, kind, suppressed
    device = pair(service)
    response = post_reading(client, notify)
    assert response.status_code == 200
    assert "mobile" not in response.json()
    assert service.feed(device["token"], 0)["events"] == []
    wa.assert_not_called()
    tg.assert_not_called()


def test_message_failure_does_not_block_other_channels(mobile_client):
    client, service, _, wa, tg = mobile_client
    wa.side_effect = RuntimeError("Provider unavailable")
    device = pair(service)
    data = post_reading(client).json()
    assert service.feed(device["token"], 0)["events"]
    tg.assert_called_once()
    job = client.get(f"/api/clinical/whatsapp/{data['whatsapp']['job_id']}").json()
    assert job["sent"] is False
    assert job["telegram"]["sent"] is True


def test_mobile_routes_require_token_and_scope_patient(mobile_client):
    client, service, _, _, _ = mobile_client
    assert client.get("/api/clinical/mobile/feed").status_code == 401
    assert client.get("/api/clinical/mobile/feed", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/clinical/mobile/pairings", json={"patient_id": "missing"}).status_code == 404
    pairing = client.post("/api/clinical/mobile/pairings", json={"patient_id": "a"})
    assert pairing.headers["cache-control"] == "no-store"
    claim = client.post("/api/clinical/mobile/claim", json={"code": pairing.json()["code"]})
    device = claim.json()
    headers = {"Authorization": f"Bearer {device['token']}"}
    feed = client.get("/api/clinical/mobile/feed", headers=headers)
    assert feed.status_code == 200
    assert feed.headers["cache-control"] == "no-store"
    assert feed.json()["patient"]["id"] == "a"
    assert feed.json()["patient"]["vitals"]["heart_rate"] == 72
    assert client.get("/api/clinical/mobile/feed?after=-1", headers=headers).status_code == 422
    assert client.delete("/api/clinical/mobile/connection", headers=headers).status_code == 200
    assert client.get("/api/clinical/mobile/feed", headers=headers).status_code == 401
