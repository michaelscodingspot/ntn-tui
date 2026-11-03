from notion_tui.links import Link, notion_id, notion_links_to_markdown, page_links, same_file


def test_notion_tags_become_markdown_links():
    md = '<page url="https://app.notion.com/p/abc">Tailscale [home] setup</page>\n<database url="https://x/p/2" inline="false" icon="🥊">Gym</database>'
    assert notion_links_to_markdown(md) == "[Tailscale \\[home\\] setup](https://app.notion.com/p/abc)\n[Gym](https://x/p/2)"


def test_empty_tag_title():
    assert notion_links_to_markdown('<page url="https://x/p/1"></page>') == "[Untitled](https://x/p/1)"


def test_page_links_in_order_once():
    md = "- [A](https://a.com) and https://b.com\n\n[A again](https://a.com)\n\n`[no](https://c.com)`"
    assert page_links(md) == [Link("A", "https://a.com"), Link("https://b.com", "https://b.com")]


def test_page_links_escaped_title():
    md = notion_links_to_markdown('<page url="https://x/p/1">x [y]</page>')
    assert page_links(md) == [Link("x [y]", "https://x/p/1")]


def test_notion_id():
    hex_id = "40d2100befea81b98df1ca772785cc9c"
    assert notion_id(f"https://app.notion.com/p/{hex_id}") == hex_id
    assert notion_id(f"https://www.notion.so/team/Some-Page-{hex_id}?v=1#x") == hex_id
    assert notion_id("https://www.notion.so/40d2100b-efea-81b9-8df1-ca772785cc9c") == hex_id
    assert notion_id(f"https://example.com/{hex_id}") is None
    assert notion_id("https://www.notion.so/pricing") is None


def test_page_images_after_links():
    md = "![](https://s3.example/a/b/image.png?sig=1)\n\n[A](https://a.com)\n\n![Chart](https://s3.example/c/d/x%20y.jpg)"
    assert page_links(md) == [
        Link("A", "https://a.com"),
        Link("Image 1 · image.png", "https://s3.example/a/b/image.png?sig=1", image=True),
        Link("Image 2 · Chart", "https://s3.example/c/d/x%20y.jpg", image=True),
    ]


def test_same_file_ignores_signature():
    assert same_file("https://s3.example/a/image.png?sig=1", "https://s3.example/a/image.png?sig=2")
    assert not same_file("https://s3.example/a/image.png?sig=1", "https://s3.example/b/image.png?sig=1")
