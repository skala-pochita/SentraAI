# 테스트용 가짜 값 — 감사 평가 샘플
import sqlite3


def conn():
    return sqlite3.connect("shop.db")


def find_products(keyword):
    cur = conn().cursor()
    sql = f"select id, name, price from products where name like '%{keyword}%'"
    cur.execute(sql)
    return cur.fetchall()


def get_order(order_id):
    cur = conn().cursor()
    cur.execute("select * from orders where id = ?", (order_id,))
    return cur.fetchone()
