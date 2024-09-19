package org.ktronics.utils;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class SignatureUtilTest {

    @Test
    public void testGenerateSignature() throws Exception {
        // Test values
        String salt = "randomSalt";
        String password = "testPassword";
        String companyKey = "companyKey123";
        String username = "testUser";

        // Expected SHA-1 hashed password and final signature output
        String expectedPasswordHash = "1c4b147d6ed516d8a693eff3df2fb298ddadba37";  // SHA-1 hash of "testPassword"
        String expectedSignature = "bf1ad52861f3e31258ed42d58d254d9db09c78a7";  // The expected full signature based on the test data

        // Invoke the method
        String generatedSignature = SignatureUtil.generateSignature(salt, password, companyKey, username);

        // Verify the expected result
        assertEquals(expectedSignature, generatedSignature);
    }

    @Test
    public void testSha1Hashing() throws Exception {
        // Test SHA-1 hashing of password
        String password = "testPassword";
        String expectedHash = "1c4b147d6ed516d8a693eff3df2fb298ddadba37"; // Precomputed SHA-1 hash of "testPassword"

        // Access the private method using reflection
        java.lang.reflect.Method method = SignatureUtil.class.getDeclaredMethod("sha1", String.class);
        method.setAccessible(true);

        String generatedHash = (String) method.invoke(null, password);

        // Verify the hash
        assertEquals(expectedHash, generatedHash);
    }

    @Test
    public void testGenerateSignatureWithDifferentValues() throws Exception {
        // Test with different values to ensure flexibility
        String salt = "anotherSalt";
        String password = "newPassword";
        String companyKey = "newCompanyKey";
        String username = "newUser";

        // Calculate expected signature (manually or through some trusted source)
        String expectedSignature = "71fc9a1b3ab5e4f21ac1afdab03295c77a2e9635";  // Example expected output for this test

        // Invoke the method
        String generatedSignature = SignatureUtil.generateSignature(salt, password, companyKey, username);

        // Verify the expected result
        assertEquals(expectedSignature, generatedSignature);
    }
}