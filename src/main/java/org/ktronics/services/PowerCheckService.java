package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.ktronics.models.Credential;

import org.ktronics.models.PowerPlant;

public class PowerCheckService {

    private ShineMonitorService shineMonitorService = new ShineMonitorService();
    private DatabaseService databaseService = new DatabaseService();

    public void checkPowerStationsForUser(Credential credential, ExecutionContext context) {
        try {
            var authToken = shineMonitorService.getShineMonitorToken(credential.getUsername(), credential.getPassword());
            var plantIds = shineMonitorService.getPowerStations(authToken.getSecret(), authToken.getToken());

            for (String plantId : plantIds) {
                String status;
                Integer mailSent;
                var totalOutput = shineMonitorService.getPowerOutputForPlantFor3Days(authToken.getSecret(), authToken.getToken(), plantId);
                if (totalOutput < 1) {
                    PowerPlant powerPlant = new PowerPlant(credential.getUserId(), plantId, "DOWN", 0);
                    databaseService.updatePowerPlantStatus(powerPlant);
                    context.getLogger().info("Power plant (" + plantId + ") is down");
                }
                else {
                    PowerPlant powerPlant = new PowerPlant(credential.getUserId(), plantId, "UP", 0);
                    databaseService.updatePowerPlantStatus(powerPlant);
                    context.getLogger().info("Power plant (" + plantId + ") is up");
                }
            }
        }
        catch (Exception e) {
            context.getLogger().severe("Error checking Power Stations for user: " + credential.getUsername() + ". Error: " + e.getMessage());
        }
    }
}
