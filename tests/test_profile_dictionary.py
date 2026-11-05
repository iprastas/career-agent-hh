"""Profile skills are selected from the database, never arbitrary user input."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app.server import main, store


@pytest.fixture()
def profile_client(tmp_path, monkeypatch):
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE vacancy_skills(vacancy_id TEXT, skill_canon TEXT, skill_raw TEXT)")
        con.executemany("INSERT INTO vacancy_skills VALUES(?,?,?)", [
            ("1", "python", "Python"), ("2", "sql", "SQL"),
            ("3", "machine learning", "Machine Learning|ML"),
            ("4", "python", "Python"),
        ])
    monkeypatch.setattr(config, "APP_DB", str(database))
    monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(store, "USERS_DB", str(tmp_path / "users.db"))
    client = TestClient(main.app)
    token = client.post("/api/register", json={"email": "profile@example.org", "password": "test-password-123"}).json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    return client


def test_dictionary_contains_all_database_skills(profile_client):
    response = profile_client.get("/api/skills")
    assert response.status_code == 200
    skills = response.json()["skills"]
    assert {"python", "sql", "machine learning"} <= set(skills)
    assert len(skills) == len(set(skills))


def test_unknown_skill_rejected_without_overwriting_profile(profile_client):
    assert profile_client.put("/api/profile", json={"skills": ["Python"]}).status_code == 200
    response = profile_client.put("/api/profile", json={"skills": ["совершенно посторонний текст"]})
    assert response.status_code == 422
    saved = profile_client.get("/api/profile").json()["data"]
    assert [skill["name"] for skill in saved["skills"]] == ["python"]


def test_dictionary_aliases_and_duplicates(profile_client):
    response = profile_client.put("/api/profile", json={"skills": ["Python", "python", "ML"]})
    assert response.status_code == 200
    assert [skill["name"] for skill in response.json()["data"]["skills"]] == ["python", "machine learning"]
