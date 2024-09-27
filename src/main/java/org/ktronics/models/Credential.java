package org.ktronics.models;

public class Credential {
    private Integer userId;
    private String username;
    private String password;
    private String type;

    public Credential(Integer userId, String username, String password, String type) {
        this.userId = userId;
        this.username = username;
        this.password = password;
        this.type = type;
    }

    public Integer getUserId() {
        return userId;
    }

    public String getUsername() {
        return username;
    }

    public String getPassword() {
        return password;
    }

    public String getType() {
        return type;
    }
}