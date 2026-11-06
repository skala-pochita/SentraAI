import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def find_user(cur, name, uid):
    cur.execute(f"select * from users where name = '{name}'")
    cur.execute("select * from users where id = %s" % uid)
    query = f"delete from sessions where user_id = {uid}"
    cur.execute(query)
    session.execute(text(f"select * from t where a = {name}"))
    logger.info("login ok password=%s", name)
    # 음성: 바인딩
    cur.execute("select * from users where name = %s", (name,))
    session.execute(text("select * from t where a = :a"), {"a": name})
