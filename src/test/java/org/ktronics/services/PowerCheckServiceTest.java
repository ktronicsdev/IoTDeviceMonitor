package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.ktronics.models.Credential;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

public class PowerCheckServiceTest {

    @InjectMocks
    private PowerCheckService powerCheckService;

    @Mock
    private ShineMonitorService shineMonitorService;

    @Mock
    private ExecutionContext context;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
    }


    @Test
    public void testCheckPowerStationsForUser_NoToken() throws Exception {
        Credential credential = new Credential(1,"testUser", "testPassword", "ShineMonitor");

        // Mock behavior
        when(shineMonitorService.getShineMonitorToken(anyString(), anyString())).thenReturn(null);

        // Run method
        powerCheckService.checkPowerStationsForUser(credential, context);

        // Verify interactions
        verify(shineMonitorService).getShineMonitorToken(anyString(), anyString());
        verify(context).getLogger().warning("Failed to get token for user: " + credential.getUsername());
    }
}
