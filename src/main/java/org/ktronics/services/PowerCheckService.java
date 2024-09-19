package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.ktronics.models.Credential;

import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

public class PowerCheckService {

    private ShineMonitorService shineMonitorService = new ShineMonitorService();

    // Method to check power stations for a given user credential
    public void checkPowerStationsForUser(Credential credential, ExecutionContext context) {
        try {
            // Step 1: Get API token for the user
            String token = shineMonitorService.getShineMonitorToken(credential.getUsername(), credential.getPassword());

            if (token != null) {
                // Step 2: Get the list of power stations
                String plantResponse = shineMonitorService.getPowerStations(token);
                List<String> plantIds = getPlantIdsFromResponse(plantResponse, context); // Extract plant IDs

                // Step 3: Check the power output for the last 3 days for each plant
                for (String plantId : plantIds) {
                    int totalOutput = shineMonitorService.getPowerOutputForPlant(token, plantId, context);
                    if (totalOutput < 100) {
                        context.getLogger().info("Power plant (" + plantId + ") is down");
                    }
                }
            } else {
                context.getLogger().warning("Failed to get token for user: " + credential.getUsername());
            }
        } catch (Exception e) {
            context.getLogger().severe("Error while checking power stations: " + e.getMessage());
        }
    }

    // Helper function to extract plant IDs from the JSON response
    List<String> getPlantIdsFromResponse(String response, ExecutionContext context) {
        List<String> plantIds = new ArrayList<>();

        try {
            JSONObject jsonResponse = new JSONObject(response);
            if (jsonResponse.getInt("err") == 0) {
                JSONArray plantsArray = jsonResponse.getJSONObject("dat").getJSONArray("plant");
                for (int i = 0; i < plantsArray.length(); i++) {
                    JSONObject plant = plantsArray.getJSONObject(i);
                    String plantId = plant.getString("pid"); // Extract plant ID (pid)
                    plantIds.add(plantId);
                }
            } else {
                context.getLogger().warning("Error in response: " + jsonResponse.getString("desc"));
            }
        } catch (Exception e) {
            context.getLogger().severe("Error parsing plant response: " + e.getMessage());
        }

        return plantIds;
    }
}
