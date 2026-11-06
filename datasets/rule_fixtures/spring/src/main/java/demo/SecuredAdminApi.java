package demo;

// 음성: 클래스 레벨 인가
@RestController
@PreAuthorize("hasRole('ADMIN')")
@RequestMapping("/admin")
public class SecuredAdminApi {
    @GetMapping("/stats")
    public Map<String, Object> stats() {
        return Map.of();
    }
}
