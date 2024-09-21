package org.ktronics;

import com.microsoft.azure.functions.annotation.*;
import com.microsoft.azure.functions.*;
import org.ktronics.models.Credential;
import org.ktronics.services.DatabaseService;
import org.ktronics.services.PowerCheckService;

import java.util.List;

/**
 * Azure Functions with Timer trigger.
 */
public class IoTDeviceMonitor {
    /**
     * This function will be invoked periodically according to the specified schedule.
     */
    @FunctionName("powerPlantMonitor")
    public void run(
            @TimerTrigger(name = "authTokenTrigger", schedule = "0 35 19 * * *") String timerInfo,
            final ExecutionContext context
    ) {
        context.getLogger().info("Azure Function triggered: " + timerInfo);

        DatabaseService databaseService = new DatabaseService();
        PowerCheckService powerCheckService = new PowerCheckService();

        List<Credential> credentials = databaseService.getCredentials();

        credentials.forEach(credential -> {
            if ("ShineMonitor".equals(credential.getType())) {
                powerCheckService.checkPowerStationsForUser(credential, context);
            }
        });
    }
}
