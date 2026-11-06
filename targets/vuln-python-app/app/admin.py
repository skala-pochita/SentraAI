# 테스트용 가짜 값 — 감사 평가 샘플
from flask import current_app as app

from app import db


@app.route("/admin/orders/<int:order_id>/refund", methods=["POST"])
def refund(order_id):
    order = db.get_order(order_id)
    return {"refunded": order is not None}
