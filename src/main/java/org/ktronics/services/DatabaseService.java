package org.ktronics.services;

import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;

import java.sql.*;
import java.util.ArrayList;
import java.util.List;

public class DatabaseService {

    private String connectionString = System.getenv("JDBC_DATABASE_URL");

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
                    "USING (SELECT ? AS userId, ? AS powerPlantId) AS source " +
                    "ON (target.userId = source.userId AND target.powerPlantId = source.powerPlantId) " +
                    "WHEN MATCHED THEN " +
                    "    UPDATE SET status = ?, mailSent = ? " +
                    "WHEN NOT MATCHED THEN " +
                    "    INSERT (userId, powerPlantId, status, mailSent) " +
                    "    VALUES (?, ?, ?, ?);";

            var statement = connection.prepareStatement(query);

            statement.setInt(1, powerPlant.getUserId());
            statement.setString(2, powerPlant.getPowerPlantId());
            statement.setString(3, powerPlant.getStatus());
            statement.setInt(4, powerPlant.getMailSent());

            statement.setInt(5, powerPlant.getUserId());
            statement.setString(6, powerPlant.getPowerPlantId());
            statement.setString(7, powerPlant.getStatus());
            statement.setInt(8, powerPlant.getMailSent());

            statement.executeUpdate();
            connection.close();
        } catch (SQLException e) {
            e.printStackTrace();
        }
    }

}
