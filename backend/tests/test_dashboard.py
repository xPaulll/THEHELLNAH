import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_dashboard_summary():
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "connections" in data
    assert "mt5" in data["connections"]
    assert "fastapi" in data["connections"]
    assert "supabase" in data["connections"]
    assert "pipeline" in data["connections"]
    assert "market" in data
    assert "data_quality" in data
    assert "account" in data
    assert "recent_events" in data

def test_dashboard_candles():
    response = client.get("/api/v1/dashboard/candles?symbol=XAUUSD.vx&timeframe=M1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "candles" in data

def test_dashboard_logs():
    response = client.get("/api/v1/dashboard/logs?limit=20")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "logs" in data

def test_dashboard_static_mounted():
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "alped punya" in response.text.lower()
    assert "market data center" in response.text.lower()
