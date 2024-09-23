package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.ktronics.models.Credential;

import org.ktronics.models.PowerPlant;

import java.util.ArrayList;
import java.util.List;

public class PowerCheckService {

    private final ShineMonitorService shineMonitorService = new ShineMonitorService();
    private final DatabaseService databaseService = new DatabaseService();
    private static final Integer numberOfDaysToCheckAvailability = System.getenv("DAYS_TO_CHECK_AVAILABILITY") != null ? Integer.parseInt(System.getenv("DAYS_TO_CHECK_AVAILABILITY")) : 3;

    public List<String> checkPowerStationsForUser(Credential credential) {
        try {
            var authToken = shineMonitorService.getShineMonitorToken(credential.getUsername(), credential.getPassword());
            var powerPlants = shineMonitorService.getPowerStations(authToken.getSecret(), authToken.getToken());
            List<String> output = new ArrayList<>();

            for (PowerPlant powerPlant : powerPlants) {
                var differencePercentage = shineMonitorService.getPowerOutputDifferencePercentage(authToken.getSecret(), authToken.getToken(), powerPlant.getPowerPlantId(), numberOfDaysToCheckAvailability);
            if (differencePercentage < -10.0) {
                powerPlant.setUserId(credential.getUserId());
                powerPlant.setIsAbnormal(1);
                powerPlant.setAbnormalPercentage(Math.abs(differencePercentage));
                powerPlant.setIsMailSent(0);
                databaseService.updatePowerPlantStatus(powerPlant);
                output.add("Power plant (" + powerPlant.getPowerPlantId() + ") is Abnormal with a " + Math.abs(differencePercentage) + "% drop in output.");
            } else {
                powerPlant.setUserId(credential.getUserId());
                powerPlant.setIsAbnormal(0);
                powerPlant.setAbnormalPercentage(0.00);
                powerPlant.setIsMailSent(0);
                databaseService.updatePowerPlantStatus(powerPlant);
                output.add("Power plant (" + powerPlant.getPowerPlantId() + ") is up.");
            }
            }

            return output;
        }
        catch (Exception e) {
            throw new RuntimeException("Error checking Power Stations for user: " + credential.getUsername() + ". Error: " + e.getMessage());
        }
    }
}
