package org.ktronics.services;

import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;

import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;

public class SQLDatabaseService implements DatabaseService {

    private final String connectionString = System.getenv("JDBC_DATABASE_URL");

    @Override
    public List<Credential> getCredentials() {
        var credentials = new ArrayList<Credential>();

        var query = "SELECT userId, username, password, type FROM Credentials";

        try (var connection = DriverManager.getConnection(connectionString);
             var statement = connection.createStatement();
             var resultSet = statement.executeQuery(query)) {

            while (resultSet.next()) {
                var userId = resultSet.getInt("userId");
                var username = resultSet.getString("username");
                var password = resultSet.getString("password");
                var type = resultSet.getString("type");
                credentials.add(new Credential(userId, username, password, type));
            }

        } catch (SQLException e) {
            throw new RuntimeException("Error fetching credentials: " + e.getMessage(), e);
        }
        return credentials;
    }

    @Override
    public void updatePowerPlantStatus(PowerPlant powerPlant) {
        var query = "MERGE PowerPlants AS target " +
                "USING (SELECT ? AS userId, ? AS powerPlantId) AS source " +
                "ON (target.userId = source.userId AND target.powerPlantId = source.powerPlantId) " +
                "WHEN MATCHED THEN " +
                "    UPDATE SET isAbnormal = ?, abnormalPercentage = ?, isMailSent = ?, powerPlantName = ? " +
                "WHEN NOT MATCHED THEN " +
                "    INSERT (userId, powerPlantId, isAbnormal, abnormalPercentage, isMailSent, powerPlantName) " +
                "    VALUES (?, ?, ?, ?, ?, ?);";

        try (var connection = DriverManager.getConnection(connectionString);
             var statement = connection.prepareStatement(query)) {

            // Set parameters for the WHEN MATCHED section
            statement.setInt(1, powerPlant.getUserId());
            statement.setString(2, powerPlant.getPowerPlantId());
            statement.setInt(3, powerPlant.getIsAbnormal());
            statement.setDouble(4, powerPlant.getAbnormalPercentage());
            statement.setInt(5, powerPlant.getIsMailSent());
            statement.setString(6, powerPlant.getPowerPlantName());

            // Set parameters for the WHEN NOT MATCHED section (insert new record)
            statement.setInt(7, powerPlant.getUserId());
            statement.setString(8, powerPlant.getPowerPlantId());
            statement.setInt(9, powerPlant.getIsAbnormal());
            statement.setDouble(10, powerPlant.getAbnormalPercentage());
            statement.setInt(11, powerPlant.getIsMailSent());
            statement.setString(12, powerPlant.getPowerPlantName());

            statement.executeUpdate();

        } catch (SQLException e) {
            throw new RuntimeException("Error updating power plant status: " + e.getMessage(), e);
        }
    }
}


