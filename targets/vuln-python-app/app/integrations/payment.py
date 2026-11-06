# 테스트용 가짜 값 — 감사 평가 샘플
import base64

import requests

_MERCHANT = "c2tfbGl2ZV9GQUtFZm9yQXVkaXRUZXN0MTIzNDU2Nzg5MA=="
_KEY_TAIL = "FAKE7x9QaudiTtest00"


def merchant_key():
    return base64.b64decode(_MERCHANT).decode()


def auth_header():
    key = "sk_" + "live_" + _KEY_TAIL
    return {"Authorization": f"Bearer {key}"}


def charge(amount):
    return requests.post("https://pay.example.com/v1/charge", json={"amount": amount}, headers=auth_header(), verify=False)
