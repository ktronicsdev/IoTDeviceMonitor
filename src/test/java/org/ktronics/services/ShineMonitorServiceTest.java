package org.ktronics.services;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.ktronics.utils.SignatureUtil;
import org.mockito.MockedStatic;
import org.mockito.Mockito;
import com.microsoft.azure.functions.ExecutionContext;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

public class ShineMonitorServiceTest {

    private ShineMonitorService shineMonitorService;
    private HttpURLConnection mockConnection;

    @BeforeEach
    public void setup() throws Exception {
        shineMonitorService = new ShineMonitorService();
        mockConnection = mock(HttpURLConnection.class);
    }

    @Test
    public void testGetShineMonitorToken_Success() throws Exception {
        String expectedToken = "mockToken";
        String username = "testUser";
        String password = "testPassword";

        // Mock the signature generation
        try (MockedStatic<SignatureUtil> signatureUtilMock = mockStatic(SignatureUtil.class)) {
            signatureUtilMock.when(() -> SignatureUtil.generateSignature(anyString(), anyString(), anyString(), anyString()))
                    .thenReturn("mockSignature");

            // Mock URL.openConnection() behavior
            try (MockedStatic<URL> urlMock = mockStatic(URL.class)) {
                urlMock.when(() -> new URL(anyString())).thenReturn(mockConnection);
                when(mockConnection.getInputStream()).thenReturn(new java.io.ByteArrayInputStream(("{\"err\":0, \"token\":\"" + expectedToken + "\"}".getBytes(StandardCharsets.UTF_8)).getBytes()));

                // Execute the service method
                String actualToken = shineMonitorService.getShineMonitorToken(username, password);

                // Verify and assert the token is correct
                assertEquals(expectedToken, actualToken);
            }
        }
    }

    @Test
    public void testGetShineMonitorToken_Failure() throws Exception {
        String username = "testUser";
        String password = "testPassword";

        // Mock signature generation
        MockedStatic<SignatureUtil> signatureUtilMock = mockStatic(SignatureUtil.class);
        signatureUtilMock.when(() -> SignatureUtil.generateSignature(anyString(), anyString(), anyString(), anyString()))
                .thenReturn("mockSignature");

        // Mock failure response
        when(mockConnection.getInputStream()).thenReturn(new java.io.ByteArrayInputStream("{\"err\":1}".getBytes()));
        mockHttpConnection(mockConnection);

        // Execute the method
        String actualToken = shineMonitorService.getShineMonitorToken(username, password);

        // Assert the token is null on failure
        assertNull(actualToken);

        signatureUtilMock.close();
    }

    @Test
    public void testGetPowerStations_Success() throws Exception {
        String expectedResponse = "{\"err\":0, \"plants\":[]}";

        // Mock signature generation
        MockedStatic<SignatureUtil> signatureUtilMock = mockStatic(SignatureUtil.class);
        signatureUtilMock.when(() -> SignatureUtil.generateSignature(anyString(), anyString(), anyString(), anyString()))
                .thenReturn("mockSignature");

        // Mock the HTTP response
        when(mockConnection.getInputStream()).thenReturn(new java.io.ByteArrayInputStream(expectedResponse.getBytes()));
        mockHttpConnection(mockConnection);

        // Call the service method
        String token = "mockToken";
        String actualResponse = shineMonitorService.getPowerStations(token);

        // Verify the response
        assertEquals(expectedResponse, actualResponse);

        signatureUtilMock.close();
    }

    @Test
    public void testGetPowerOutputForPlant() throws Exception {
        String token = "mockToken";
        String plantId = "mockPlantId";
        ExecutionContext mockContext = mock(ExecutionContext.class);

        // Mock the signature generation
        MockedStatic<SignatureUtil> signatureUtilMock = mockStatic(SignatureUtil.class);
        signatureUtilMock.when(() -> SignatureUtil.generateSignature(anyString(), anyString(), anyString(), anyString()))
                .thenReturn("mockSignature");

        // Mock API response for daily output
        String dailyOutputResponse = "{\"err\":0, \"dat\":{\"energy\":100}}";
        when(mockConnection.getInputStream()).thenReturn(new java.io.ByteArrayInputStream(dailyOutputResponse.getBytes()));
        mockHttpConnection(mockConnection);

        // Execute the method
        int totalOutput = shineMonitorService.getPowerOutputForPlant(token, plantId, mockContext);

        // Assert that the total output for 3 days is calculated correctly
        assertEquals(300, totalOutput);

        signatureUtilMock.close();
    }

    // Helper method to mock HttpURLConnection behavior
    private void mockHttpConnection(HttpURLConnection connection) throws Exception {
        URL mockURL = mock(URL.class);
        when(mockURL.openConnection()).thenReturn(connection);
        MockedStatic<URL> urlMock = mockStatic(URL.class);
        Mockito.when(new URL(anyString()).openConnection()).thenReturn(mockConnection);
    }
}