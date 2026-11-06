# 테스트용 가짜 값 — 감사 평가 샘플
import os


class Config:
    DEBUG = True
    SECRET_KEY = "f8Kq2Lw9ZpX4mN7rT1vB6yH3cJ0dS5eA"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///shop.db")
    DB_PASSWORD = os.environ["DB_PASSWORD"]
    UPLOAD_DIR = "/srv/uploads"
