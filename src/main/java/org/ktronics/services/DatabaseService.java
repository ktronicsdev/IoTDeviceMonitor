package org.ktronics.services;

import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.Updates;
import org.bson.Document;
import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;
import java.util.ArrayList;
import java.util.List;

public class DatabaseService {

    private final String connectionString = System.getenv("MONGODB_CONNECTION_URL");
    private final MongoClient mongoClient;
    private final MongoDatabase database;

    public DatabaseService() {
        mongoClient = MongoClients.create(connectionString);
        database = mongoClient.getDatabase("iot-device-monitor");
    }

    public List<Credential> getCredentials() {
        var credentials = new ArrayList<Credential>();
        var collection = database.getCollection("Credentials");

        var cursor = collection.find().iterator();
        try {
            while (cursor.hasNext()) {
                var doc = cursor.next();
                var userId = doc.getInteger("userId");
                var username = doc.getString("username");
                var password = doc.getString("password");
                var type = doc.getString("type");
                credentials.add(new Credential(userId, username, password, type));
            }
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            cursor.close();
        }
        return credentials;
    }

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
            e.printStackTrace();
        }
    }
}
