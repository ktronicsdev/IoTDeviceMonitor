package org.ktronics.services;

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import org.bson.Document;
import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;

import java.util.ArrayList;
import java.util.List;

public class MongoDatabaseService implements DatabaseService {

    private final MongoDatabase database;
    private final MongoClient mongoClient;

    public MongoDatabaseService() {
        String connectionString = System.getenv("MONGODB_CONNECTION_URL");
        mongoClient = MongoClients.create(connectionString);
        database = mongoClient.getDatabase("iot-device-monitor");
    }


    @Override
    public List<Credential> getCredentials() {
        var credentials = new ArrayList<Credential>();
        var collection = database.getCollection("Credentials");

        try (var cursor = collection.find().iterator()) {
            while (cursor.hasNext()) {
                var doc = cursor.next();
                var userId = doc.getInteger("userId");
                var username = doc.getString("username");
                var password = doc.getString("password");
                var type = doc.getString("type");
                if (username != null && password != null && type != null) {
                    credentials.add(new Credential(userId, username, password, type));
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("Error occurred while fetching credentials: " + e.getMessage());
        }
        return credentials;
    }

    @Override
    public void updatePowerPlantStatus(PowerPlant powerPlant) {
        try {
            var collection = database.getCollection("PowerPlants");

            var filter = Filters.and(
                    Filters.eq("userId", powerPlant.getUserId()),
                    Filters.eq("powerPlantId", powerPlant.getPowerPlantId())
            );

            var update = new Document("$set", new Document("isAbnormal", powerPlant.getIsAbnormal())
                    .append("abnormalPercentage", powerPlant.getAbnormalPercentage())
                    .append("isMailSent", powerPlant.getIsMailSent())
                    .append("powerPlantName", powerPlant.getPowerPlantName()));

            var result = collection.updateOne(filter, update);

            if (result.getMatchedCount() == 0) {
                // Insert if no matching document found
                var doc = new Document("userId", powerPlant.getUserId())
                        .append("powerPlantId", powerPlant.getPowerPlantId())
                        .append("isAbnormal", powerPlant.getIsAbnormal())
                        .append("abnormalPercentage", powerPlant.getAbnormalPercentage())
                        .append("isMailSent", powerPlant.getIsMailSent())
                        .append("powerPlantName", powerPlant.getPowerPlantName());
                collection.insertOne(doc);
            }
        } catch (Exception e) {
            throw new RuntimeException("Error occurred while updating power plant status: " + e.getMessage());
        }
    }

    }
}
