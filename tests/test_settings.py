from app.settings import env


def test_new_names_win_and_old_names_still_work(monkeypatch):
    monkeypatch.delenv("SCREENPROOF_X_TEST", raising=False)
    monkeypatch.setenv("HIREREADY_X_TEST", "old")
    assert env("X_TEST") == "old"  # an existing deploy keeps working before its variables are renamed
    monkeypatch.setenv("SCREENPROOF_X_TEST", "new")
    assert env("X_TEST") == "new"
    assert env("NOT_SET_ANYWHERE", "default") == "default"
