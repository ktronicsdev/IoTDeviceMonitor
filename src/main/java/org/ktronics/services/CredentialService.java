package org.ktronics.services;

import org.ktronics.models.Credential;

import java.sql.*;
import java.util.ArrayList;
import java.util.List;

public class CredentialService {

    // Method to fetch credentials from the database
    public List<Credential> getCredentials() {
        List<Credential> credentials = new ArrayList<>();

        try {
            // Database connection - adjust the connection details as needed
            String connectionString = System.getenv("JDBC_DATABASE_URL");
            Connection connection = DriverManager.getConnection(connectionString);
            Statement statement = connection.createStatement();
            ResultSet resultSet = statement.executeQuery("SELECT username, password, type FROM Credentials");

            while (resultSet.next()) {
                String username = resultSet.getString("username");
                String password = resultSet.getString("password");
                String type = resultSet.getString("type");
                credentials.add(new Credential(username, password, type));
            }
            connection.close();
        } catch (SQLException e) {
            e.printStackTrace();
        }
        return credentials;
    }
}
