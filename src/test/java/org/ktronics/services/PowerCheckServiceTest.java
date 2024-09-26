package org.ktronics.services;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.ktronics.models.Credential;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class PowerCheckServiceIntegrationTest {

    private PowerCheckService powerCheckService;
    private Credential credential;

    @BeforeEach
    void setup() {
        powerCheckService = new PowerCheckService();
        credential = new Credential("be2fc4f6-439f-44c3-b663-79a39ea21798", "0777707188", "T98765432", "ShineMonitor");
    }

    @Test
    void shouldCheckPowerStationsAndUpdateDatabaseCorrectly() {

        List<String> result = powerCheckService.checkPowerStationsForUser(credential);

        assertNotNull(result, "Result should not be null");
        assertTrue(result.size() > 0, "Expected some power plants to be processed");

        result.forEach(System.out::println);
    }
}
