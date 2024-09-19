package org.ktronics;

import com.microsoft.azure.functions.annotation.*;
import com.microsoft.azure.functions.*;
import org.ktronics.models.Credential;
import org.ktronics.services.CredentialService;
import org.ktronics.services.PowerCheckService;

import java.util.List;

/**
 * Azure Functions with Timer trigger.
 */
public class TimerTriggerJava {
    /**
     * This function will be invoked periodically according to the specified schedule.
     */
    @FunctionName("powerPlantMonitor")
    public void run(
            @TimerTrigger(name = "authTokenTrigger", schedule = "0 0 0 * * *") String timerInfo,
            final ExecutionContext context
    ) {
        context.getLogger().info("Azure Function triggered: " + timerInfo);

        // Initialize services
        CredentialService credentialService = new CredentialService();
        PowerCheckService powerCheckService = new PowerCheckService();

        // Step 1: Get the list of credentials from the database
        List<Credential> credentials = credentialService.getCredentials();

        // Step 2: Process each credential if it's for ShineMonitor
        credentials.forEach(credential -> {
            if ("ShineMonitor".equals(credential.getType())) {
                // Step 3: Check power output and perform the required actions
                powerCheckService.checkPowerStationsForUser(credential, context);
            }
        });
    }
}
