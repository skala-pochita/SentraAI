# 테스트용 가짜 값 — 감사 평가 샘플
from flask import Flask

from app.config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    from app import admin, views  # noqa: F401

    return app
