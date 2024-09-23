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
            @TimerTrigger(name = "powerPlantMonitorTimerTrigger", schedule = "%TIMER_SCHEDULE%") String timerInfo,
            final ExecutionContext context
    ) {
        context.getLogger().info("Azure Function triggered: " + timerInfo);

        DatabaseService databaseService = new DatabaseService();
        PowerCheckService powerCheckService = new PowerCheckService();

        List<Credential> credentials = databaseService.getCredentials();

        credentials.forEach(credential -> {
            if ("ShineMonitor".equals(credential.getType())) {
                var shineMonitorStatus = powerCheckService.checkPowerStationsForUser(credential);
                for (String status : shineMonitorStatus) {
                    context.getLogger().info(status);
                }
            }
        });
    }
}
