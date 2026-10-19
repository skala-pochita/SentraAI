package com.demo.shop.service;

import com.demo.shop.domain.Member;
import com.demo.shop.repository.MemberSearchRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MemberService {

    private final MemberSearchRepository repository;

    public MemberService(MemberSearchRepository repository) {
        this.repository = repository;
    }

    public long count() {
        return repository.count();
    }

    @Transactional
    public void disable(Long id) {
        Member m = repository.findById(id);
        if (m != null) {
            m.setActive(false);
        }
    }
}
