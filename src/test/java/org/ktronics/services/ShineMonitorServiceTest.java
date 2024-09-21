package org.ktronics.services;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ShineMonitorServiceTest {

    //Integration Tests
    private ShineMonitorService service = new ShineMonitorService();

    @Test
    void getShineMonitorToken_Integration() throws Exception {
        var username = "Muadhfazlun";
        var password = "Muadh@123";

        var token = service.getShineMonitorToken(username, password);

        assertNotNull(token);
        assertEquals(username, token.getUsr());
    }

    @Test
    void getPowerStations_Integration() throws Exception {
        var secret = "3f977ed41562ebd04a9e8853fddabd56666ba4ad";
        var token = "41423fa31c1ad854d642c2986c3ebc02ea09962a1822ab18c4e7f25dee8d9693";

        var plantIds = service.getPowerStations(secret, token);

        assertNotNull(plantIds);
        assertTrue(plantIds.size() > 0);
    }

    @Test
    void getPowerOutputForDay_Integration() throws Exception {
        var secret = "3f977ed41562ebd04a9e8853fddabd56666ba4ad";
        var token = "41423fa31c1ad854d642c2986c3ebc02ea09962a1822ab18c4e7f25dee8d9693";
        var plantId = "1264925";
        var date = "2024-09-16";

        var powerOutput = service.getPowerOutputForDay(secret, token, plantId, date);

        System.out.println("Power Output: " + powerOutput);

        assertNotNull(powerOutput);
        assertTrue(powerOutput > 0);
    }
}
