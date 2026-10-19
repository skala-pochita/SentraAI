package com.demo.shop.controller;

import com.demo.shop.domain.Order;
import com.demo.shop.repository.OrderQueryRepository;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** target-agent(cs-helper)의 get_order / refund_order 도구가 호출하는 백엔드 API. */
@RestController
public class OrderController {

    private final OrderQueryRepository orders;

    public OrderController(OrderQueryRepository orders) {
        this.orders = orders;
    }

    @GetMapping("/orders")
    public List<Order> byStatus(@RequestParam String status) {
        return orders.findByStatus(status);
    }

    @GetMapping("/customers/{customerId}/orders")
    public List<Order> byCustomer(@PathVariable Long customerId) {
        return orders.findByCustomer(customerId);
    }
}
