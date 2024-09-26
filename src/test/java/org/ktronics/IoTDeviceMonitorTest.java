package org.ktronics;

import org.junit.jupiter.api.Test;

import com.microsoft.azure.functions.ExecutionContext;
import org.ktronics.services.DatabaseService;
import org.ktronics.services.PowerCheckService;

import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

public class IoTDeviceMonitorTest {

    // Assuming you are using the real DatabaseService and PowerCheckService
    DatabaseService databaseService = new DatabaseService();
    PowerCheckService powerCheckService = new PowerCheckService();

    IoTDeviceMonitor function = new IoTDeviceMonitor();

    @Test
    public void testPowerPlantMonitor() {
        // Create a simulated execution context
        ExecutionContext context = new ExecutionContext() {
            @Override
            public Logger getLogger() {
                return Logger.getLogger(IoTDeviceMonitorTest.class.getName());
            }

            @Override
            public String getInvocationId() {
                return "test-invocation-id";
            }

            @Override
            public String getFunctionName() {
                return "powerPlantMonitor";
            }
        };

        // Simulate timerInfo (this can be any string, since timerInfo usually represents time)
        String timerInfo = "2024-09-26T19:45:00";

        // Test that the function runs without throwing any exceptions
        assertDoesNotThrow(() -> {
            function.run(timerInfo, context);
        });
    }

}