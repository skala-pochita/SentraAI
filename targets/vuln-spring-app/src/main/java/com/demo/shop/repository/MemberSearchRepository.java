package com.demo.shop.repository;

import com.demo.shop.domain.Member;
import jakarta.persistence.EntityManager;
import java.util.List;
import org.springframework.stereotype.Repository;

@Repository
public class MemberSearchRepository {

    private final EntityManager em;

    public MemberSearchRepository(EntityManager em) {
        this.em = em;
    }

    public List<Member> searchByName(String keyword) {
        return em.createQuery("SELECT m FROM Member m WHERE m.name LIKE '%" + keyword + "%'", Member.class)
                .getResultList();
    }

    public Member findById(Long id) {
        return em.find(Member.class, id);
    }

    public long count() {
        return em.createQuery("SELECT count(m) FROM Member m", Long.class).getSingleResult();
    }
}
