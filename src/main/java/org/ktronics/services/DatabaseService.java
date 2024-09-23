package org.ktronics.services;

import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;

import java.sql.*;
import java.util.ArrayList;
import java.util.List;

public class DatabaseService {

    private final String connectionString = System.getenv("JDBC_DATABASE_URL");

    public List<Credential> getCredentials() {
        var credentials = new ArrayList<Credential>();

        try {
            var connection = DriverManager.getConnection(connectionString);
            var statement = connection.createStatement();
            var resultSet = statement.executeQuery("SELECT userId, username, password, type FROM Credentials");

            while (resultSet.next()) {
                var userId = resultSet.getInt("userId");
                var username = resultSet.getString("username");
                var password = resultSet.getString("password");
                var type = resultSet.getString("type");
                credentials.add(new Credential(userId, username, password, type));
            }
            connection.close();
        } catch (SQLException e) {
            e.printStackTrace();
        }
        return credentials;
    }

    public void updatePowerPlantStatus(PowerPlant powerPlant) {
        try {
            var connection = DriverManager.getConnection(connectionString);

            //Using MERGE statement to update the record if the plant and user exists. Otherwise add it to the db.
            var query = "MERGE PowerPlants AS target " +
                    "USING (SELECT ? AS userId, ? AS powerPlantId, ? AS powerPlantName) AS source " +
                    "ON (target.userId = source.userId AND target.powerPlantId = source.powerPlantId) " +
                    "WHEN MATCHED THEN " +
                    "    UPDATE SET isAbnormal = ?, abnormalPercentage = ?, isMailSent = ?, powerPlantName = ? " +
                    "WHEN NOT MATCHED THEN " +
                    "    INSERT (userId, powerPlantId, isAbnormal, abnormalPercentage, isMailSent, powerPlantName) " +
                    "    VALUES (?, ?, ?, ?, ?, ?);";

            var statement = connection.prepareStatement(query);

            statement.setInt(1, powerPlant.getUserId());
            statement.setString(2, powerPlant.getPowerPlantId());
            statement.setString(3, powerPlant.getPowerPlantName());
            statement.setInt(4, powerPlant.getIsAbnormal());
            statement.setDouble(5, powerPlant.getAbnormalPercentage());
            statement.setInt(6, powerPlant.getIsMailSent());
            statement.setString(7, powerPlant.getPowerPlantName());

            statement.setInt(8, powerPlant.getUserId());
            statement.setString(9, powerPlant.getPowerPlantId());
            statement.setInt(10, powerPlant.getIsAbnormal());
            statement.setDouble(11, powerPlant.getAbnormalPercentage());
            statement.setInt(12, powerPlant.getIsMailSent());
            statement.setString(13, powerPlant.getPowerPlantName());

            statement.executeUpdate();
            connection.close();

        } catch (SQLException e) {
            e.printStackTrace();
        }
    }

}
