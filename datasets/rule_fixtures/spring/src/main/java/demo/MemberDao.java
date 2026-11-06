package demo;

public class MemberDao {
    public List<Member> byName(String name) {
        String sql = "select * from member where name = '" + name + "'";
        return jdbcTemplate.query(sql, mapper);
    }

    public List<Member> byStatus(String status) {
        String q = String.format("select * from member where status = '%s'", status);
        return jdbcTemplate.query(q, mapper);
    }

    public List<Member> sorted(String col) {
        StringBuilder sb = new StringBuilder("select * from member");
        sb.append(" order by ").append(col);
        return jdbcTemplate.query(sb.toString(), mapper);
    }

    public Object raw(String nativeSql) {
        return em.createNativeQuery(nativeSql).getResultList();
    }

    // 음성: 상수 쿼리 + 바인딩
    public List<Member> safe(String name) {
        String sql = "select * from member where name = ?";
        return jdbcTemplate.query(sql, mapper, name);
    }

    public Object safeNative() {
        return em.createNativeQuery("select count(*) from member").getSingleResult();
    }
}
