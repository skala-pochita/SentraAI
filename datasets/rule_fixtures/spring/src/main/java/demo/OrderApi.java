package demo;

@RestController
@RequestMapping("/orders")
@CrossOrigin(origins = "*")
public class OrderApi {
    @GetMapping("/{orderId}")
    public Order get(@PathVariable Long orderId) {
        return orderRepository.findById(orderId).orElseThrow();
    }

    // 음성: 소유자 확인
    @GetMapping("/mine/{orderId}")
    public Order mine(@PathVariable Long orderId, @AuthenticationPrincipal User me) {
        Order o = orderRepository.findById(orderId).orElseThrow();
        if (!o.getOwnerId().equals(me.getId())) throw new AccessDeniedException("x");
        return o;
    }
}
