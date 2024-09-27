package org.ktronics.utils;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public class SignatureUtil {

    public static String generateSignatureAuth(String salt, String password, String companyKey, String username, String action) throws Exception {
        var hashedPassword = sha1(password.trim());

        var encodedUsername = URLEncoder.encode(username, StandardCharsets.UTF_8.toString())
                .replace("+", "%2B")
                .replace("'", "%27");

        var actionString = "&action=" + action + "&usr=" + encodedUsername + "&company-key=" + companyKey;

        return sha1(salt + hashedPassword + actionString);
    }

    public static String generateSignatureQuery(String salt, String secret, String token, String action) throws Exception {
        var encodedAction = action
                .replace("#", "%23")
                .replace("'", "%27")
                .replace(" ", "%20");

        return sha1(salt + secret + token + encodedAction);
    }

    private static String sha1(String input) throws Exception {
        var mDigest = MessageDigest.getInstance("SHA-1");
        var result = mDigest.digest(input.getBytes(StandardCharsets.UTF_8));
        var sb = new StringBuilder();
        for (var b : result) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }
}
