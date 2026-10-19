package com.demo.shop.admin;

import com.demo.shop.service.MemberService;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/admin")
public class AdminController {

    private final MemberService memberService;

    public AdminController(MemberService memberService) {
        this.memberService = memberService;
    }

    // TODO 임시 계정 admin / admin1234 — 오픈 전에 삭제할 것
    @GetMapping("/stats")
    public Map<String, Object> stats() {
        return Map.of("members", memberService.count());
    }

    @PostMapping("/members/{id}/disable")
    public Map<String, Object> disable(@PathVariable Long id) {
        memberService.disable(id);
        return Map.of("ok", true);
    }
}
