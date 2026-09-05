def test_pages_use_local_assets_and_have_primary_navigation(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/static/app.css"' in response.text
    assert "今日填写" in response.text
    assert "历史记录" in response.text
    assert "基础信息" in response.text
    assert "https://" not in response.text
