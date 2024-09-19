package org.ktronics.services;

import com.microsoft.azure.functions.ExecutionContext;
import org.json.JSONArray;
import org.json.JSONObject;
import org.ktronics.utils.SignatureUtil;

import java.net.HttpURLConnection;
import java.net.URL;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;

public class ShineMonitorService {

    private static final String API_URL = "http://api.shinemonitor.com/public/";

    // Method to get the API token from ShineMonitor
    public String getShineMonitorToken(String username, String password) throws Exception {
        String companyKey = "0123456789ABCDEF"; // Replace with actual company key
        String salt = String.valueOf(System.currentTimeMillis());
        String sign = SignatureUtil.generateSignature(salt, password, companyKey, username);
        String requestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&action=auth&usr=" + username + "&company-key=" + companyKey;

        URL url = new URL(requestURL);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        String response = in.readLine();
        in.close();

        // Parse JSON response to get the token
        if (response.contains("\"err\":0")) {
            return response.split("\"token\":\"")[1].split("\"")[0];
        }
        return null;
    }

    // Method to get the list of power stations for the user
    public String getPowerStations(String token) throws Exception {
        String salt = String.valueOf(System.currentTimeMillis());
        String action = "queryPlants";
        String sign = SignatureUtil.generateSignature(salt, "your_secret_here", token, action); // Replace with actual secret

        String plantRequestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&token=" + token + "&action=queryPlants&page=0&pagesize=10";
        URL url = new URL(plantRequestURL);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        String response = in.readLine();
        in.close();

        return response;
    }

    // Function to get power output for the last 3 days for a plant
    public int getPowerOutputForPlant(String token, String plantId, ExecutionContext context) throws Exception {
        int totalOutput = 0;

        // Get the current date and iterate through the last 3 days
        LocalDate today = LocalDate.now();
        DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd");

        for (int i = 0; i < 3; i++) {
            LocalDate date = today.minusDays(i);
            String formattedDate = date.format(formatter);
            int dailyOutput = getPowerOutputForDay(token, plantId, formattedDate, context);
            totalOutput += dailyOutput;
        }

        return totalOutput;
    }

    // Helper method to get power output for a specific day
    private int getPowerOutputForDay(String token, String plantId, String date, ExecutionContext context) throws Exception {
        int dailyOutput = 0;
        String salt = String.valueOf(System.currentTimeMillis());
        String action = "queryPlantEnergyDay";
        String sign = SignatureUtil.generateSignature(salt, "your_secret_here", token, action); // Replace with actual secret

        String powerRequestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&token=" + token + "&action=" + action + "&plantid=" + plantId + "&date=" + date;
        URL url = new URL(powerRequestURL);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        String response = in.readLine();
        in.close();

        // Parse the response to extract power output for the specific day
        JSONObject jsonResponse = new JSONObject(response);
        if (jsonResponse.getInt("err") == 0) {
            dailyOutput = jsonResponse.getJSONObject("dat").getInt("energy"); // Assuming 'energy' field holds the daily output
        } else {
            context.getLogger().warning("Error in API response for date " + date + ": " + jsonResponse.getString("desc"));
        }

        return dailyOutput;
    }

    // Helper method to parse the power output from the JSON response
    private int parsePowerOutput(String response) throws Exception {
        int totalOutput = 0;

        try {
            JSONObject jsonResponse = new JSONObject(response);
            if (jsonResponse.getInt("err") == 0) {
                JSONArray dailyOutputs = jsonResponse.getJSONObject("dat").getJSONArray("days");
                for (int i = 0; i < dailyOutputs.length(); i++) {
                    JSONObject dayData = dailyOutputs.getJSONObject(i);
                    double dailyOutput = dayData.getDouble("energy"); // Assume 'energy' is in kW
                    totalOutput += dailyOutput;
                }
            } else {
                throw new Exception("Error in API response: " + jsonResponse.getString("desc"));
            }
        } catch (Exception e) {
            throw new Exception("Error parsing power output response: " + e.getMessage(), e);
        }

        return totalOutput;
    }
}
