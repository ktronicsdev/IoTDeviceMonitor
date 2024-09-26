package org.ktronics.services;

import com.azure.cosmos.*;
import com.azure.cosmos.models.*;
import com.azure.cosmos.util.CosmosPagedIterable;
import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class DatabaseService {

    private final String endpoint = System.getenv("COSMOS_ENDPOINT");
    private final String key = System.getenv("COSMOS_KEY");
    private final CosmosClient client;
    private final CosmosContainer credentialsContainer;
    private final CosmosContainer powerPlantsContainer;

    public DatabaseService() {
        CosmosClientBuilder clientBuilder = new CosmosClientBuilder()
                .endpoint(endpoint)
                .key(key)
                .consistencyLevel(ConsistencyLevel.EVENTUAL);

        client = clientBuilder.buildClient();

        CosmosDatabase database = client.getDatabase("iot-device-monitor");
        credentialsContainer = database.getContainer("Credentials");
        powerPlantsContainer = database.getContainer("PowerPlants");
    }

    public List<Credential> getCredentials() {
        System.out.println("Fetching credentials from database");
        var credentials = new ArrayList<Credential>();
        var sqlQuery = "SELECT * FROM c";
        var queryOptions = new CosmosQueryRequestOptions();

        CosmosPagedIterable<Credential> items = credentialsContainer.queryItems(sqlQuery, queryOptions, Credential.class);
        System.out.println("Items" + items.toString());
        items.forEach(credentials::add);

        return credentials;
    }

    public void updatePowerPlantStatus(PowerPlant powerPlant) {
        var partitionKey = new PartitionKey(powerPlant.getCredentialId());

        // Check if the power plant exists based on powerPlantId
        PowerPlant existing = getPowerPlantByPowerPlantId(powerPlant.getPowerPlantId(), partitionKey);

        if (existing != null) {
            // Update existing power plant information
            existing.setIsAbnormal(powerPlant.getIsAbnormal());
            existing.setAbnormalPercentage(powerPlant.getAbnormalPercentage());
            existing.setIsMailSent(powerPlant.getIsMailSent());

            powerPlantsContainer.replaceItem(existing, existing.getId(), partitionKey, new CosmosItemRequestOptions());
        } else {
            // Create new power plant if it doesn't exist
            powerPlantsContainer.createItem(powerPlant, partitionKey, new CosmosItemRequestOptions());
        }
    }

    private PowerPlant getPowerPlantByPowerPlantId(String powerPlantId, PartitionKey partitionKey) {
        String query = "SELECT * FROM c WHERE c.powerPlantId = @powerPlantId";
        SqlParameter powerPlantParam = new SqlParameter("@powerPlantId", powerPlantId);
        SqlQuerySpec querySpec = new SqlQuerySpec(query, Collections.singletonList(powerPlantParam));

        CosmosPagedIterable<PowerPlant> iterable = powerPlantsContainer.queryItems(querySpec, new CosmosQueryRequestOptions(), PowerPlant.class);
        for (PowerPlant plant : iterable) {
            return plant;
        }
        return null;
    }
}
