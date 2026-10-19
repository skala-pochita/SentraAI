package com.demo.shop.repository;

import com.demo.shop.domain.Order;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

@Repository
public class OrderQueryRepository {

    private static final RowMapper<Order> MAPPER = (rs, i) -> new Order(
            rs.getLong("id"), rs.getLong("customer_id"), rs.getString("item"),
            rs.getLong("amount"), rs.getString("status"));

    private final JdbcTemplate jdbcTemplate;

    public OrderQueryRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<Order> findByStatus(String status) {
        return jdbcTemplate.query("SELECT * FROM orders WHERE status = '" + status + "' ORDER BY id DESC", MAPPER);
    }

    public List<Order> findByCustomer(Long customerId) {
        return jdbcTemplate.query("SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC", MAPPER, customerId);
    }
}
