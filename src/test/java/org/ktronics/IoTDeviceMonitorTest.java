package org.ktronics;

import org.junit.jupiter.api.Test;

import com.microsoft.azure.functions.ExecutionContext;
import org.junit.jupiter.api.BeforeEach;
import org.ktronics.models.Credential;
import org.ktronics.services.DatabaseService;
import org.ktronics.services.PowerCheckService;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.Arrays;
import java.util.List;

import static org.mockito.Mockito.*;

/**
 * Unit tests for the TimerTriggerJava class.
 */
public class IoTDeviceMonitorTest {

    @Mock
    private DatabaseService databaseService;

    @Mock
    private PowerCheckService powerCheckService;

    @Mock
    private ExecutionContext context;

    @InjectMocks
    private IoTDeviceMonitor ioTDeviceMonitor;

    @BeforeEach
    public void setup() {
        MockitoAnnotations.openMocks(this);
    }

    @Test
    public void testRun_ShineMonitorCredentials() {
        // Arrange
        when(context.getLogger()).thenReturn(mock(java.util.logging.Logger.class));

        Credential shineMonitorCredential = new Credential(1, "user1", "token123", "ShineMonitor");
        Credential otherCredential = new Credential(2, "user2", "token456", "OtherService");
        List<Credential> credentials = Arrays.asList(shineMonitorCredential, otherCredential);

        when(databaseService.getCredentials()).thenReturn(credentials);

        // Act
        ioTDeviceMonitor.run("0 0 0 * * *", context);

        // Assert
        verify(databaseService, times(1)).getCredentials();
        verify(powerCheckService, times(1)).checkPowerStationsForUser(shineMonitorCredential, context);
        verify(powerCheckService, times(0)).checkPowerStationsForUser(otherCredential, context);
    }

    @Test
    public void testRun_NoShineMonitorCredentials() {
        // Arrange
        when(context.getLogger()).thenReturn(mock(java.util.logging.Logger.class));

        Credential otherCredential = new Credential(1, "user2", "token456", "OtherService");
        List<Credential> credentials = Arrays.asList(otherCredential);

        when(databaseService.getCredentials()).thenReturn(credentials);

        // Act
        ioTDeviceMonitor.run("0 0 0 * * *", context);

        // Assert
        verify(databaseService, times(1)).getCredentials();
        verify(powerCheckService, times(0)).checkPowerStationsForUser(any(Credential.class), eq(context));
    }
}