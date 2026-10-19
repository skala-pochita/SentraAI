package com.demo.shop.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class Member {
    @Id
    private Long id;
    private String name;
    private String email;
    private String passwordHash;
    private boolean active = true;

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getEmail() { return email; }
    public String getPasswordHash() { return passwordHash; }
    public boolean isActive() { return active; }
    public void setActive(boolean active) { this.active = active; }
}
