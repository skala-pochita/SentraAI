package com.demo.shop.domain;

public record Order(Long id, Long customerId, String item, long amount, String status) {}
