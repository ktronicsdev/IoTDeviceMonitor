package org.ktronics.utils;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

public class SignatureUtilTest {

    @Test
    public void testGenerateSignatureAuth() throws Exception {
        // Test values
        String salt = "1726817326099";
        String password = "Muadh@123";
        String companyKey = "bnrl_frRFjEz8Mkn";
        String username = "Muadhfazlun";

        // Expected SHA-1 hashed password and final signature output
        String expectedPasswordHash = "f9e8fdfe7e533de73085cce247a2b0ff78aef398";  // SHA-1 hash of "testPassword"
        String expectedSignature = "bca79350f4372745b3cbe9829988df2049fa66f4";  // The expected full signature based on the test data

        // Invoke the method
        String generatedSignature = SignatureUtil.generateSignatureAuth(salt, password, companyKey, username, "authEmail");

        // Verify the expected result
        assertEquals(expectedSignature, generatedSignature);

    }

    @Test
    public void testSha1HashingPlainText() throws Exception {
        // Test SHA-1 hashing of password
        String password = "T98765432";
        String expectedHash = "4d2146d69d5c9b0915d608269da157ab7599dcd4"; // Precomputed SHA-1 hash of "test"

        // Access the private method using reflection
        java.lang.reflect.Method method = SignatureUtil.class.getDeclaredMethod("sha1", String.class);
        method.setAccessible(true);

        String generatedHash = (String) method.invoke(null, password);

        // Verify the hash
        assertEquals(expectedHash, generatedHash);
    }

    @Test
    public void testSha1HashingSpecialChar() throws Exception {
        // Test SHA-1 hashing of password
        String password = "tiQri123$";
        String expectedHash = "830c5fd836a0b0ba108b9235fcbf409a89daa0e8";

        // Access the private method using reflection
        java.lang.reflect.Method method = SignatureUtil.class.getDeclaredMethod("sha1", String.class);
        method.setAccessible(true);

        String generatedHash = (String) method.invoke(null, password);

        // Verify the hash
        assertEquals(expectedHash, generatedHash);
    }
}