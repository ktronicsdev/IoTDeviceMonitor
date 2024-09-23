package org.ktronics.services;

import org.json.JSONArray;
import org.json.JSONObject;
import org.ktronics.models.AuthToken;
import org.ktronics.models.PowerPlant;
import org.ktronics.utils.SignatureUtil;

import java.net.HttpURLConnection;
import java.net.URL;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

public class ShineMonitorService {

    private static final String API_URL = "http://api.shinemonitor.com/public/";
    private static final String companyKey = "bnrl_frRFjEz8Mkn";

    public AuthToken getShineMonitorToken(String username, String password) throws Exception {
        var salt = System.currentTimeMillis() + "";
        var sign = SignatureUtil.generateSignatureAuth(salt, password, companyKey, username, "authEmail");
        var requestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&action=authEmail&usr=" + username + "&company-key=" + companyKey;

        var url = new URL(requestURL);
        var conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        var in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        var response = in.readLine();
        in.close();

        var jsonResponse = new JSONObject(response);

        if (jsonResponse.getInt("err") == 0) {
            var data = jsonResponse.getJSONObject("dat");

            var token = data.getString("token");
            var secret = data.getString("secret");
            var usr = data.getString("usr");
            var uid = String.valueOf(data.getInt("uid"));

            return new AuthToken(token, secret, usr, uid);
        } else {
            var errorMessage = "Error getting token for user: " + username + ". Response: " + response;
            throw new Exception(errorMessage);
        }
    }

    public List<PowerPlant> getPowerStations(String secret, String token) throws Exception {
        var salt = System.currentTimeMillis() + "";
        var action = "&action=queryPlants";
        var sign = SignatureUtil.generateSignatureQuery(salt, secret, token, action);

        var plantRequestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&token=" + token + action;

        var url = new URL(plantRequestURL);
        var conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        var in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        var response = in.readLine();
        in.close();

        var powerPlants = new ArrayList<PowerPlant>();

        try {
            var jsonResponse = new JSONObject(response);
            if (jsonResponse.getInt("err") == 0) {
                var plantsArray = jsonResponse.getJSONObject("dat").getJSONArray("plant");
                for (var i = 0; i < plantsArray.length(); i++) {
                    var plant = plantsArray.getJSONObject(i);
                    var plantId = String.valueOf(plant.getInt("pid"));
                    var plantName = plant.getString("name");
                    powerPlants.add(new PowerPlant(plantId, plantName));
                }
                return powerPlants;
            } else {
                throw new Exception("Error getting plant IDs from response: " + response);
            }
        } catch (Exception e) {
            throw new RuntimeException("Error getting plant IDs from response: " + response, e);
        }
    }

    public Double getPowerOutputForPlantFor3Days(String secret, String token, String plantId) throws Exception {
        var totalOutput = 0.0;

        var today = LocalDate.now();
        var formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd");

        for (var i = 0; i < 3; i++) {
            var date = today.minusDays(i);
            var formattedDate = date.format(formatter);
            var dailyOutput = getPowerOutputForDay(secret, token, plantId, formattedDate);
            totalOutput += dailyOutput;
        }

        return totalOutput;
    }

    public Double getPowerOutputDifferencePercentage(String secret, String token, String plantId, Integer numberOfDays) throws Exception {
        var today = LocalDate.now();
        var formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd");
        var todayOutput = getPowerOutputForDay(secret, token, plantId, today.format(formatter));
        var previousOutput = 0.0;

        for (var i = 1; i <= numberOfDays; i++) {
            var date = today.minusDays(i);
            var formattedDate = date.format(formatter);
            var dailyOutput = getPowerOutputForDay(secret, token, plantId, formattedDate);
            previousOutput += dailyOutput;
        }

        var averageOutput = previousOutput / numberOfDays;

        if (averageOutput == 0.0) {
            return todayOutput == 0.0 ? -100.0 : 100.0;
        }

        return (todayOutput - averageOutput) / averageOutput * 100;
    }

    Double getPowerOutputForDay(String secret, String token, String plantId, String date) throws Exception {
        var dailyOutput = 0.0;
        var salt = String.valueOf(System.currentTimeMillis());
        var action = "&action=queryPlantEnergyDay&plantid=" + plantId + "&date=" + date;
        var sign = SignatureUtil.generateSignatureQuery(salt, secret, token, action);

        var powerRequestURL = API_URL + "?sign=" + sign + "&salt=" + salt + "&token=" + token + action;

        var url = new URL(powerRequestURL);
        var conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");

        var in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
        var response = in.readLine();
        in.close();

        var jsonResponse = new JSONObject(response);
        if (jsonResponse.getInt("err") == 0) {
            dailyOutput = jsonResponse.getJSONObject("dat").getDouble("energy");
        } else if (jsonResponse.getInt("err") == 12) {
            dailyOutput = 0.0;
        } else {
            throw new Exception("Error getting power output for plant: " + plantId + " for date: " + date + ". Response: " + response);
        }

        return dailyOutput;
    }
}
