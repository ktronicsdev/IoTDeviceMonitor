package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.json.JSONArray;
import org.json.JSONObject;
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
    public void testCheckPowerStationsForUser_Success() throws Exception {
        // Setup test data
        Credential credential = new Credential("testUser", "testPassword", "ShineMonitor");

        // Mock the behavior of ShineMonitorService
        String mockToken = "mockToken";
        String mockPowerStationsResponse = "{\"err\":0, \"dat\": {\"plant\": [{\"pid\": \"plant1\"}, {\"pid\": \"plant2\"}]}}";
        String mockPowerOutputResponse = "{\"err\":0, \"dat\": {\"energy\": 50}}"; // Less than threshold

        when(shineMonitorService.getShineMonitorToken(anyString(), anyString())).thenReturn(mockToken);
        when(shineMonitorService.getPowerStations(mockToken)).thenReturn(mockPowerStationsResponse);
        when(shineMonitorService.getPowerOutputForPlant(mockToken, "plant1", context)).thenReturn(50);
        when(shineMonitorService.getPowerOutputForPlant(mockToken, "plant2", context)).thenReturn(50);

        // Run the method to be tested
        powerCheckService.checkPowerStationsForUser(credential, context);

        // Verify interactions and log messages
        verify(shineMonitorService).getShineMonitorToken(anyString(), anyString());
        verify(shineMonitorService).getPowerStations(mockToken);
        verify(shineMonitorService).getPowerOutputForPlant(mockToken, "plant1", context);
        verify(shineMonitorService).getPowerOutputForPlant(mockToken, "plant2", context);
        verify(context, times(2)).getLogger().info(anyString()); // For each plant below threshold
    }

    @Test
    public void testCheckPowerStationsForUser_NoToken() throws Exception {
        Credential credential = new Credential("testUser", "testPassword", "ShineMonitor");

        // Mock behavior
        when(shineMonitorService.getShineMonitorToken(anyString(), anyString())).thenReturn(null);

        // Run method
        powerCheckService.checkPowerStationsForUser(credential, context);

        // Verify interactions
        verify(shineMonitorService).getShineMonitorToken(anyString(), anyString());
        verify(context).getLogger().warning("Failed to get token for user: " + credential.getUsername());
    }

    @Test
    public void testGetPlantIdsFromResponse_Success() {
        String response = "{\"err\":0, \"dat\": {\"plant\": [{\"pid\": \"plant1\"}, {\"pid\": \"plant2\"}]}}";

        List<String> plantIds = powerCheckService.getPlantIdsFromResponse(response, context);

        assertEquals(Arrays.asList("plant1", "plant2"), plantIds);
    }

    @Test
    public void testGetPlantIdsFromResponse_Error() {
        String response = "{\"err\":1, \"desc\": \"Error message\"}";

        List<String> plantIds = powerCheckService.getPlantIdsFromResponse(response, context);

        assertEquals(0, plantIds.size());
        verify(context).getLogger().warning("Error in response: Error message");
    }
}
