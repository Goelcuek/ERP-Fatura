"""Minimal i18n: UI strings are written in English and translated via a dictionary.

Turkish is the default language. Missing translations fall back to English, and
tests/test_i18n.py fails when a string in the code has no Turkish translation.
"""

from flask import g, has_request_context, session

from .translations_tr import TR

LANGUAGES = {"tr": "Türkçe", "en": "English"}
DEFAULT_LANG = "tr"


def current_lang():
    if has_request_context():
        user = getattr(g, "user", None)
        if user is not None and user.lang in LANGUAGES:
            return user.lang
        lang = session.get("lang")
        if lang in LANGUAGES:
            return lang
    return DEFAULT_LANG


def _(text, **kwargs):
    if current_lang() == "tr":
        text = TR.get(text, text)
    return text.format(**kwargs) if kwargs else text


def init_i18n(app):
    app.jinja_env.globals.update(_=_, current_lang=current_lang, LANGUAGES=LANGUAGES)
