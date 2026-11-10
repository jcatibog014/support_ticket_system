"""Help Articles (Markdown) -> HTML that is safe to show to everyone.

There are three walls, even though only Team Leads and Admins write articles:
1. markdown-it runs with html=False, so raw <script> stays plain text.
2. nh3 then keeps only the tags and attributes listed here, and throws out
   javascript: links. No pictures at all: one from another site could track readers.
3. The browser's Content-Security-Policy (__init__.py) refuses inline scripts.

Replies and notes never come here: they are plain text, escaped by the templates.
"""
import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])

ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "strong", "em", "del", "s", "a", "ul", "ol", "li",
    "blockquote", "pre", "code", "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}}


def to_html(markdown_text):
    raw = _markdown.render(markdown_text or "")
    return Markup(nh3.clean(raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                            url_schemes={"http", "https", "mailto"}, link_rel="noopener noreferrer nofollow"))


def init_app(app):
    app.jinja_env.filters["markdown"] = to_html
