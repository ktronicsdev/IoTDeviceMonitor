package org.ktronics.models;

public final class AuthToken {
    private String token;
    private String secret;
    private String usr;
    private String uid;

    public AuthToken(String token, String secret, String usr, String uid) {
        this.token = token;
        this.secret = secret;
        this.usr = usr;
        this.uid = uid;
    }

    public String getToken() {
        return token;
    }

    public String getSecret() {
        return secret;
    }

    public String getUsr() {
        return usr;
    }

    public String getUid() {
        return uid;
    }
}
