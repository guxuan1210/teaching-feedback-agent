def test_save_special_feedback(client, special_session):
    response = client.post("/sessions/SESSION2/special/S1", data={
        "rating_skill": "4", "rating_habit": "4",
        "progress_indicators": ["SK001", "SH001"],
        "weak_indicators": ["SKW003"], "note": "继续训练细节。",
    }, follow_redirects=False)
    assert response.status_code == 303
