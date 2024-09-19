package org.ktronics.utils;

import java.security.MessageDigest;

public class SignatureUtil {

    // Method to generate SHA-1 signature for API requests
    public static String generateSignature(String salt, String password, String companyKey, String username) throws Exception {
        String input = salt + sha1(password) + "&action=auth&usr=" + username + "&company-key=" + companyKey;
        MessageDigest md = MessageDigest.getInstance("SHA-1");
        byte[] sha1Hash = md.digest(input.getBytes("UTF-8"));
        StringBuilder sb = new StringBuilder();
        for (byte b : sha1Hash) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    // Method to SHA-1 hash the password
    private static String sha1(String input) throws Exception {
        MessageDigest mDigest = MessageDigest.getInstance("SHA-1");
        byte[] result = mDigest.digest(input.getBytes());
        StringBuilder sb = new StringBuilder();
        for (byte b : result) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }
}