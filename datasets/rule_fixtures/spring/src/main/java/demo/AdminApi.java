package demo;

@RestController
@RequestMapping("/api/admin")
public class AdminApi {
    @GetMapping("/users")
    public List<User> users() {
        return repo.findAll();
    }

    @PreAuthorize("hasRole('ADMIN')")
    @DeleteMapping("/users/{id}")
    public void delete(@PathVariable Long id) {
        repo.deleteById(id);
    }
}
