from urllib.parse import parse_qs, urlparse


def test_complete_data_collection_workflow(client):
    def created_id(path, data):
        response = client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303
        return parse_qs(urlparse(response.headers["location"]).query)["created"][0]

    teacher_id = created_id("/catalog/teachers", {"name": "王老师", "role": "晚辅教师"})
    class_id = created_id(
        "/catalog/classes",
        {"name": "三年级A班", "grade": "三年级", "class_type": "daily"},
    )
    student_id = created_id(
        "/catalog/students",
        {"name": "李明", "grade": "三年级", "current_stage": "三阶"},
    )
    client.post(
        "/catalog/enrollments",
        data={"student_id": student_id, "class_id": class_id, "start_date": "2026-09-01"},
    )

    created = client.post(
        "/sessions",
        data={
            "class_id": class_id,
            "teacher_id": teacher_id,
            "session_type": "daily",
            "session_date": "2026-09-05",
            "start_time": "16:30",
        },
        follow_redirects=False,
    )
    workspace_url = created.headers["location"]

    saved = client.post(
        f"{workspace_url}/daily/{student_id}",
        data={
            "rating_knowledge": "4",
            "rating_habit": "4",
            "rating_mindset": "5",
            "progress_indicators": ["K001", "H003", "M002"],
            "note": "主动性明显提升",
        },
        follow_redirects=True,
    )
    assert "1 / 1 已完成" in saved.text
    assert "主动性明显提升" in client.get(f"/history?student_id={student_id}").text
    assert client.get(f"/export.xlsx?student_id={student_id}").status_code == 200
