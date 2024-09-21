package org.ktronics.models;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class CredentialTest {

    private Credential credential;

    @BeforeEach
    public void setUp() {
        // Initialize the Credential object before each test
        credential = new Credential(1,"testUser", "testPass", "ShineMonitor");
    }

    @Test
    public void testConstructorInitialization() {
        // Test if the constructor correctly sets the values
        assertNotNull(credential);
        assertEquals("testUser", credential.getUsername());
        assertEquals("testPass", credential.getPassword());
        assertEquals("ShineMonitor", credential.getType());
    }

    @Test
    public void testGetUsername() {
        // Test if the getUsername method returns the correct value
        assertEquals("testUser", credential.getUsername());
    }

    @Test
    public void testGetPassword() {
        // Test if the getPassword method returns the correct value
        assertEquals("testPass", credential.getPassword());
    }

    @Test
    public void testGetType() {
        // Test if the getType method returns the correct value
        assertEquals("ShineMonitor", credential.getType());
    }
}