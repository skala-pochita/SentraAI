# 테스트용 가짜 값 — 감사 평가 샘플
import base64
import hashlib
import pickle

import yaml


def load_cart(cookie_value):
    return pickle.loads(base64.b64decode(cookie_value))


def load_settings(text):
    return yaml.load(text)


def load_safe_settings(text):
    return yaml.safe_load(text)


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def etag(body):
    return hashlib.md5(body).hexdigest()
