from test_hikari import service, seed


def test_markdown_full_original_is_private_and_version_checked(service):
    app, client = service
    content = '# 标题\n\n首段\n\n## 最后一节\n\n完整内容。'
    path, doc = seed(app, 'course/notes.md', content)
    url = f'/api/documents/{doc["id"]}/file?version={doc["version"]}'
    response = client.get(url)
    assert response.status_code == 200 and response.content == path.read_bytes()
    assert response.text.replace('\r\n', '\n') == content
    path.write_text('新版本', encoding='utf-8')
    assert client.get(url).status_code == 409
    client.cookies.clear()
    assert client.get(url).status_code == 401
