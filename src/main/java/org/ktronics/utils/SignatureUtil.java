package org.ktronics.utils;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public class SignatureUtil {

    public static String generateSignatureAuth(String salt, String password, String companyKey, String username, String action) throws Exception {
        String hashedPassword = sha1(password.trim());

        String encodedUsername = URLEncoder.encode(username, StandardCharsets.UTF_8.toString())
                .replace("+", "%2B")
                .replace("'", "%27");

        String actionString = "&action=" + action + "&usr=" + encodedUsername + "&company-key=" + companyKey;

        String input = salt + hashedPassword + actionString;

        return sha1(input);
    }

    public static String generateSignatureQuery(String salt, String secret, String token, String action) throws Exception {
        String encodedAction = action
                .replace("#", "%23")
                .replace("'", "%27")
                .replace(" ", "%20");

        String input = salt + secret + token + encodedAction;

        String signature = sha1(input);

        return signature;
    }

    private static String sha1(String input) throws Exception {
        MessageDigest mDigest = MessageDigest.getInstance("SHA-1");
        byte[] result = mDigest.digest(input.getBytes(StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        for (byte b : result) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }
}