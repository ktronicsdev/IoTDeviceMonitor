package org.ktronics;

import org.junit.jupiter.api.Test;

import com.microsoft.azure.functions.ExecutionContext;
import org.junit.jupiter.api.BeforeEach;
import org.ktronics.models.Credential;
import org.ktronics.services.CredentialService;
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
    private CredentialService credentialService;

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

        Credential shineMonitorCredential = new Credential("ShineMonitor", "user1", "token123");
        Credential otherCredential = new Credential("OtherService", "user2", "token456");
        List<Credential> credentials = Arrays.asList(shineMonitorCredential, otherCredential);

        when(credentialService.getCredentials()).thenReturn(credentials);

        // Act
        ioTDeviceMonitor.run("0 0 0 * * *", context);

        // Assert
        verify(credentialService, times(1)).getCredentials();
        verify(powerCheckService, times(1)).checkPowerStationsForUser(shineMonitorCredential, context);
        verify(powerCheckService, times(0)).checkPowerStationsForUser(otherCredential, context);
    }

    @Test
    public void testRun_NoShineMonitorCredentials() {
        // Arrange
        when(context.getLogger()).thenReturn(mock(java.util.logging.Logger.class));

        Credential otherCredential = new Credential("OtherService", "user2", "token456");
        List<Credential> credentials = Arrays.asList(otherCredential);

        when(credentialService.getCredentials()).thenReturn(credentials);

        // Act
        ioTDeviceMonitor.run("0 0 0 * * *", context);

        // Assert
        verify(credentialService, times(1)).getCredentials();
        verify(powerCheckService, times(0)).checkPowerStationsForUser(any(Credential.class), eq(context));
    }
}